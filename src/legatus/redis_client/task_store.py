from datetime import UTC, datetime

from legatus.models.task import VALID_TRANSITIONS, Task, TaskEvent, TaskStatus
from legatus.redis_client.client import RedisClient


class TaskStore:
    """CRUD operations for tasks in Redis."""

    KEY_PREFIX = "task"
    INDEX_KEY = "tasks:all"

    def __init__(self, redis_client: RedisClient):
        self._redis = redis_client

    def _key(self, task_id: str) -> str:
        return f"{self.KEY_PREFIX}:{task_id}"

    async def create(self, task: Task) -> Task:
        r = self._redis.client
        key = self._key(task.id)
        data = task.model_dump_json()
        await r.set(key, data)
        score = task.created_at.timestamp()
        await r.zadd(self.INDEX_KEY, {task.id: score})
        return task

    async def get(self, task_id: str) -> Task | None:
        r = self._redis.client
        data = await r.get(self._key(task_id))
        if data is None:
            return None
        return Task.model_validate_json(data)

    async def list_all(self) -> list[Task]:
        r = self._redis.client
        task_ids = await r.zrange(self.INDEX_KEY, 0, -1)
        tasks = []
        for tid in task_ids:
            task = await self.get(tid)
            if task:
                tasks.append(task)
        return tasks

    async def update(self, task: Task) -> Task:
        task.updated_at = datetime.now(UTC)
        r = self._redis.client
        await r.set(self._key(task.id), task.model_dump_json())
        return task

    async def update_status(
        self,
        task_id: str,
        new_status: TaskStatus,
        event_by: str | None = None,
        event_detail: str | None = None,
    ) -> Task:
        task = await self.get(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        allowed = VALID_TRANSITIONS.get(task.status, [])
        if new_status not in allowed:
            raise ValueError(
                f"Invalid transition: {task.status} -> {new_status}. "
                f"Allowed: {allowed}"
            )

        task.status = new_status
        task.history.append(
            TaskEvent(
                event=f"status_change:{new_status}",
                by=event_by,
                detail=event_detail,
            )
        )
        return await self.update(task)

    async def get_by_status(self, status: TaskStatus) -> list[Task]:
        all_tasks = await self.list_all()
        return [t for t in all_tasks if t.status == status]

    async def get_next_ready(self) -> Task | None:
        """Get the highest-priority PLANNED task with no unresolved dependencies."""
        planned = await self.get_by_status(TaskStatus.PLANNED)
        if not planned:
            return None

        # Sort by priority (lower number = higher priority)
        planned.sort(key=lambda t: t.priority)

        for task in planned:
            if not task.depends_on:
                return task
            # Check if all dependencies are DONE
            all_done = True
            for dep_id in task.depends_on:
                dep = await self.get(dep_id)
                if dep is None or dep.status != TaskStatus.DONE:
                    all_done = False
                    break
            if all_done:
                return task

        return None
