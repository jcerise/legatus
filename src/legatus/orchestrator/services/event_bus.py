import asyncio
import fnmatch
import logging
import os

from fastapi import WebSocket

from legatus.models.agent import AgentRole, AgentStatus
from legatus.models.config import LegatusSettings, QAMode, ReviewMode
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
from legatus.orchestrator.services.qa_parser import (
    QAResult,
    parse_qa_output,
)
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

# File patterns that can be auto-resolved during merge by accepting the
# incoming (task-branch) version.  Only generated / build artifacts that
# are never hand-edited should appear here.
_AUTO_RESOLVE_PATTERNS = [
    ".coverage",
    ".coverage.*",
    "htmlcov/*",
    "*.pyc",
    "*.pyo",
    "__pycache__/*",
    "*.egg-info/*",
    "dist/*",
    "build/*",
    ".eggs/*",
    ".pytest_cache/*",
    ".mypy_cache/*",
    ".ruff_cache/*",
    ".tox/*",
    ".DS_Store",
    "Thumbs.db",
    "*.log",
]


def _can_auto_resolve(conflict_files: list[str]) -> bool:
    """Return True if every conflicted file matches an auto-resolve pattern."""
    return all(
        any(fnmatch.fnmatch(f, pat) for pat in _AUTO_RESOLVE_PATTERNS)
        for f in conflict_files
    )


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
            redis_client,
            task_store,
        )
        self.dispatcher = TaskDispatcher(
            task_store,
            state_store,
            spawner,
            git_ops=self.git_ops,
            settings=settings,
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
            msg.type,
            msg.task_id,
            msg.agent_id,
        )

        try:
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
        except Exception:
            logger.exception(
                "Error handling message: type=%s task=%s agent=%s",
                msg.type,
                msg.task_id,
                msg.agent_id,
            )

        await self._broadcast_to_ws(msg)

    async def _on_task_complete(self, msg: Message) -> None:
        if not msg.task_id or not msg.agent_id:
            return

        # Determine the agent's role
        agent_info = await self.state_store.get_agent_info(
            msg.agent_id,
        )
        agent_role = agent_info.role if agent_info else AgentRole.DEV

        match agent_role:
            case AgentRole.PM:
                await self._on_pm_complete(msg)
            case AgentRole.ARCHITECT:
                await self._on_architect_complete(msg)
            case AgentRole.REVIEWER:
                await self._on_reviewer_complete(msg)
            case AgentRole.QA:
                await self._on_qa_complete(msg)
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
                "PM agent failed to produce a valid decomposition plan",
            )
            return

        parent = await self.task_store.get(task_id)
        if parent is None:
            return

        # Store raw PM output for downstream agents (architect)
        parent.agent_outputs["pm"] = output
        await self.task_store.update(parent)

        # Create child tasks in order
        parallel = (
            self.settings.agent.parallel_enabled
            if self.settings else False
        )
        child_ids: list[str] = []
        prev_child_id: str | None = None
        for i, st in enumerate(plan.subtasks):
            # Determine dependencies
            if parallel:
                # Use PM-specified depends_on indices, converted to task IDs
                deps = [
                    child_ids[idx]
                    for idx in st.depends_on
                    if idx < len(child_ids)
                ]
            else:
                # Sequential: each task depends on the previous
                deps = [prev_child_id] if prev_child_id else []

            child = Task(
                title=st.title,
                description=st.description,
                prompt=st.description,
                type=parent.type,
                project=parent.project,
                parent_id=task_id,
                acceptance_criteria=st.acceptance_criteria,
                priority=parent.priority,
                depends_on=deps,
                created_by="pm_agent",
                history=[
                    TaskEvent(
                        event="created",
                        by="pm_agent",
                        detail=(f"sub-task {i + 1}/{len(plan.subtasks)}"),
                    )
                ],
            )
            child = await self.task_store.create(child)
            # Transition child CREATED -> PLANNED
            await self.task_store.update_status(
                child.id,
                TaskStatus.PLANNED,
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
            f"PM decomposed '{parent.title}' into {len(child_ids)} sub-tasks:",
            "",
        ]
        if plan.analysis:
            desc_lines.append(f"**Analysis**: {plan.analysis}")
            desc_lines.append("")

        for i, st in enumerate(plan.subtasks):
            desc_lines.append(f"{i + 1}. **{st.title}** ({st.estimated_complexity})")
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
            task_id,
            len(child_ids),
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
                    desc_lines.append(f"- **{title}**: {rationale[:200]}")
                desc_lines.append("")

            if plan.interfaces:
                desc_lines.append("**Interfaces:**")
                for iface in plan.interfaces:
                    module = iface.get("module", "?")
                    defn = iface.get(
                        "definition",
                        "",
                    )[:200]
                    desc_lines.append(f"- **{module}**: {defn}")
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
            if task.branch_name:
                worktree_path = os.path.join(
                    self.settings.agent.worktree_base if self.settings else "/workspace-worktrees",
                    f"task-{task.id}",
                )
                commit_hash = self.git_ops.commit_in_worktree(
                    worktree_path,
                    f"legatus: {task.title} ({task.id})",
                )
            else:
                commit_hash = self.git_ops.commit_changes(
                    f"legatus: {task.title} ({task.id})",
                )
            if commit_hash:
                logger.info("Git commit: %s", commit_hash)
        except Exception:
            logger.exception(
                "Git commit failed for task %s",
                task_id,
            )

        # Check if reviewer is enabled for per-subtask mode
        reviewer_enabled = self.settings.agent.reviewer_enabled if self.settings else False
        review_mode = self.settings.agent.review_mode if self.settings else ReviewMode.PER_SUBTASK

        # Read QA settings
        qa_enabled = self.settings.agent.qa_enabled if self.settings else False
        qa_mode = self.settings.agent.qa_mode if self.settings else QAMode.PER_SUBTASK

        if reviewer_enabled and review_mode == ReviewMode.PER_SUBTASK and task.parent_id:
            # Route to reviewer: ACTIVE → REVIEW only
            await self.task_store.update_status(
                task_id,
                TaskStatus.REVIEW,
                event_by="agent",
                event_detail="dev complete, awaiting review",
            )
            await self._spawn_reviewer(task)
        elif qa_enabled and qa_mode == QAMode.PER_SUBTASK and task.parent_id:
            # Route to QA directly (no reviewer): ACTIVE → TESTING
            await self.task_store.update_status(
                task_id,
                TaskStatus.TESTING,
                event_by="agent",
                event_detail="dev complete, awaiting QA",
            )
            await self._spawn_qa(task)
        else:
            # Original path: ACTIVE → REVIEW → DONE
            await self.task_manager.on_task_complete(
                task_id,
                msg.data,
            )
            await self._handle_subtask_done(task)

    async def _spawn_reviewer(self, task: Task) -> None:
        """Spawn a reviewer agent for the given task.

        Falls back to auto-approve if spawning fails.
        """
        try:
            agent_info = self.spawner.spawn_agent(
                task,
                AgentRole.REVIEWER,
            )
            await self.state_store.set_agent_info(agent_info)
            logger.info(
                "Spawned reviewer agent %s for task %s",
                agent_info.id,
                task.id,
            )
        except Exception:
            logger.exception(
                "Failed to spawn reviewer for task %s, auto-approving",
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
        """Approve: REVIEW → TESTING (if QA) or REVIEW → DONE."""
        qa_enabled = self.settings.agent.qa_enabled if self.settings else False
        qa_mode = self.settings.agent.qa_mode if self.settings else QAMode.PER_SUBTASK

        # Per-subtask QA on a subtask
        if qa_enabled and qa_mode == QAMode.PER_SUBTASK and task.parent_id:
            await self.task_store.update_status(
                task.id,
                TaskStatus.TESTING,
                event_by="reviewer",
                event_detail="reviewer approved, awaiting QA",
            )
            task = await self.task_store.get(task.id)
            if task:
                await self._spawn_qa(task)
            logger.info(
                "Reviewer approved task %s, routing to QA",
                task.id if task else "?",
            )
            return

        # Per-campaign QA on the parent (campaign-level reviewer)
        if qa_enabled and qa_mode == QAMode.PER_CAMPAIGN and not task.parent_id:
            await self.task_store.update_status(
                task.id,
                TaskStatus.TESTING,
                event_by="reviewer",
                event_detail="reviewer approved, awaiting campaign QA",
            )
            task = await self.task_store.get(task.id)
            if task:
                await self._spawn_qa(task)
            logger.info(
                "Reviewer approved campaign %s, routing to QA",
                task.id if task else "?",
            )
            return

        # No QA — original path: REVIEW → DONE
        await self.task_store.update_status(
            task.id,
            TaskStatus.DONE,
            event_by="reviewer",
            event_detail="reviewer approved",
        )
        logger.info(
            "Reviewer approved task %s",
            task.id,
        )
        # Re-fetch task to get updated status
        task = await self.task_store.get(task.id)
        if task:
            await self._handle_subtask_done(task)

    async def _reviewer_reject(
        self,
        task: Task,
        review: ReviewResult,
    ) -> None:
        """Reject: retry DEV or escalate to checkpoint."""
        max_retries = self.settings.agent.reviewer_max_retries if self.settings else 1
        retry_count = int(task.agent_outputs.get("reviewer_retry_count", "0"))

        if retry_count < max_retries:
            # Store feedback for the next DEV run
            task.agent_outputs["reviewer_feedback"] = review.summary
            task.agent_outputs["reviewer_retry_count"] = str(retry_count + 1)
            await self.task_store.update(task)

            # REVIEW → REJECTED → PLANNED, then re-dispatch
            await self.task_store.update_status(
                task.id,
                TaskStatus.REJECTED,
                event_by="reviewer",
                event_detail=f"rejected (retry {retry_count + 1}/"
                f"{max_retries}): {review.summary[:200]}",
            )
            await self.task_store.update_status(
                task.id,
                TaskStatus.PLANNED,
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
                        "Failed to re-dispatch task %s after reviewer rejection",
                        task.id,
                    )
            logger.info(
                "Reviewer rejected task %s, retrying DEV (attempt %d/%d)",
                task.id if task else "?",
                retry_count + 1,
                max_retries,
            )
        else:
            # Retries exhausted — escalate to user
            desc_lines = [
                f"Reviewer rejected task '{task.title}' after {max_retries} DEV retry(ies).",
                "",
                f"**Summary**: {review.summary}",
                "",
            ]
            if review.findings:
                desc_lines.append("**Findings:**")
                for f in review.findings:
                    desc_lines.append(f"- [{f.severity}] {f.category} ({f.file}): {f.description}")
                desc_lines.append("")

            desc_lines.append("**Next steps:**")
            desc_lines.append(
                "- **Approve** to accept the code as-is and"
                " continue the campaign."
            )
            desc_lines.append(
                "- **Reject** to abandon this task."
                " The campaign will be marked as failed."
            )
            desc_lines.append("")

            await self.checkpoint_manager.create(
                task_id=task.id,
                title=f"Review failed: {task.title}",
                description="\n".join(desc_lines),
                source_role="reviewer",
            )
            logger.info(
                "Reviewer rejected task %s, retries exhausted, checkpoint created",
                task.id,
            )

    async def _reviewer_security_checkpoint(
        self,
        task: Task,
        review: ReviewResult,
    ) -> None:
        """Security concerns found — always create checkpoint."""
        desc_lines = [
            f"Reviewer found security concerns in '{task.title}':",
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
                desc_lines.append(f"- [{f.severity}] {f.category} ({f.file}): {f.description}")
            desc_lines.append("")

        await self.checkpoint_manager.create(
            task_id=task.id,
            title=f"Security review: {task.title}",
            description="\n".join(desc_lines),
            source_role="reviewer",
        )
        logger.info(
            "Reviewer flagged security concerns for task %s, checkpoint created",
            task.id,
        )

    # --------------------------------------------------
    # QA (tesserarius) methods
    # --------------------------------------------------

    async def _spawn_qa(self, task: Task) -> None:
        """Spawn a QA agent for the given task.

        Falls back to auto-pass if spawning fails.
        """
        try:
            agent_info = self.spawner.spawn_agent(
                task,
                AgentRole.QA,
            )
            await self.state_store.set_agent_info(agent_info)
            logger.info(
                "Spawned QA agent %s for task %s",
                agent_info.id,
                task.id,
            )
        except Exception:
            logger.exception(
                "Failed to spawn QA for task %s, auto-passing",
                task.id,
            )
            await self._qa_approve(task)

    async def _on_qa_complete(self, msg: Message) -> None:
        """Handle QA agent completion: parse output, git commit
        test files, route to approve or reject."""
        task_id = msg.task_id
        output = msg.data.get("output", "")

        task = await self.task_store.get(task_id)
        if task is None:
            return

        # Store QA output
        task.agent_outputs["qa"] = output
        await self.task_store.update(task)

        # Git commit test files (best-effort — QA writes tests)
        try:
            if task.branch_name:
                worktree_path = os.path.join(
                    self.settings.agent.worktree_base if self.settings else "/workspace-worktrees",
                    f"task-{task.id}",
                )
                commit_hash = self.git_ops.commit_in_worktree(
                    worktree_path,
                    f"legatus: QA tests for {task.title} ({task.id})",
                )
            else:
                commit_hash = self.git_ops.commit_changes(
                    f"legatus: QA tests for {task.title} ({task.id})",
                )
            if commit_hash:
                logger.info("QA git commit: %s", commit_hash)
        except Exception:
            logger.exception(
                "Git commit failed for QA on task %s",
                task_id,
            )

        qa_result = parse_qa_output(output)

        if qa_result is None or qa_result.verdict == "pass":
            # Parse failure → auto-pass
            await self._qa_approve(task)
        else:
            await self._qa_reject(task, qa_result)

    async def _qa_approve(self, task: Task) -> None:
        """QA passed: TESTING → DONE, then handle subtask
        completion."""
        await self.task_store.update_status(
            task.id,
            TaskStatus.DONE,
            event_by="qa",
            event_detail="QA passed",
        )
        logger.info("QA passed for task %s", task.id)
        task = await self.task_store.get(task.id)
        if task:
            await self._handle_subtask_done(task)

    async def _qa_reject(
        self,
        task: Task,
        qa_result: QAResult,
    ) -> None:
        """QA failed: retry DEV or escalate to checkpoint."""
        max_retries = self.settings.agent.qa_max_retries if self.settings else 1
        retry_count = int(task.agent_outputs.get("qa_retry_count", "0"))

        if retry_count < max_retries:
            # Build feedback string for the next DEV run
            feedback_parts = [qa_result.summary]
            if qa_result.failure_details:
                feedback_parts.append(qa_result.failure_details)
            for tr in qa_result.test_results:
                if tr.status in ("fail", "error"):
                    feedback_parts.append(f"- {tr.name}: {tr.status} — {tr.output[:300]}")

            task.agent_outputs["qa_feedback"] = "\n".join(feedback_parts)
            task.agent_outputs["qa_retry_count"] = str(retry_count + 1)
            await self.task_store.update(task)

            # TESTING → REJECTED → PLANNED, then re-dispatch DEV
            await self.task_store.update_status(
                task.id,
                TaskStatus.REJECTED,
                event_by="qa",
                event_detail=f"QA failed (retry {retry_count + 1}/"
                f"{max_retries}): {qa_result.summary[:200]}",
            )
            await self.task_store.update_status(
                task.id,
                TaskStatus.PLANNED,
                event_by="orchestrator",
                event_detail="queued for retry after QA failure",
            )

            # Re-fetch and re-dispatch
            task = await self.task_store.get(task.id)
            if task:
                dispatched = await self.dispatcher.dispatch_single(
                    task,
                )
                if not dispatched:
                    logger.error(
                        "Failed to re-dispatch task %s after QA failure",
                        task.id,
                    )
            logger.info(
                "QA rejected task %s, retrying DEV (attempt %d/%d)",
                task.id if task else "?",
                retry_count + 1,
                max_retries,
            )
        else:
            # Retries exhausted — escalate to user
            desc_lines = [
                f"QA failed for task '{task.title}' after {max_retries} DEV retry(ies).",
                "",
                f"**Summary**: {qa_result.summary}",
                "",
            ]
            if qa_result.test_results:
                desc_lines.append("**Test Results:**")
                for tr in qa_result.test_results:
                    desc_lines.append(f"- {tr.name}: {tr.status}")
                    if tr.output:
                        desc_lines.append(f"  {tr.output[:200]}")
                desc_lines.append("")

            if qa_result.failure_details:
                desc_lines.append("**Failure Details:**")
                desc_lines.append(qa_result.failure_details[:500])
                desc_lines.append("")

            desc_lines.append("**Next steps:**")
            desc_lines.append(
                "- **Approve** to accept the code as-is and"
                " continue the campaign."
            )
            desc_lines.append(
                "- **Reject** to abandon this task."
                " The campaign will be marked as failed."
            )
            desc_lines.append("")

            await self.checkpoint_manager.create(
                task_id=task.id,
                title=f"QA failed: {task.title}",
                description="\n".join(desc_lines),
                source_role="qa",
            )
            logger.info(
                "QA rejected task %s, retries exhausted, checkpoint created",
                task.id,
            )

    async def _handle_subtask_done(self, task: Task) -> None:
        """Shared helper called after a sub-task reaches DONE.

        If the task has a branch (parallel mode), merges the
        branch into the working branch and cleans up the worktree
        before dispatching the next tasks.
        """
        if not task.parent_id:
            return

        # Parallel mode: merge the task's branch into the working branch
        if task.branch_name:
            merged = await self._merge_and_cleanup(task)
            if not merged:
                # Merge conflict — checkpoint created, campaign paused
                return

        result = await self.dispatcher.on_subtask_complete(
            task.parent_id,
        )
        if result == "all_done":
            await self._on_campaign_done(task.parent_id)

    async def _merge_and_cleanup(self, task: Task) -> bool:
        """Merge a completed task's branch into the working branch.

        Returns True if the merge succeeded (or was a no-op),
        False if there was a conflict (checkpoint created).
        """
        worktree_base = (
            self.settings.agent.worktree_base
            if self.settings
            else "/workspace-worktrees"
        )
        worktree_path = os.path.join(worktree_base, f"task-{task.id}")

        try:
            merge_result = self.git_ops.merge_branch(
                task.branch_name,
                f"merge: {task.title} ({task.id})",
            )
        except Exception:
            logger.exception(
                "Merge failed for task %s branch %s",
                task.id, task.branch_name,
            )
            # Remove worktree directory but keep the branch so the
            # work isn't lost and can be recovered manually.
            import contextlib

            with contextlib.suppress(Exception):
                self.git_ops.remove_worktree(worktree_path)
            return True

        if merge_result.success:
            logger.info(
                "Merged branch %s for task %s: %s",
                task.branch_name, task.id, merge_result.commit_hash,
            )
            self._cleanup_worktree(worktree_path, task.branch_name)
            return True

        # Merge conflict
        if merge_result.conflict_files:
            # Auto-resolve if all conflicts are on generated artifacts
            if _can_auto_resolve(merge_result.conflict_files):
                try:
                    self.git_ops.resolve_conflicts_theirs(
                        merge_result.conflict_files,
                    )
                    self.git_ops.commit_merge_resolution(
                        f"merge (auto-resolved): {task.title} ({task.id})",
                    )
                    logger.info(
                        "Auto-resolved %d conflict(s) for task %s: %s",
                        len(merge_result.conflict_files),
                        task.id,
                        merge_result.conflict_files,
                    )
                    self._cleanup_worktree(worktree_path, task.branch_name)
                    return True
                except Exception:
                    logger.exception(
                        "Auto-resolve failed for task %s, escalating",
                        task.id,
                    )
                    self.git_ops.abort_merge()

            # Real conflicts (or auto-resolve failed) — abort and
            # create checkpoint for user resolution
            else:
                self.git_ops.abort_merge()

            conflict_list = "\n".join(
                f"- `{f}`" for f in merge_result.conflict_files
            )
            desc = (
                f"Merge conflict when integrating '{task.title}'"
                f" (branch `{task.branch_name}`).\n\n"
                f"**Conflicted files:**\n{conflict_list}\n\n"
                "Resolve the conflicts in `/workspace` (the working"
                " branch), then approve this checkpoint to continue."
            )

            await self.checkpoint_manager.create(
                task_id=task.id,
                title=f"Merge conflict: {task.title}",
                description=desc,
                source_role="merge_conflict",
            )
            logger.warning(
                "Merge conflict for task %s, checkpoint created",
                task.id,
            )
            return False

        # Non-conflict merge failure — keep the branch for recovery
        logger.error(
            "Merge failed for task %s (no conflicts detected)."
            " Branch %s preserved for manual recovery.",
            task.id, task.branch_name,
        )
        import contextlib

        with contextlib.suppress(Exception):
            self.git_ops.remove_worktree(worktree_path)
        return True

    def _cleanup_worktree(
        self, worktree_path: str, branch_name: str,
    ) -> None:
        """Remove worktree and delete the task branch (best-effort)."""
        try:
            self.git_ops.remove_worktree(worktree_path)
        except Exception:
            logger.exception(
                "Failed to remove worktree at %s", worktree_path,
            )
        try:
            self.git_ops.delete_branch(branch_name)
        except Exception:
            logger.exception(
                "Failed to delete branch %s", branch_name,
            )

    async def _on_campaign_done(self, parent_id: str) -> None:
        """Called when all sub-tasks for a campaign are finished.

        Merges the campaign working branch back to the original
        branch (if parallel mode was used), then routes to
        per-campaign review/QA or marks the parent as done.
        """
        reviewer_enabled = self.settings.agent.reviewer_enabled if self.settings else False
        review_mode = self.settings.agent.review_mode if self.settings else ReviewMode.PER_SUBTASK
        qa_enabled = self.settings.agent.qa_enabled if self.settings else False
        qa_mode = self.settings.agent.qa_mode if self.settings else QAMode.PER_SUBTASK

        parent = await self.task_store.get(parent_id)
        if parent is None:
            return

        # Merge campaign branch back to the original branch so that
        # per-campaign review/QA (and the user) see the final state
        # on the original branch rather than a detached campaign branch.
        await self._finalize_campaign_branch(parent)

        if reviewer_enabled and review_mode == ReviewMode.PER_CAMPAIGN:
            # Aggregate child dev outputs into parent
            aggregated = await self._aggregate_child_outputs(parent)
            parent.agent_outputs["dev"] = aggregated
            await self.task_store.update(parent)

            # Transition parent ACTIVE → REVIEW, spawn reviewer
            # (reviewer_approve will chain to QA if enabled)
            await self.task_store.update_status(
                parent_id,
                TaskStatus.REVIEW,
                event_by="orchestrator",
                event_detail="all sub-tasks done, campaign review",
            )
            await self._spawn_reviewer(parent)
        elif qa_enabled and qa_mode == QAMode.PER_CAMPAIGN:
            # Per-campaign QA without reviewer
            aggregated = await self._aggregate_child_outputs(parent)
            parent.agent_outputs["dev"] = aggregated
            await self.task_store.update(parent)

            await self.task_store.update_status(
                parent_id,
                TaskStatus.TESTING,
                event_by="orchestrator",
                event_detail="all sub-tasks done, campaign QA",
            )
            await self._spawn_qa(parent)
        else:
            # No campaign review or QA — complete the parent
            await self.task_store.update_status(
                parent_id,
                TaskStatus.REVIEW,
                event_by="orchestrator",
                event_detail="all sub-tasks completed",
            )
            await self.task_store.update_status(
                parent_id,
                TaskStatus.DONE,
                event_by="orchestrator",
                event_detail="all sub-tasks done",
            )

    async def _finalize_campaign_branch(self, parent: Task) -> None:
        """Merge the campaign working branch back to the original branch.

        Called after all sub-tasks have been merged into the campaign
        branch.  Checks out the original branch, merges the campaign
        branch in, and cleans up.  No-ops when parallel mode was not
        used (no ``_original_branch`` stored on the parent).
        """
        original_branch = parent.agent_outputs.get("_original_branch")
        if not original_branch:
            return

        campaign_branch = f"legatus/campaign-{parent.id}"
        try:
            self.git_ops.checkout(original_branch)
            merge_result = self.git_ops.merge_branch(
                campaign_branch,
                f"legatus: campaign {parent.title} ({parent.id})",
            )
            if merge_result.success:
                self.git_ops.delete_branch(campaign_branch)
                logger.info(
                    "Campaign %s merged to %s",
                    parent.id, original_branch,
                )
            else:
                logger.error(
                    "Failed to merge campaign %s to %s: conflicts=%s",
                    parent.id,
                    original_branch,
                    merge_result.conflict_files,
                )
                if merge_result.conflict_files:
                    self.git_ops.abort_merge()
                # Leave both branches for manual resolution
        except Exception:
            logger.exception(
                "Failed to merge campaign %s to %s",
                parent.id, original_branch,
            )

    async def _aggregate_child_outputs(
        self,
        parent: Task,
    ) -> str:
        """Aggregate child dev outputs into a single string."""
        aggregated = []
        for child_id in parent.subtask_ids:
            child = await self.task_store.get(child_id)
            if child and child.agent_outputs.get("dev"):
                aggregated.append(f"### Sub-task: {child.title}\n{child.agent_outputs['dev']}")
        return "\n\n".join(aggregated)

    async def _on_task_failed(self, msg: Message) -> None:
        if not msg.task_id:
            return

        error = msg.data.get("error", "Unknown error")
        await self.task_manager.on_task_failed(msg.task_id, error)

        # Clean up worktree if this was a parallel task
        task = await self.task_store.get(msg.task_id)
        if task and task.branch_name:
            worktree_base = (
                self.settings.agent.worktree_base
                if self.settings
                else "/workspace-worktrees"
            )
            worktree_path = os.path.join(
                worktree_base, f"task-{task.id}",
            )
            self._cleanup_worktree(worktree_path, task.branch_name)

        # If this was a sub-task, create a checkpoint so the user
        # can see the error and decide how to proceed.
        if task and task.parent_id:
            parent = await self.task_store.get(task.parent_id)
            if parent and parent.status == TaskStatus.ACTIVE:
                error_short = error[:500]
                desc_lines = [
                    f"Agent failed for task '{task.title}'.",
                    "",
                    f"**Error**: {error_short}",
                    "",
                    "**Next steps:**",
                    "- **Approve** to skip this task and"
                    " continue the campaign with the"
                    " remaining tasks.",
                    "- **Reject** to abandon the campaign.",
                    "",
                ]
                try:
                    await self.checkpoint_manager.create(
                        task_id=parent.id,
                        title=f"Agent failed: {task.title}",
                        description="\n".join(desc_lines),
                        source_role="agent_failed",
                    )
                except Exception:
                    logger.exception(
                        "Failed to create checkpoint for agent"
                        " failure on task %s, failing parent",
                        task.id,
                    )
                    # Fallback: cascade failure directly
                    # (parent might already be BLOCKED if
                    #  checkpoint partially succeeded)
                    parent = await self.task_store.get(
                        task.parent_id,
                    )
                    if parent and parent.status == TaskStatus.ACTIVE:
                        await self.task_store.update_status(
                            parent.id,
                            TaskStatus.REVIEW,
                            event_by="orchestrator",
                            event_detail=(
                                f"sub-task {task.id} failed: {error}"
                            ),
                        )
                        await self.task_store.update_status(
                            parent.id,
                            TaskStatus.REJECTED,
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
        - merge_conflict → commit resolution, cleanup, dispatch
        - PM checkpoint + architect enabled → spawn Architect
        - Reviewer checkpoint → check QA, then DONE
        - QA checkpoint → TESTING → DONE, handle subtask
        - Otherwise → dispatch sub-tasks
        """
        task = await self.task_store.get(task_id)
        if not task:
            return

        if source_role == "merge_conflict":
            # User resolved merge conflicts in the workspace.
            # Commit the resolution, cleanup, and continue dispatching.
            try:
                self.git_ops.commit_merge_resolution(
                    f"merge resolution: {task.title} ({task.id})",
                )
            except Exception:
                logger.exception(
                    "Failed to commit merge resolution for task %s",
                    task_id,
                )

            if task.branch_name:
                worktree_base = (
                    self.settings.agent.worktree_base
                    if self.settings
                    else "/workspace-worktrees"
                )
                worktree_path = os.path.join(
                    worktree_base, f"task-{task.id}",
                )
                self._cleanup_worktree(worktree_path, task.branch_name)

            # Continue dispatching
            if task.parent_id:
                result = await self.dispatcher.on_subtask_complete(
                    task.parent_id,
                )
                if result == "all_done":
                    await self._on_campaign_done(task.parent_id)
            return

        if source_role == "qa":
            # QA checkpoint approved — task is now ACTIVE
            # (unblocked by checkpoint_manager).
            # Transition ACTIVE → TESTING → DONE
            await self.task_store.update_status(
                task_id,
                TaskStatus.TESTING,
                event_by="user",
                event_detail="QA checkpoint approved",
            )
            await self.task_store.update_status(
                task_id,
                TaskStatus.DONE,
                event_by="user",
                event_detail="approved after QA",
            )
            task = await self.task_store.get(task_id)
            if task:
                await self._handle_subtask_done(task)
            return

        if source_role == "agent_failed":
            # User chose to skip the failed sub-task and continue
            # the campaign.  The checkpoint was created on the
            # *parent* task, so task_id IS the parent.
            # Re-evaluate campaign state now that it's unblocked.
            result = await self.dispatcher.on_subtask_complete(task_id)
            if result == "all_done":
                await self._on_campaign_done(task_id)
            return

        if source_role == "reviewer":
            # Reviewer checkpoint approved — task is now
            # ACTIVE (unblocked by checkpoint_manager).
            # Check if QA should run before going to DONE
            qa_enabled = self.settings.agent.qa_enabled if self.settings else False
            qa_mode = self.settings.agent.qa_mode if self.settings else QAMode.PER_SUBTASK

            if qa_enabled and (
                (qa_mode == QAMode.PER_SUBTASK and task.parent_id)
                or (qa_mode == QAMode.PER_CAMPAIGN and not task.parent_id)
            ):
                # Route to QA: ACTIVE → TESTING
                await self.task_store.update_status(
                    task_id,
                    TaskStatus.TESTING,
                    event_by="user",
                    event_detail=("reviewer checkpoint approved, routing to QA"),
                )
                task = await self.task_store.get(task_id)
                if task:
                    await self._spawn_qa(task)
                return

            # No QA — original path: ACTIVE → REVIEW → DONE
            await self.task_store.update_status(
                task_id,
                TaskStatus.REVIEW,
                event_by="user",
                event_detail="reviewer checkpoint approved",
            )
            await self.task_store.update_status(
                task_id,
                TaskStatus.DONE,
                event_by="user",
                event_detail="approved after review",
            )
            task = await self.task_store.get(task_id)
            if task:
                await self._handle_subtask_done(task)
            return

        if not task.subtask_ids:
            return

        architect_enabled = self.settings.agent.architect_review if self.settings else True

        if source_role == "pm" and architect_enabled:
            # Spawn architect agent to review the plan
            try:
                agent_info = self.spawner.spawn_agent(
                    task,
                    AgentRole.ARCHITECT,
                )
                await self.state_store.set_agent_info(agent_info)
                logger.info(
                    "Spawned architect agent %s for task %s",
                    agent_info.id,
                    task_id,
                )
            except Exception:
                logger.exception(
                    "Failed to spawn architect for task %s, proceeding to dispatch",
                    task_id,
                )
                await self._dispatch_initial(task_id)
        else:
            # Architect already approved, or disabled — dispatch
            await self._dispatch_initial(task_id)

    async def _dispatch_initial(self, parent_id: str) -> None:
        """Dispatch the first batch of sub-tasks for a campaign.

        In parallel mode, creates a working branch and dispatches
        all ready tasks. In sequential mode, dispatches the first task.
        """
        parallel = (
            self.settings.agent.parallel_enabled
            if self.settings else False
        )

        if parallel:
            # Save the original branch so we can merge back after
            # the campaign completes.
            try:
                original_branch = self.git_ops.get_current_branch()
                parent = await self.task_store.get(parent_id)
                if parent:
                    parent.agent_outputs["_original_branch"] = original_branch
                    await self.task_store.update(parent)
            except Exception:
                logger.exception(
                    "Failed to save original branch for campaign %s",
                    parent_id,
                )

            # Create working branch before dispatching
            branch_name = f"legatus/campaign-{parent_id}"
            try:
                self.git_ops.ensure_working_branch(branch_name)
            except Exception:
                logger.exception(
                    "Failed to create working branch for campaign %s",
                    parent_id,
                )

            try:
                count = await self.dispatcher.dispatch_all_ready(parent_id)
            except Exception:
                logger.exception(
                    "dispatch_all_ready failed for campaign %s",
                    parent_id,
                )
                count = 0
            logger.info(
                "Parallel dispatch: %d tasks for campaign %s",
                count, parent_id,
            )
        else:
            await self.dispatcher.dispatch_next(parent_id)

    async def on_checkpoint_rejected(
        self,
        task_id: str,
        source_role: str | None = None,
    ) -> None:
        """Hook called when a checkpoint is rejected.

        Routes based on source_role:
        - merge_conflict → abort merge, fail subtask/parent
        - QA checkpoint → ACTIVE → TESTING → REJECTED,
          fail parent if applicable
        - Reviewer checkpoint → ACTIVE → REVIEW → REJECTED,
          fail parent if applicable
        - PM/Architect → clean up sub-tasks, fail parent
        """
        task = await self.task_store.get(task_id)
        if task is None:
            return

        if source_role == "merge_conflict":
            # User rejected the merge conflict resolution
            self.git_ops.abort_merge()

            if task.branch_name:
                worktree_base = (
                    self.settings.agent.worktree_base
                    if self.settings
                    else "/workspace-worktrees"
                )
                worktree_path = os.path.join(
                    worktree_base, f"task-{task.id}",
                )
                self._cleanup_worktree(worktree_path, task.branch_name)

            # Let the dispatcher re-evaluate parent state
            if task.parent_id:
                await self.dispatcher.on_subtask_complete(
                    task.parent_id,
                )
            return

        if source_role == "qa":
            # QA checkpoint rejected — task is now ACTIVE
            # (unblocked by checkpoint_manager).
            # Transition ACTIVE → TESTING → REJECTED
            await self.task_store.update_status(
                task_id,
                TaskStatus.TESTING,
                event_by="user",
                event_detail="QA checkpoint rejected",
            )
            await self.task_store.update_status(
                task_id,
                TaskStatus.REJECTED,
                event_by="user",
                event_detail="rejected after QA",
            )
            # If this is a sub-task, fail the parent too
            if task.parent_id:
                parent = await self.task_store.get(task.parent_id)
                if parent and parent.status == TaskStatus.ACTIVE:
                    await self.task_store.update_status(
                        parent.id,
                        TaskStatus.REVIEW,
                        event_by="orchestrator",
                        event_detail=(f"sub-task {task.id} QA rejected"),
                    )
                    await self.task_store.update_status(
                        parent.id,
                        TaskStatus.REJECTED,
                        event_by="orchestrator",
                        event_detail="sub-task QA failure",
                    )
            return

        if source_role == "agent_failed":
            # User rejected — abandon the campaign.
            # The checkpoint was on the parent, so task_id IS
            # the parent.  Transition ACTIVE → REVIEW → REJECTED.
            await self.task_store.update_status(
                task_id,
                TaskStatus.REVIEW,
                event_by="user",
                event_detail="agent failure checkpoint rejected",
            )
            await self.task_store.update_status(
                task_id,
                TaskStatus.REJECTED,
                event_by="user",
                event_detail="campaign abandoned after agent failure",
            )
            return

        if source_role == "reviewer":
            # Reviewer checkpoint rejected — task is now
            # ACTIVE (unblocked by checkpoint_manager).
            # Transition ACTIVE → REVIEW → REJECTED
            await self.task_store.update_status(
                task_id,
                TaskStatus.REVIEW,
                event_by="user",
                event_detail="reviewer checkpoint rejected",
            )
            await self.task_store.update_status(
                task_id,
                TaskStatus.REJECTED,
                event_by="user",
                event_detail="rejected after review",
            )
            # If this is a sub-task, fail the parent too
            if task.parent_id:
                parent = await self.task_store.get(task.parent_id)
                if parent and parent.status == TaskStatus.ACTIVE:
                    await self.task_store.update_status(
                        parent.id,
                        TaskStatus.REVIEW,
                        event_by="orchestrator",
                        event_detail=(f"sub-task {task.id} review rejected"),
                    )
                    await self.task_store.update_status(
                        parent.id,
                        TaskStatus.REJECTED,
                        event_by="orchestrator",
                        event_detail="sub-task review failure",
                    )
            return

        if task.subtask_ids:
            await self.dispatcher.cleanup_subtasks(task_id)

        detail = (
            "design rejected by user" if source_role == "architect" else "plan rejected by user"
        )

        # Transition parent ACTIVE -> REVIEW -> REJECTED
        if task.status == TaskStatus.ACTIVE:
            await self.task_store.update_status(
                task_id,
                TaskStatus.REVIEW,
                event_by="user",
                event_detail=detail,
            )
            await self.task_store.update_status(
                task_id,
                TaskStatus.REJECTED,
                event_by="user",
                event_detail=detail,
            )

    async def _update_agent_status(self, msg: Message) -> None:
        """Update agent status based on incoming events."""
        agent = await self.state_store.get_agent_info(msg.agent_id)
        if agent is None:
            return

        if msg.type in (
            MessageType.TASK_COMPLETE,
            MessageType.TASK_FAILED,
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
                    agent_id,
                    logs[-2000:],
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
