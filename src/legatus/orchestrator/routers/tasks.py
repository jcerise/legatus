import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from legatus.models.agent import AgentRole
from legatus.models.config import LegatusSettings
from legatus.models.task import Task, TaskStatus
from legatus.orchestrator.dependencies import (
    get_settings,
    get_state_store,
    get_task_store,
)
from legatus.orchestrator.services.agent_spawner import AgentSpawner
from legatus.orchestrator.services.task_manager import TaskManager
from legatus.redis_client.state import StateStore
from legatus.redis_client.task_store import TaskStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    prompt: str
    title: str | None = None
    project: str | None = None
    direct: bool = False


@router.post("/", response_model=Task)
async def create_task(
    req: CreateTaskRequest,
    task_store: TaskStore = Depends(get_task_store),
    state_store: StateStore = Depends(get_state_store),
    settings: LegatusSettings = Depends(get_settings),
):
    """Create a new task and spawn an agent.

    By default, a PM agent is spawned to decompose the task.
    Use direct=True to bypass PM and spawn a dev agent directly.
    """
    manager = TaskManager(task_store)

    task = await manager.create_task(
        prompt=req.prompt, title=req.title, project=req.project,
    )

    # Choose agent role based on direct flag
    role = AgentRole.DEV if req.direct else AgentRole.PM

    spawner = AgentSpawner(settings)
    try:
        agent_info = spawner.spawn_agent(task, role)
    except Exception as e:
        logger.error(
            "Failed to spawn %s agent for task %s: %s",
            role.value, task.id, e,
        )
        raise HTTPException(
            status_code=503,
            detail=f"Failed to spawn agent container: {e}",
        ) from e

    await state_store.set_agent_info(agent_info)

    # Transition to ACTIVE
    task = await manager.transition(
        task.id, TaskStatus.ACTIVE,
        event_by="orchestrator",
        event_detail=f"agent={agent_info.id} role={role.value}",
    )

    task.assigned_to = agent_info.id
    await task_store.update(task)

    return task


@router.get("/", response_model=list[Task])
async def list_tasks(
    task_store: TaskStore = Depends(get_task_store),
):
    """List all tasks."""
    return await task_store.list_all()


@router.get("/history/")
async def task_history(
    limit: int = 20,
    task_store: TaskStore = Depends(get_task_store),
) -> list[Task]:
    """Return completed/rejected tasks sorted by updated_at desc."""
    all_tasks = await task_store.list_all()
    finished = [
        t for t in all_tasks
        if t.status in (TaskStatus.DONE, TaskStatus.REJECTED)
    ]
    finished.sort(key=lambda t: t.updated_at, reverse=True)
    return finished[:limit]


@router.get("/{task_id}", response_model=Task)
async def get_task(
    task_id: str,
    task_store: TaskStore = Depends(get_task_store),
):
    """Get a single task by ID."""
    task = await task_store.get(task_id)
    if task is None:
        raise HTTPException(
            status_code=404, detail=f"Task {task_id} not found",
        )
    return task
