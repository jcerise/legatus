"""Dispatches sub-tasks and detects parent completion."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from legatus.models.agent import AgentRole
from legatus.models.task import Task, TaskStatus
from legatus.orchestrator.services.agent_spawner import AgentSpawner
from legatus.orchestrator.services.architect_parser import (
    parse_architect_output,
)
from legatus.redis_client.state import StateStore
from legatus.redis_client.task_store import TaskStore

if TYPE_CHECKING:
    from legatus.models.config import LegatusSettings
    from legatus.orchestrator.services.git_ops import GitOps

logger = logging.getLogger(__name__)

_RUNNING_STATUSES = {
    TaskStatus.ACTIVE,
    TaskStatus.REVIEW,
    TaskStatus.TESTING,
}


class TaskDispatcher:
    """Dispatches sub-tasks and manages parent completion.

    When parallel mode is enabled (via settings), multiple sub-tasks
    whose dependencies are satisfied will be dispatched simultaneously,
    each in its own git worktree.
    """

    def __init__(
        self,
        task_store: TaskStore,
        state_store: StateStore,
        spawner: AgentSpawner,
        git_ops: GitOps | None = None,
        settings: LegatusSettings | None = None,
    ):
        self.task_store = task_store
        self.state_store = state_store
        self.spawner = spawner
        self.git_ops = git_ops
        self.settings = settings

    @property
    def _parallel_enabled(self) -> bool:
        return bool(self.settings and self.settings.agent.parallel_enabled)

    @property
    def _worktree_base(self) -> str:
        if self.settings:
            return self.settings.agent.worktree_base
        return "/workspace-worktrees"

    # --------------------------------------------------
    # Sequential dispatch (original behaviour)
    # --------------------------------------------------

    async def dispatch_next(self, parent_id: str) -> bool:
        """Find and dispatch the next ready sub-task for a parent.

        Returns True if a sub-task was dispatched, False otherwise.
        """
        parent = await self.task_store.get(parent_id)
        if parent is None:
            logger.error("Parent task %s not found", parent_id)
            return False

        for child_id in parent.subtask_ids:
            child = await self.task_store.get(child_id)
            if child is None or child.status != TaskStatus.PLANNED:
                continue

            # Check dependencies
            deps_met = True
            for dep_id in child.depends_on:
                dep = await self.task_store.get(dep_id)
                if dep is None or dep.status != TaskStatus.DONE:
                    deps_met = False
                    break

            if not deps_met:
                continue

            # Inject architect context into sub-task if available
            architect_ctx = _format_architect_context(parent)
            if architect_ctx:
                child.description = (
                    child.description + architect_ctx
                )
                await self.task_store.update(child)

            # Dispatch this child
            try:
                agent_info = self.spawner.spawn_agent(
                    child, AgentRole.DEV,
                )
            except Exception as e:
                logger.error(
                    "Failed to spawn dev agent for sub-task %s: %s",
                    child_id, e,
                )
                # Walk through valid transitions: PLANNED→ACTIVE→REVIEW→REJECTED
                await self.task_store.update_status(
                    child_id, TaskStatus.ACTIVE,
                    event_by="orchestrator",
                    event_detail=f"spawn failed: {e}",
                )
                await self.task_store.update_status(
                    child_id, TaskStatus.REVIEW,
                    event_by="orchestrator",
                    event_detail=f"spawn failed: {e}",
                )
                await self.task_store.update_status(
                    child_id, TaskStatus.REJECTED,
                    event_by="orchestrator",
                    event_detail=f"spawn failed: {e}",
                )
                continue

            await self.state_store.set_agent_info(agent_info)
            await self.task_store.update_status(
                child_id, TaskStatus.ACTIVE,
                event_by="orchestrator",
                event_detail=f"agent={agent_info.id}",
            )
            child = await self.task_store.get(child_id)
            if child:
                child.assigned_to = agent_info.id
                await self.task_store.update(child)

            logger.info(
                "Dispatched sub-task %s (%s) with agent %s",
                child_id, child.title if child else "?", agent_info.id,
            )
            return True

        return False

    # --------------------------------------------------
    # Parallel dispatch
    # --------------------------------------------------

    async def dispatch_all_ready(self, parent_id: str) -> int:
        """Dispatch ALL sub-tasks whose dependencies are satisfied.

        Creates a git worktree for each task before spawning
        its dev agent. Returns the number of tasks dispatched.
        """
        parent = await self.task_store.get(parent_id)
        if parent is None:
            logger.error("Parent task %s not found", parent_id)
            return 0

        dispatched = 0
        for child_id in parent.subtask_ids:
            child = await self.task_store.get(child_id)
            if child is None or child.status != TaskStatus.PLANNED:
                continue

            # Check dependencies
            deps_met = True
            for dep_id in child.depends_on:
                dep = await self.task_store.get(dep_id)
                if dep is None or dep.status != TaskStatus.DONE:
                    deps_met = False
                    break

            if not deps_met:
                continue

            # Inject architect context
            architect_ctx = _format_architect_context(parent)
            if architect_ctx:
                child.description = child.description + architect_ctx
                await self.task_store.update(child)

            # Create worktree for this task
            branch_name = f"legatus/task-{child.id}"
            worktree_path = os.path.join(
                self._worktree_base, f"task-{child.id}",
            )
            try:
                if self.git_ops:
                    self.git_ops.create_worktree(worktree_path, branch_name)
            except Exception as e:
                logger.error(
                    "Failed to create worktree for task %s: %s",
                    child_id, e,
                )
                # Walk through valid transitions: PLANNED→ACTIVE→REVIEW→REJECTED
                await self.task_store.update_status(
                    child_id, TaskStatus.ACTIVE,
                    event_by="orchestrator",
                    event_detail=f"worktree failed: {e}",
                )
                await self.task_store.update_status(
                    child_id, TaskStatus.REVIEW,
                    event_by="orchestrator",
                    event_detail=f"worktree failed: {e}",
                )
                await self.task_store.update_status(
                    child_id, TaskStatus.REJECTED,
                    event_by="orchestrator",
                    event_detail=f"worktree failed: {e}",
                )
                continue

            # Store branch on the task so spawner mounts the worktree
            child = await self.task_store.get(child_id)
            if child is None:
                continue
            child.branch_name = branch_name
            await self.task_store.update(child)

            # Spawn DEV agent
            try:
                agent_info = self.spawner.spawn_agent(
                    child, AgentRole.DEV,
                )
            except Exception as e:
                logger.error(
                    "Failed to spawn dev agent for sub-task %s: %s",
                    child_id, e,
                )
                # Clean up the worktree we just created
                if self.git_ops:
                    try:
                        self.git_ops.remove_worktree(worktree_path)
                        self.git_ops.delete_branch(branch_name)
                    except Exception:
                        pass
                # Walk through valid transitions: PLANNED→ACTIVE→REVIEW→REJECTED
                await self.task_store.update_status(
                    child_id, TaskStatus.ACTIVE,
                    event_by="orchestrator",
                    event_detail=f"spawn failed: {e}",
                )
                await self.task_store.update_status(
                    child_id, TaskStatus.REVIEW,
                    event_by="orchestrator",
                    event_detail=f"spawn failed: {e}",
                )
                await self.task_store.update_status(
                    child_id, TaskStatus.REJECTED,
                    event_by="orchestrator",
                    event_detail=f"spawn failed: {e}",
                )
                continue

            await self.state_store.set_agent_info(agent_info)
            await self.task_store.update_status(
                child_id, TaskStatus.ACTIVE,
                event_by="orchestrator",
                event_detail=f"agent={agent_info.id}",
            )
            child = await self.task_store.get(child_id)
            if child:
                child.assigned_to = agent_info.id
                await self.task_store.update(child)

            logger.info(
                "Dispatched sub-task %s (%s) with agent %s [branch=%s]",
                child_id, child.title if child else "?",
                agent_info.id, branch_name,
            )
            dispatched += 1

        return dispatched

    # --------------------------------------------------
    # Re-dispatch (review/QA retry)
    # --------------------------------------------------

    async def dispatch_single(self, task: Task) -> bool:
        """Re-dispatch a specific task to a DEV agent.

        Used for reviewer/QA retry: transitions PLANNED -> ACTIVE
        and spawns a DEV agent. Injects architect context from
        the parent if available (skips duplication check).

        If the task has a branch_name, the worktree already exists
        and the spawner will mount it automatically.

        Returns True if the agent was spawned successfully.
        """
        # Inject architect context if parent has it
        if task.parent_id:
            parent = await self.task_store.get(task.parent_id)
            if parent:
                architect_ctx = _format_architect_context(parent)
                if architect_ctx and architect_ctx not in task.description:
                    task.description = task.description + architect_ctx
                    await self.task_store.update(task)

        try:
            agent_info = self.spawner.spawn_agent(
                task, AgentRole.DEV,
            )
        except Exception as e:
            logger.error(
                "Failed to spawn dev agent for task %s: %s",
                task.id, e,
            )
            return False

        await self.state_store.set_agent_info(agent_info)
        await self.task_store.update_status(
            task.id, TaskStatus.ACTIVE,
            event_by="orchestrator",
            event_detail=f"retry agent={agent_info.id}",
        )
        task_updated = await self.task_store.get(task.id)
        if task_updated:
            task_updated.assigned_to = agent_info.id
            await self.task_store.update(task_updated)

        logger.info(
            "Re-dispatched task %s (%s) with agent %s",
            task.id, task.title, agent_info.id,
        )
        return True

    # --------------------------------------------------
    # Subtask completion
    # --------------------------------------------------

    async def on_subtask_complete(
        self, parent_id: str,
    ) -> str | None:
        """Called when a sub-task finishes.

        Returns a signal string instead of transitioning the parent:
        - "all_done" -- all children DONE, parent NOT transitioned
        - "failed" -- a child REJECTED, parent marked REJECTED
        - None -- more children pending, dispatched or waiting

        The caller (EventBus) decides whether to spawn a
        per-campaign reviewer or complete the parent directly.
        """
        parent = await self.task_store.get(parent_id)
        if parent is None:
            return None

        # Don't re-evaluate if the parent is BLOCKED (awaiting user
        # decision on a checkpoint, e.g. agent failure).
        if parent.status == TaskStatus.BLOCKED:
            return None

        all_done = True
        any_failed = False
        any_running = False
        for child_id in parent.subtask_ids:
            child = await self.task_store.get(child_id)
            if child is None:
                continue
            if child.status == TaskStatus.REJECTED:
                any_failed = True
            elif child.status in _RUNNING_STATUSES:
                any_running = True
                all_done = False
            elif child.status != TaskStatus.DONE:
                all_done = False

        if all_done:
            logger.info(
                "All sub-tasks done for parent %s",
                parent_id,
            )
            return "all_done"

        if any_failed and not any_running:
            # All running tasks finished and at least one failed
            logger.error(
                "Sub-task failed for parent %s", parent_id,
            )
            await self.task_store.update_status(
                parent_id, TaskStatus.REVIEW,
                event_by="orchestrator",
                event_detail="sub-task failed",
            )
            await self.task_store.update_status(
                parent_id, TaskStatus.REJECTED,
                event_by="orchestrator",
                event_detail="sub-task failure",
            )
            return "failed"

        if any_failed:
            # A task failed but others are still running.
            # Don't dispatch new tasks; wait for running ones to finish.
            logger.info(
                "Sub-task failed for parent %s, waiting for running tasks",
                parent_id,
            )
            return None

        # Dispatch next task(s)
        if self._parallel_enabled:
            count = await self.dispatch_all_ready(parent_id)
            if count:
                logger.info(
                    "Dispatched %d newly-unblocked tasks for parent %s",
                    count, parent_id,
                )
        else:
            dispatched = await self.dispatch_next(parent_id)
            if not dispatched:
                logger.debug(
                    "No sub-task ready to dispatch for parent %s"
                    " (waiting on running task)",
                    parent_id,
                )
        return None

    async def cleanup_subtasks(self, parent_id: str) -> None:
        """Mark all pending sub-tasks as REJECTED.

        Called when a checkpoint is rejected and we need to
        abandon the decomposition plan.
        """
        parent = await self.task_store.get(parent_id)
        if parent is None:
            return

        for child_id in parent.subtask_ids:
            child = await self.task_store.get(child_id)
            if child is None:
                continue
            if child.status in (
                TaskStatus.CREATED, TaskStatus.PLANNED,
            ):
                # CREATED -> PLANNED -> ACTIVE -> REVIEW -> REJECTED
                # But child might be CREATED or PLANNED, so step through
                if child.status == TaskStatus.CREATED:
                    await self.task_store.update_status(
                        child_id, TaskStatus.PLANNED,
                        event_by="orchestrator",
                        event_detail="plan rejected",
                    )
                await self.task_store.update_status(
                    child_id, TaskStatus.ACTIVE,
                    event_by="orchestrator",
                    event_detail="plan rejected",
                )
                await self.task_store.update_status(
                    child_id, TaskStatus.REVIEW,
                    event_by="orchestrator",
                    event_detail="plan rejected",
                )
                await self.task_store.update_status(
                    child_id, TaskStatus.REJECTED,
                    event_by="orchestrator",
                    event_detail="parent plan rejected by user",
                )


def _format_architect_context(parent: Task) -> str | None:
    """Format the architect's parsed design as guidance for DEV agents.

    Returns a markdown section to append to the sub-task description,
    or None if no architect output exists.
    """
    raw = parent.agent_outputs.get("architect")
    if not raw:
        return None

    plan = parse_architect_output(raw)
    if plan is None:
        # Fallback: couldn't parse, skip rather than inject garbage
        return None

    lines = [
        "\n\n## Architecture Guidance",
        "The following design decisions were approved by the"
        " Architect. Follow these guidelines during implementation.",
        "",
    ]

    if plan.decisions:
        lines.append("### Design Decisions")
        for d in plan.decisions:
            title = d.get("title", "Untitled")
            rationale = d.get("rationale", "")
            lines.append(f"- **{title}**: {rationale}")
        lines.append("")

    if plan.interfaces:
        lines.append("### Interfaces")
        for iface in plan.interfaces:
            module = iface.get("module", "?")
            defn = iface.get("definition", "")
            lines.append(f"- **{module}**: {defn}")
        lines.append("")

    if plan.concerns:
        lines.append("### Concerns")
        for c in plan.concerns:
            lines.append(f"- {c}")
        lines.append("")

    if plan.design_notes:
        lines.append("### Additional Notes")
        lines.append(plan.design_notes)
        lines.append("")

    return "\n".join(lines)
