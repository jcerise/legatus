from fastapi import APIRouter, Depends

from legatus.orchestrator.dependencies import get_event_bus, get_state_store
from legatus.orchestrator.services.event_bus import EventBus
from legatus.redis_client.state import StateStore

router = APIRouter(prefix="/system", tags=["system"])


@router.post("/pause")
async def pause(
    state_store: StateStore = Depends(get_state_store),
) -> dict:
    """Pause task dispatch. Running agents will finish but no new tasks are dispatched."""
    await state_store.set_paused(True)
    return {"paused": True}


@router.post("/resume")
async def resume(
    state_store: StateStore = Depends(get_state_store),
    event_bus: EventBus = Depends(get_event_bus),
) -> dict:
    """Resume task dispatch and catch up on queued work."""
    await state_store.set_paused(False)
    await event_bus.resume_dispatch()
    return {"paused": False}


@router.get("/status")
async def system_status(
    state_store: StateStore = Depends(get_state_store),
) -> dict:
    """Get system status including paused state."""
    paused = await state_store.is_paused()
    return {"paused": paused}
