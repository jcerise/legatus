import json
from datetime import UTC, datetime

from legatus.redis_client.client import RedisClient


class CostStore:
    """Tracks API cost data per project in Redis."""

    PREFIX = "costs"

    def __init__(self, redis_client: RedisClient):
        self._redis = redis_client

    async def record(
        self,
        task_id: str,
        agent_role: str,
        cost: float,
        project_id: str | None = None,
    ) -> None:
        """Record a cost entry and update the running total."""
        r = self._redis.client
        key = f"{self.PREFIX}:{project_id or 'default'}"
        entry = json.dumps(
            {
                "task_id": task_id,
                "agent_role": agent_role,
                "cost": cost,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        await r.lpush(key, entry)
        await r.incrbyfloat(f"{key}:total", cost)

    async def get_breakdown(self, project_id: str | None = None) -> dict:
        """Return total cost, per-role breakdown, and individual entries."""
        r = self._redis.client
        key = f"{self.PREFIX}:{project_id or 'default'}"

        total_raw = await r.get(f"{key}:total")
        total = float(total_raw) if total_raw else 0.0

        raw_entries = await r.lrange(key, 0, -1)
        entries = [json.loads(e) for e in raw_entries]

        by_role: dict[str, float] = {}
        for entry in entries:
            role = entry.get("agent_role", "unknown")
            by_role[role] = by_role.get(role, 0.0) + entry.get("cost", 0.0)

        return {
            "total": total,
            "by_role": by_role,
            "entries": entries,
        }
