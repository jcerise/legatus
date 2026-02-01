import asyncio
import logging

from fastapi import WebSocket

from legatus.models.agent import AgentRole, AgentStatus
from legatus.models.config import LegatusSettings
from legatus.models.messages import Message, MessageType
from legatus.models.task import Task, TaskEvent, TaskStatus
from legatus.orchestrator.services.agent_spawner import AgentSpawner
from legatus.orchestrator.services.architect_parser import (
    parse_architect_output,
)
from legatus.orchestrator.services.checkpoint_manager import (
    CheckpointManager,
)
from legatus.orchestrator.services.git_ops import GitOps
from legatus.orchestrator.services.pm_parser import parse_pm_output
from legatus.orchestrator.services.task_dispatcher import TaskDispatcher
from legatus.orchestrator.services.task_manager import TaskManager
from legatus.redis_client.client import RedisClient
from legatus.redis_client.pubsub import PubSubManager
from legatus.redis_client.state import StateStore
from legatus.redis_client.task_store import TaskStore

logger = logging.getLogger(__name__)


class EventBus:
    """Listens on Redis pub/sub channels and dispatches events."""

    def __init__(
        self,
        task_store: TaskStore,
        state_store: StateStore,
        pubsub: PubSubManager,
        workspace_path: str,
        spawner: AgentSpawner,
        redis_client: RedisClient,
        settings: LegatusSettings | None = None,
    ):
        self.task_manager = TaskManager(task_store)
        self.task_store = task_store
        self.state_store = state_store
        self.pubsub = pubsub
        self.git_ops = GitOps(workspace_path)
        self.spawner = spawner
        self.settings = settings
        self.checkpoint_manager = CheckpointManager(
            redis_client, task_store,
        )
        self.dispatcher = TaskDispatcher(
            task_store, state_store, spawner,
        )
        self.ws_connections: list[WebSocket] = []
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Begin listening for agent events."""
        logger.info(
            "EventBus started, listening on %s",
            PubSubManager.CHANNEL_AGENT,
        )
        try:
            async for message in self.pubsub.listen(
                PubSubManager.CHANNEL_AGENT,
            ):
                await self._handle_agent_message(message)
        except asyncio.CancelledError:
            logger.info("EventBus shutting down")
        except Exception:
            logger.exception("EventBus error")

    async def _handle_agent_message(self, msg: Message) -> None:
        logger.info(
            "Received event: type=%s task=%s agent=%s",
            msg.type, msg.task_id, msg.agent_id,
        )

        await self.state_store.append_log(msg.model_dump(mode="json"))

        # Update agent status on first sign of life
        if msg.agent_id:
            await self._update_agent_status(msg)

        match msg.type:
            case MessageType.TASK_COMPLETE:
                await self._on_task_complete(msg)
            case MessageType.TASK_FAILED:
                await self._on_task_failed(msg)
            case MessageType.LOG_ENTRY:
                pass  # Already logged above
            case _:
                logger.warning("Unhandled message type: %s", msg.type)

        await self._broadcast_to_ws(msg)

    async def _on_task_complete(self, msg: Message) -> None:
        if not msg.task_id or not msg.agent_id:
            return

        # Determine the agent's role
        agent_info = await self.state_store.get_agent_info(
            msg.agent_id,
        )
        agent_role = (
            agent_info.role if agent_info else AgentRole.DEV
        )

        match agent_role:
            case AgentRole.PM:
                await self._on_pm_complete(msg)
            case AgentRole.ARCHITECT:
                await self._on_architect_complete(msg)
            case _:
                await self._on_dev_complete(msg)

        # Clean up agent container regardless of role
        await self._cleanup_agent(msg.agent_id)

    async def _on_pm_complete(self, msg: Message) -> None:
        """Handle PM agent completion: parse plan, create sub-tasks,
        create checkpoint for user approval."""
        task_id = msg.task_id
        output = msg.data.get("output", "")

        plan = parse_pm_output(output)

        if plan is None or len(plan.subtasks) == 0:
            logger.error(
                "PM agent produced no valid plan for task %s",
                task_id,
            )
            await self.task_manager.on_task_failed(
                task_id,
                "PM agent failed to produce a valid"
                " decomposition plan",
            )
            return

        parent = await self.task_store.get(task_id)
        if parent is None:
            return

        # Store raw PM output for downstream agents (architect)
        parent.agent_outputs["pm"] = output
        await self.task_store.update(parent)

        # Create child tasks in order, chaining depends_on
        child_ids: list[str] = []
        prev_child_id: str | None = None
        for i, st in enumerate(plan.subtasks):
            child = Task(
                title=st.title,
                description=st.description,
                prompt=st.description,
                type=parent.type,
                project=parent.project,
                parent_id=task_id,
                acceptance_criteria=st.acceptance_criteria,
                priority=parent.priority,
                depends_on=(
                    [prev_child_id] if prev_child_id else []
                ),
                created_by="pm_agent",
                history=[TaskEvent(
                    event="created",
                    by="pm_agent",
                    detail=(
                        f"sub-task {i + 1}/{len(plan.subtasks)}"
                    ),
                )],
            )
            child = await self.task_store.create(child)
            # Transition child CREATED -> PLANNED
            await self.task_store.update_status(
                child.id, TaskStatus.PLANNED,
                event_by="pm_agent",
                event_detail="planned by PM",
            )
            child_ids.append(child.id)
            prev_child_id = child.id

        # Update parent with child IDs
        parent.subtask_ids = child_ids
        await self.task_store.update(parent)

        # Build checkpoint description
        desc_lines = [
            f"PM decomposed '{parent.title}' into"
            f" {len(child_ids)} sub-tasks:",
            "",
        ]
        if plan.analysis:
            desc_lines.append(f"**Analysis**: {plan.analysis}")
            desc_lines.append("")

        for i, st in enumerate(plan.subtasks):
            desc_lines.append(
                f"{i + 1}. **{st.title}**"
                f" ({st.estimated_complexity})"
            )
            desc_lines.append(f"   {st.description[:200]}")
            for ac in st.acceptance_criteria:
                desc_lines.append(f"   - {ac}")
            desc_lines.append("")

        await self.checkpoint_manager.create(
            task_id=task_id,
            title=f"Approve plan: {parent.title}",
            description="\n".join(desc_lines),
            source_role="pm",
        )

        logger.info(
            "PM plan for task %s: %d sub-tasks, checkpoint created",
            task_id, len(child_ids),
        )

    async def _on_architect_complete(self, msg: Message) -> None:
        """Handle Architect agent completion: parse design,
        store output, create checkpoint for user approval."""
        task_id = msg.task_id
        output = msg.data.get("output", "")

        task = await self.task_store.get(task_id)
        if task is None:
            return

        # Store raw architect output on the task
        task.agent_outputs["architect"] = output
        await self.task_store.update(task)

        # Parse output (best-effort — architect is advisory)
        plan = parse_architect_output(output)

        # Build checkpoint description
        desc_lines = [
            f"Architect reviewed '{task.title}':",
            "",
        ]

        if plan:
            if plan.decisions:
                desc_lines.append("**Design Decisions:**")
                for d in plan.decisions:
                    title = d.get("title", "Untitled")
                    rationale = d.get("rationale", "")
                    desc_lines.append(
                        f"- **{title}**: {rationale[:200]}"
                    )
                desc_lines.append("")

            if plan.interfaces:
                desc_lines.append("**Interfaces:**")
                for iface in plan.interfaces:
                    module = iface.get("module", "?")
                    defn = iface.get(
                        "definition", "",
                    )[:200]
                    desc_lines.append(
                        f"- **{module}**: {defn}"
                    )
                desc_lines.append("")

            if plan.concerns:
                desc_lines.append("**Concerns:**")
                for c in plan.concerns:
                    desc_lines.append(f"- {c}")
                desc_lines.append("")

            if plan.design_notes:
                desc_lines.append("**Notes:**")
                desc_lines.append(plan.design_notes[:500])
                desc_lines.append("")
        else:
            desc_lines.append(
                "*(Architect output could not be parsed"
                " as structured JSON. Raw output stored"
                " on task.)*"
            )
            desc_lines.append("")

        await self.checkpoint_manager.create(
            task_id=task_id,
            title=f"Approve design: {task.title}",
            description="\n".join(desc_lines),
            source_role="architect",
        )

        logger.info(
            "Architect design for task %s: checkpoint created",
            task_id,
        )

    async def _on_dev_complete(self, msg: Message) -> None:
        """Handle dev agent completion: mark done, git commit,
        and dispatch next sub-task if applicable."""
        task_id = msg.task_id
        task = await self.task_manager.on_task_complete(
            task_id, msg.data,
        )

        # Git commit (best-effort — don't block dispatch)
        try:
            commit_hash = self.git_ops.commit_changes(
                f"legatus: {task.title} ({task.id})"
            )
            if commit_hash:
                logger.info("Git commit: %s", commit_hash)
        except Exception:
            logger.exception(
                "Git commit failed for task %s", task_id,
            )

        # If this is a sub-task, dispatch the next one
        if task.parent_id:
            await self.dispatcher.on_subtask_complete(
                task.parent_id,
            )

    async def _on_task_failed(self, msg: Message) -> None:
        if not msg.task_id:
            return

        error = msg.data.get("error", "Unknown error")
        await self.task_manager.on_task_failed(msg.task_id, error)

        # If this was a sub-task, fail the parent too
        task = await self.task_store.get(msg.task_id)
        if task and task.parent_id:
            parent = await self.task_store.get(task.parent_id)
            if parent and parent.status == TaskStatus.ACTIVE:
                await self.task_store.update_status(
                    parent.id, TaskStatus.REVIEW,
                    event_by="orchestrator",
                    event_detail=(
                        f"sub-task {task.id} failed: {error}"
                    ),
                )
                await self.task_store.update_status(
                    parent.id, TaskStatus.REJECTED,
                    event_by="orchestrator",
                    event_detail=f"sub-task failure: {error}",
                )

        if msg.agent_id:
            await self._cleanup_agent(msg.agent_id)

    async def on_checkpoint_approved(
        self,
        task_id: str,
        source_role: str | None = None,
    ) -> None:
        """Hook called when a checkpoint is approved.

        Routes based on source_role:
        - PM checkpoint + architect enabled → spawn Architect
        - Otherwise → dispatch sub-tasks
        """
        task = await self.task_store.get(task_id)
        if not task or not task.subtask_ids:
            return

        architect_enabled = (
            self.settings.agent.architect_review
            if self.settings
            else True
        )

        if source_role == "pm" and architect_enabled:
            # Spawn architect agent to review the plan
            try:
                agent_info = self.spawner.spawn_agent(
                    task, AgentRole.ARCHITECT,
                )
                await self.state_store.set_agent_info(agent_info)
                logger.info(
                    "Spawned architect agent %s for task %s",
                    agent_info.id, task_id,
                )
            except Exception:
                logger.exception(
                    "Failed to spawn architect for task %s,"
                    " proceeding to dispatch",
                    task_id,
                )
                await self.dispatcher.dispatch_next(task_id)
        else:
            # Architect already approved, or disabled — dispatch
            await self.dispatcher.dispatch_next(task_id)

    async def on_checkpoint_rejected(
        self,
        task_id: str,
        source_role: str | None = None,
    ) -> None:
        """Hook called when a checkpoint is rejected.

        Cleans up sub-tasks and fails the parent.
        """
        task = await self.task_store.get(task_id)
        if task and task.subtask_ids:
            await self.dispatcher.cleanup_subtasks(task_id)

        detail = (
            "design rejected by user"
            if source_role == "architect"
            else "plan rejected by user"
        )

        # Transition parent ACTIVE -> REVIEW -> REJECTED
        if task and task.status == TaskStatus.ACTIVE:
            await self.task_store.update_status(
                task_id, TaskStatus.REVIEW,
                event_by="user",
                event_detail=detail,
            )
            await self.task_store.update_status(
                task_id, TaskStatus.REJECTED,
                event_by="user",
                event_detail=detail,
            )

    async def _update_agent_status(self, msg: Message) -> None:
        """Update agent status based on incoming events."""
        agent = await self.state_store.get_agent_info(msg.agent_id)
        if agent is None:
            return

        if msg.type in (
            MessageType.TASK_COMPLETE, MessageType.TASK_FAILED,
        ):
            new_status = AgentStatus.STOPPING
        elif agent.status == AgentStatus.STARTING:
            new_status = AgentStatus.ACTIVE
        else:
            return

        agent.status = new_status
        await self.state_store.set_agent_info(agent)

    async def _cleanup_agent(self, agent_id: str) -> None:
        """Remove agent state and clean up its Docker container."""
        agent_info = await self.state_store.get_agent_info(agent_id)
        if agent_info and agent_info.container_id:
            logs = self.spawner.collect_logs_and_remove(
                agent_info.container_id,
            )
            if logs:
                logger.debug(
                    "Agent %s container logs:\n%s",
                    agent_id, logs[-2000:],
                )
        await self.state_store.remove_agent(agent_id)

    async def _broadcast_to_ws(self, msg: Message) -> None:
        """Forward event to all connected WebSocket clients."""
        if not self.ws_connections:
            return

        data = msg.model_dump_json()
        disconnected = []
        for ws in self.ws_connections:
            try:
                await ws.send_text(data)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.ws_connections.remove(ws)
