import json
from datetime import UTC, datetime

from legatus.models.agent import AgentInfo
from legatus.redis_client.client import RedisClient


class StateStore:
    """Agent and session state management in Redis."""

    AGENT_PREFIX = "agent"
    AGENT_INDEX = "agents:all"
    LOG_KEY = "logs:activity"

    def __init__(self, redis_client: RedisClient):
        self._redis = redis_client

    def _agent_key(self, agent_id: str) -> str:
        return f"{self.AGENT_PREFIX}:{agent_id}"

    async def set_agent_info(self, agent: AgentInfo) -> None:
        r = self._redis.client
        await r.set(self._agent_key(agent.id), agent.model_dump_json())
        await r.sadd(self.AGENT_INDEX, agent.id)

    async def get_agent_info(self, agent_id: str) -> AgentInfo | None:
        r = self._redis.client
        data = await r.get(self._agent_key(agent_id))
        if data is None:
            return None
        return AgentInfo.model_validate_json(data)

    async def list_agents(self) -> list[AgentInfo]:
        r = self._redis.client
        agent_ids = await r.smembers(self.AGENT_INDEX)
        agents = []
        for aid in agent_ids:
            agent = await self.get_agent_info(aid)
            if agent:
                agents.append(agent)
        return agents

    async def remove_agent(self, agent_id: str) -> None:
        r = self._redis.client
        await r.delete(self._agent_key(agent_id))
        await r.srem(self.AGENT_INDEX, agent_id)

    async def append_log(self, entry: dict) -> None:
        r = self._redis.client
        if "timestamp" not in entry:
            entry["timestamp"] = datetime.now(UTC).isoformat()
        await r.lpush(self.LOG_KEY, json.dumps(entry, default=str))
        # Keep last 1000 entries
        await r.ltrim(self.LOG_KEY, 0, 999)

    async def get_logs(self, limit: int = 50) -> list[dict]:
        r = self._redis.client
        raw = await r.lrange(self.LOG_KEY, 0, limit - 1)
        return [json.loads(entry) for entry in raw]
