from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from legatus.models.config import LegatusSettings
from legatus.models.task import Task, TaskStatus
from legatus.orchestrator.dependencies import get_settings, get_state_store, get_task_store
from legatus.orchestrator.services.agent_spawner import AgentSpawner
from legatus.orchestrator.services.task_manager import TaskManager
from legatus.redis_client.state import StateStore
from legatus.redis_client.task_store import TaskStore

router = APIRouter(prefix="/tasks", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    prompt: str
    title: str | None = None


@router.post("/", response_model=Task)
async def create_task(
    req: CreateTaskRequest,
    task_store: TaskStore = Depends(get_task_store),
    state_store: StateStore = Depends(get_state_store),
    settings: LegatusSettings = Depends(get_settings),
):
    """Create a new task from a user prompt and spawn a dev agent."""
    manager = TaskManager(task_store)

    task = await manager.create_task(prompt=req.prompt, title=req.title)

    # Spawn a dev agent container for this task
    spawner = AgentSpawner(settings)
    agent_info = spawner.spawn_dev_agent(task)
    await state_store.set_agent_info(agent_info)

    # Transition to ACTIVE
    task = await manager.transition(
        task.id, TaskStatus.ACTIVE, event_by="orchestrator", event_detail=f"agent={agent_info.id}"
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


@router.get("/{task_id}", response_model=Task)
async def get_task(
    task_id: str,
    task_store: TaskStore = Depends(get_task_store),
):
    """Get a single task by ID."""
    task = await task_store.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return task
