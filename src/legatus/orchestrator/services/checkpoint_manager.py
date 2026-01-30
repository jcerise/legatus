from datetime import UTC, datetime

from legatus.models.checkpoint import Checkpoint, CheckpointStatus
from legatus.models.task import TaskStatus
from legatus.redis_client.client import RedisClient
from legatus.redis_client.task_store import TaskStore


class CheckpointManager:
    """Checkpoint lifecycle: create, approve, reject."""

    KEY_PREFIX = "checkpoint"
    PENDING_INDEX = "checkpoints:pending"

    def __init__(self, redis_client: RedisClient, task_store: TaskStore):
        self._redis = redis_client
        self._task_store = task_store

    def _key(self, checkpoint_id: str) -> str:
        return f"{self.KEY_PREFIX}:{checkpoint_id}"

    async def create(
        self,
        task_id: str,
        title: str,
        description: str,
    ) -> Checkpoint:
        """Create a checkpoint and block its associated task."""
        cp = Checkpoint(task_id=task_id, title=title, description=description)

        r = self._redis.client
        await r.set(self._key(cp.id), cp.model_dump_json())
        await r.zadd(self.PENDING_INDEX, {cp.id: cp.created_at.timestamp()})

        # Block the task
        await self._task_store.update_status(
            task_id,
            TaskStatus.BLOCKED,
            event_by="checkpoint",
            event_detail=f"checkpoint={cp.id}: {title}",
        )

        return cp

    async def get(self, checkpoint_id: str) -> Checkpoint | None:
        r = self._redis.client
        data = await r.get(self._key(checkpoint_id))
        if data is None:
            return None
        return Checkpoint.model_validate_json(data)

    async def get_pending(self) -> list[Checkpoint]:
        r = self._redis.client
        cp_ids = await r.zrange(self.PENDING_INDEX, 0, -1)
        checkpoints = []
        for cid in cp_ids:
            cp = await self.get(cid)
            if cp and cp.status == CheckpointStatus.PENDING:
                checkpoints.append(cp)
        return checkpoints

    async def approve(self, checkpoint_id: str) -> Checkpoint | None:
        cp = await self.get(checkpoint_id)
        if cp is None:
            return None

        cp.status = CheckpointStatus.APPROVED
        cp.resolved_at = datetime.now(UTC)
        cp.resolved_by = "user"

        r = self._redis.client
        await r.set(self._key(cp.id), cp.model_dump_json())
        await r.zrem(self.PENDING_INDEX, cp.id)

        # Unblock the task
        await self._task_store.update_status(
            cp.task_id,
            TaskStatus.ACTIVE,
            event_by="user",
            event_detail=f"checkpoint {cp.id} approved",
        )

        return cp

    async def reject(self, checkpoint_id: str, reason: str = "") -> Checkpoint | None:
        cp = await self.get(checkpoint_id)
        if cp is None:
            return None

        cp.status = CheckpointStatus.REJECTED
        cp.resolved_at = datetime.now(UTC)
        cp.resolved_by = "user"
        cp.rejection_reason = reason

        r = self._redis.client
        await r.set(self._key(cp.id), cp.model_dump_json())
        await r.zrem(self.PENDING_INDEX, cp.id)

        # Move task to REVIEW then REJECTED so it can be re-planned
        task = await self._task_store.get(cp.task_id)
        if task and task.status == TaskStatus.BLOCKED:
            # BLOCKED -> ACTIVE (unblock first), then handled externally
            await self._task_store.update_status(
                cp.task_id,
                TaskStatus.ACTIVE,
                event_by="user",
                event_detail=f"checkpoint {cp.id} rejected: {reason}",
            )

        return cp
