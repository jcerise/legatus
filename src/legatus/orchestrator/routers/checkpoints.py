from fastapi import APIRouter, Depends, HTTPException

from legatus.models.checkpoint import Checkpoint
from legatus.orchestrator.dependencies import (
    get_event_bus,
    get_redis,
    get_task_store,
)
from legatus.orchestrator.services.checkpoint_manager import (
    CheckpointManager,
)
from legatus.orchestrator.services.event_bus import EventBus
from legatus.redis_client.client import RedisClient
from legatus.redis_client.task_store import TaskStore

router = APIRouter(prefix="/checkpoints", tags=["checkpoints"])


def _get_checkpoint_manager(
    redis: RedisClient = Depends(get_redis),
    task_store: TaskStore = Depends(get_task_store),
) -> CheckpointManager:
    return CheckpointManager(redis, task_store)


@router.get("/", response_model=list[Checkpoint])
async def list_checkpoints(
    manager: CheckpointManager = Depends(_get_checkpoint_manager),
):
    """List all pending checkpoints."""
    return await manager.get_pending()


@router.get("/{checkpoint_id}", response_model=Checkpoint)
async def get_checkpoint(
    checkpoint_id: str,
    manager: CheckpointManager = Depends(_get_checkpoint_manager),
):
    """Get a specific checkpoint."""
    cp = await manager.get(checkpoint_id)
    if cp is None:
        raise HTTPException(
            status_code=404,
            detail=f"Checkpoint {checkpoint_id} not found",
        )
    return cp


@router.post("/{checkpoint_id}/approve", response_model=Checkpoint)
async def approve_checkpoint(
    checkpoint_id: str,
    manager: CheckpointManager = Depends(_get_checkpoint_manager),
    event_bus: EventBus = Depends(get_event_bus),
):
    """Approve a checkpoint and unblock its associated task."""
    cp = await manager.approve(checkpoint_id)
    if cp is None:
        raise HTTPException(
            status_code=404,
            detail=f"Checkpoint {checkpoint_id} not found",
        )

    # Trigger sub-task dispatch if applicable
    await event_bus.on_checkpoint_approved(cp.task_id)

    return cp


@router.post("/{checkpoint_id}/reject", response_model=Checkpoint)
async def reject_checkpoint(
    checkpoint_id: str,
    reason: str = "",
    manager: CheckpointManager = Depends(_get_checkpoint_manager),
    event_bus: EventBus = Depends(get_event_bus),
):
    """Reject a checkpoint with feedback."""
    cp = await manager.reject(checkpoint_id, reason)
    if cp is None:
        raise HTTPException(
            status_code=404,
            detail=f"Checkpoint {checkpoint_id} not found",
        )

    # Clean up sub-tasks and fail the parent
    await event_bus.on_checkpoint_rejected(cp.task_id)

    return cp
