from legatus.models.task import Task, TaskEvent, TaskStatus, TaskType
from legatus.redis_client.task_store import TaskStore


class TaskManager:
    """Task lifecycle state machine."""

    def __init__(self, task_store: TaskStore):
        self._store = task_store

    async def create_task(self, prompt: str, title: str | None = None) -> Task:
        """Create a new task from a user prompt and immediately plan it.

        In Phase 1 there is no PM agent, so we go CREATED -> PLANNED directly.
        """
        task = Task(
            title=title or prompt[:80],
            description=prompt,
            prompt=prompt,
            type=TaskType.FEATURE,
            history=[TaskEvent(event="created", by="user")],
        )
        task = await self._store.create(task)

        # Immediately transition to PLANNED (no PM agent in Phase 1)
        task = await self._store.update_status(
            task.id,
            TaskStatus.PLANNED,
            event_by="orchestrator",
            event_detail="auto-planned (Phase 1)",
        )
        return task

    async def transition(
        self,
        task_id: str,
        new_status: TaskStatus,
        event_by: str | None = None,
        event_detail: str | None = None,
    ) -> Task:
        """Validate and execute a state transition."""
        return await self._store.update_status(
            task_id, new_status, event_by=event_by, event_detail=event_detail
        )

    async def on_task_complete(self, task_id: str, result: dict) -> Task:
        """Agent reports task completion.

        In Phase 1 (no reviewer), we go ACTIVE -> DONE directly
        by first transitioning to REVIEW, then to DONE.
        """
        task = await self.transition(
            task_id,
            TaskStatus.REVIEW,
            event_by="agent",
            event_detail="task completed",
        )
        task = await self.transition(
            task_id,
            TaskStatus.DONE,
            event_by="orchestrator",
            event_detail="auto-approved (Phase 1)",
        )
        return task

    async def on_task_failed(self, task_id: str, error: str) -> Task:
        """Agent reports task failure. Mark as REJECTED so it can be re-planned."""
        # ACTIVE -> REVIEW -> REJECTED
        task = await self.transition(
            task_id,
            TaskStatus.REVIEW,
            event_by="agent",
            event_detail=f"failed: {error}",
        )
        task = await self.transition(
            task_id,
            TaskStatus.REJECTED,
            event_by="orchestrator",
            event_detail=error,
        )
        return task
