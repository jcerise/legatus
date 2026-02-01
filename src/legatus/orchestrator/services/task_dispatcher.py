"""Dispatches sub-tasks sequentially and detects parent completion."""

import logging

from legatus.models.agent import AgentRole
from legatus.models.task import Task, TaskStatus
from legatus.orchestrator.services.agent_spawner import AgentSpawner
from legatus.orchestrator.services.architect_parser import (
    parse_architect_output,
)
from legatus.redis_client.state import StateStore
from legatus.redis_client.task_store import TaskStore

logger = logging.getLogger(__name__)


class TaskDispatcher:
    """Dispatches sub-tasks sequentially and manages parent completion."""

    def __init__(
        self,
        task_store: TaskStore,
        state_store: StateStore,
        spawner: AgentSpawner,
    ):
        self.task_store = task_store
        self.state_store = state_store
        self.spawner = spawner

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
                # Mark child as failed, try the next one
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

    async def on_subtask_complete(self, parent_id: str) -> None:
        """Called when a sub-task finishes.

        Dispatches the next ready sub-task, or completes/fails
        the parent if all children are finished.
        """
        parent = await self.task_store.get(parent_id)
        if parent is None:
            return

        all_done = True
        any_failed = False
        for child_id in parent.subtask_ids:
            child = await self.task_store.get(child_id)
            if child is None:
                continue
            if child.status == TaskStatus.REJECTED:
                any_failed = True
            elif child.status != TaskStatus.DONE:
                all_done = False

        if all_done and not any_failed:
            logger.info(
                "All sub-tasks done for parent %s, completing",
                parent_id,
            )
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
            return

        if any_failed:
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
            return

        # Not all done yet — try to dispatch the next one
        dispatched = await self.dispatch_next(parent_id)
        if not dispatched:
            logger.debug(
                "No sub-task ready to dispatch for parent %s"
                " (waiting on running task)",
                parent_id,
            )

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
