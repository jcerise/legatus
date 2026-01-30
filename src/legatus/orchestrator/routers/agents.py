from fastapi import APIRouter, Depends

from legatus.models.agent import AgentInfo
from legatus.orchestrator.dependencies import get_state_store
from legatus.redis_client.state import StateStore

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/", response_model=list[AgentInfo])
async def list_agents(
    state_store: StateStore = Depends(get_state_store),
):
    """List all active and recent agents."""
    return await state_store.list_agents()
