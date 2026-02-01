import asyncio
import logging

from fastapi import WebSocket

from legatus.models.agent import AgentRole, AgentStatus
from legatus.models.config import LegatusSettings, ReviewMode
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
from legatus.orchestrator.services.reviewer_parser import (
    ReviewResult,
    parse_reviewer_output,
)
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
            case AgentRole.REVIEWER:
                await self._on_reviewer_complete(msg)
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
        """Handle dev agent completion: store output, git commit,
        and either route to reviewer or mark done."""
        task_id = msg.task_id
        output = msg.data.get("output", "")

        # Store dev output on the task
        task = await self.task_store.get(task_id)
        if task is None:
            return
        task.agent_outputs["dev"] = output
        await self.task_store.update(task)

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

        # Check if reviewer is enabled for per-subtask mode
        reviewer_enabled = (
            self.settings.agent.reviewer_enabled
            if self.settings
            else False
        )
        review_mode = (
            self.settings.agent.review_mode
            if self.settings
            else ReviewMode.PER_SUBTASK
        )

        if (
            reviewer_enabled
            and review_mode == ReviewMode.PER_SUBTASK
            and task.parent_id
        ):
            # Route to reviewer: ACTIVE → REVIEW only
            await self.task_store.update_status(
                task_id, TaskStatus.REVIEW,
                event_by="agent",
                event_detail="dev complete, awaiting review",
            )
            await self._spawn_reviewer(task)
        else:
            # Original path: ACTIVE → REVIEW → DONE
            await self.task_manager.on_task_complete(
                task_id, msg.data,
            )
            await self._handle_subtask_done(task)

    async def _spawn_reviewer(self, task: Task) -> None:
        """Spawn a reviewer agent for the given task.

        Falls back to auto-approve if spawning fails.
        """
        try:
            agent_info = self.spawner.spawn_agent(
                task, AgentRole.REVIEWER,
            )
            await self.state_store.set_agent_info(agent_info)
            logger.info(
                "Spawned reviewer agent %s for task %s",
                agent_info.id, task.id,
            )
        except Exception:
            logger.exception(
                "Failed to spawn reviewer for task %s,"
                " auto-approving",
                task.id,
            )
            # Auto-approve on spawn failure
            await self._reviewer_approve(task)

    async def _on_reviewer_complete(self, msg: Message) -> None:
        """Handle Reviewer agent completion: parse output and
        route to approve, reject, or security checkpoint."""
        task_id = msg.task_id
        output = msg.data.get("output", "")

        task = await self.task_store.get(task_id)
        if task is None:
            return

        # Store reviewer output
        task.agent_outputs["reviewer"] = output
        await self.task_store.update(task)

        review = parse_reviewer_output(output)

        # Security concerns always create a checkpoint
        if review and review.security_concerns:
            await self._reviewer_security_checkpoint(task, review)
            return

        if review is None or review.verdict == "approve":
            # Parse failure → auto-approve
            await self._reviewer_approve(task)
        else:
            await self._reviewer_reject(task, review)

    async def _reviewer_approve(self, task: Task) -> None:
        """Approve: REVIEW → DONE, then handle subtask completion."""
        await self.task_store.update_status(
            task.id, TaskStatus.DONE,
            event_by="reviewer",
            event_detail="reviewer approved",
        )
        logger.info(
            "Reviewer approved task %s", task.id,
        )
        # Re-fetch task to get updated status
        task = await self.task_store.get(task.id)
        if task:
            await self._handle_subtask_done(task)

    async def _reviewer_reject(
        self, task: Task, review: ReviewResult,
    ) -> None:
        """Reject: retry DEV or escalate to checkpoint."""
        max_retries = (
            self.settings.agent.reviewer_max_retries
            if self.settings
            else 1
        )
        retry_count = int(
            task.agent_outputs.get("reviewer_retry_count", "0")
        )

        if retry_count < max_retries:
            # Store feedback for the next DEV run
            task.agent_outputs["reviewer_feedback"] = review.summary
            task.agent_outputs["reviewer_retry_count"] = str(
                retry_count + 1
            )
            await self.task_store.update(task)

            # REVIEW → REJECTED → PLANNED, then re-dispatch
            await self.task_store.update_status(
                task.id, TaskStatus.REJECTED,
                event_by="reviewer",
                event_detail=f"rejected (retry {retry_count + 1}/"
                f"{max_retries}): {review.summary[:200]}",
            )
            await self.task_store.update_status(
                task.id, TaskStatus.PLANNED,
                event_by="orchestrator",
                event_detail="queued for retry",
            )

            # Re-fetch and re-dispatch
            task = await self.task_store.get(task.id)
            if task:
                dispatched = await self.dispatcher.dispatch_single(
                    task,
                )
                if not dispatched:
                    logger.error(
                        "Failed to re-dispatch task %s after"
                        " reviewer rejection",
                        task.id,
                    )
            logger.info(
                "Reviewer rejected task %s, retrying DEV"
                " (attempt %d/%d)",
                task.id if task else "?",
                retry_count + 1,
                max_retries,
            )
        else:
            # Retries exhausted — escalate to user
            desc_lines = [
                f"Reviewer rejected task '{task.title}' after"
                f" {max_retries} DEV retry(ies).",
                "",
                f"**Summary**: {review.summary}",
                "",
            ]
            if review.findings:
                desc_lines.append("**Findings:**")
                for f in review.findings:
                    desc_lines.append(
                        f"- [{f.severity}] {f.category}"
                        f" ({f.file}): {f.description}"
                    )
                desc_lines.append("")

            await self.checkpoint_manager.create(
                task_id=task.id,
                title=f"Review failed: {task.title}",
                description="\n".join(desc_lines),
                source_role="reviewer",
            )
            logger.info(
                "Reviewer rejected task %s, retries exhausted,"
                " checkpoint created",
                task.id,
            )

    async def _reviewer_security_checkpoint(
        self, task: Task, review: ReviewResult,
    ) -> None:
        """Security concerns found — always create checkpoint."""
        desc_lines = [
            f"Reviewer found security concerns in"
            f" '{task.title}':",
            "",
            f"**Verdict**: {review.verdict}",
            f"**Summary**: {review.summary}",
            "",
            "**Security Concerns:**",
        ]
        for concern in review.security_concerns:
            desc_lines.append(f"- {concern}")
        desc_lines.append("")

        if review.findings:
            desc_lines.append("**Findings:**")
            for f in review.findings:
                desc_lines.append(
                    f"- [{f.severity}] {f.category}"
                    f" ({f.file}): {f.description}"
                )
            desc_lines.append("")

        await self.checkpoint_manager.create(
            task_id=task.id,
            title=f"Security review: {task.title}",
            description="\n".join(desc_lines),
            source_role="reviewer",
        )
        logger.info(
            "Reviewer flagged security concerns for task %s,"
            " checkpoint created",
            task.id,
        )

    async def _handle_subtask_done(self, task: Task) -> None:
        """Shared helper called after a sub-task reaches DONE.

        If the task has a parent, calls the dispatcher to check
        whether more sub-tasks remain or the campaign is done.
        """
        if not task.parent_id:
            return

        result = await self.dispatcher.on_subtask_complete(
            task.parent_id,
        )
        if result == "all_done":
            await self._on_campaign_done(task.parent_id)

    async def _on_campaign_done(self, parent_id: str) -> None:
        """Called when all sub-tasks for a campaign are finished.

        If per-campaign review is enabled, spawns a reviewer on
        the parent task with aggregated dev outputs. Otherwise,
        marks the parent as done directly.
        """
        reviewer_enabled = (
            self.settings.agent.reviewer_enabled
            if self.settings
            else False
        )
        review_mode = (
            self.settings.agent.review_mode
            if self.settings
            else ReviewMode.PER_SUBTASK
        )

        parent = await self.task_store.get(parent_id)
        if parent is None:
            return

        if (
            reviewer_enabled
            and review_mode == ReviewMode.PER_CAMPAIGN
        ):
            # Aggregate child dev outputs into parent
            aggregated = []
            for child_id in parent.subtask_ids:
                child = await self.task_store.get(child_id)
                if child and child.agent_outputs.get("dev"):
                    aggregated.append(
                        f"### Sub-task: {child.title}\n"
                        f"{child.agent_outputs['dev']}"
                    )
            parent.agent_outputs["dev"] = "\n\n".join(aggregated)
            await self.task_store.update(parent)

            # Transition parent ACTIVE → REVIEW, spawn reviewer
            await self.task_store.update_status(
                parent_id, TaskStatus.REVIEW,
                event_by="orchestrator",
                event_detail="all sub-tasks done, campaign review",
            )
            await self._spawn_reviewer(parent)
        else:
            # No campaign review — complete the parent
            await self.task_store.update_status(
                parent_id, TaskStatus.REVIEW,
                event_by="orchestrator",
                event_detail="all sub-tasks completed",
            )
            await self.task_store.update_status(
                parent_id, TaskStatus.DONE,
                event_by="orchestrator",
                event_detail="all sub-tasks done",
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
        - Reviewer checkpoint → REVIEW → DONE, handle subtask
        - Otherwise → dispatch sub-tasks
        """
        task = await self.task_store.get(task_id)
        if not task:
            return

        if source_role == "reviewer":
            # Reviewer checkpoint approved — task is now
            # ACTIVE (unblocked by checkpoint_manager).
            # Transition ACTIVE → REVIEW → DONE
            await self.task_store.update_status(
                task_id, TaskStatus.REVIEW,
                event_by="user",
                event_detail="reviewer checkpoint approved",
            )
            await self.task_store.update_status(
                task_id, TaskStatus.DONE,
                event_by="user",
                event_detail="approved after review",
            )
            task = await self.task_store.get(task_id)
            if task:
                await self._handle_subtask_done(task)
            return

        if not task.subtask_ids:
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

        Routes based on source_role:
        - Reviewer checkpoint → ACTIVE → REVIEW → REJECTED,
          fail parent if applicable
        - PM/Architect → clean up sub-tasks, fail parent
        """
        task = await self.task_store.get(task_id)
        if task is None:
            return

        if source_role == "reviewer":
            # Reviewer checkpoint rejected — task is now
            # ACTIVE (unblocked by checkpoint_manager).
            # Transition ACTIVE → REVIEW → REJECTED
            await self.task_store.update_status(
                task_id, TaskStatus.REVIEW,
                event_by="user",
                event_detail="reviewer checkpoint rejected",
            )
            await self.task_store.update_status(
                task_id, TaskStatus.REJECTED,
                event_by="user",
                event_detail="rejected after review",
            )
            # If this is a sub-task, fail the parent too
            if task.parent_id:
                parent = await self.task_store.get(task.parent_id)
                if parent and parent.status == TaskStatus.ACTIVE:
                    await self.task_store.update_status(
                        parent.id, TaskStatus.REVIEW,
                        event_by="orchestrator",
                        event_detail=(
                            f"sub-task {task.id} review rejected"
                        ),
                    )
                    await self.task_store.update_status(
                        parent.id, TaskStatus.REJECTED,
                        event_by="orchestrator",
                        event_detail="sub-task review failure",
                    )
            return

        if task.subtask_ids:
            await self.dispatcher.cleanup_subtasks(task_id)

        detail = (
            "design rejected by user"
            if source_role == "architect"
            else "plan rejected by user"
        )

        # Transition parent ACTIVE -> REVIEW -> REJECTED
        if task.status == TaskStatus.ACTIVE:
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
