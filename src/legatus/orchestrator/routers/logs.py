from fastapi import APIRouter, Depends

from legatus.orchestrator.dependencies import get_state_store
from legatus.redis_client.state import StateStore

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("/")
async def get_logs(
    limit: int = 50,
    state_store: StateStore = Depends(get_state_store),
):
    """Return recent activity log entries."""
    return await state_store.get_logs(limit=limit)
