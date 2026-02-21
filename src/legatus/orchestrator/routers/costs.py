from fastapi import APIRouter, Depends

from legatus.orchestrator.dependencies import get_redis
from legatus.redis_client.client import RedisClient
from legatus.redis_client.cost_store import CostStore

router = APIRouter(prefix="/costs", tags=["costs"])


def _get_cost_store(redis: RedisClient = Depends(get_redis)) -> CostStore:
    return CostStore(redis)


@router.get("/")
async def get_costs(
    project_id: str | None = None,
    store: CostStore = Depends(_get_cost_store),
) -> dict:
    """Get cost breakdown for a project."""
    return await store.get_breakdown(project_id)
