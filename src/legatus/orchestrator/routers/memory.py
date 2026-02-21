from fastapi import APIRouter, Depends

from legatus.memory.client import Mem0Client
from legatus.memory.namespaces import MemoryNamespace
from legatus.orchestrator.dependencies import get_mem0

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/")
async def list_memories(
    namespace: str = "project",
    project_id: str | None = None,
    mem0: Mem0Client = Depends(get_mem0),
) -> list[dict]:
    """List memories for a namespace."""
    if namespace == "global":
        ns = MemoryNamespace.global_user()
    else:
        ns = MemoryNamespace.project(project_id or "default")
    return await mem0.list_memories(**ns)


@router.get("/search")
async def search_memories(
    query: str,
    namespace: str = "project",
    project_id: str | None = None,
    limit: int = 10,
    mem0: Mem0Client = Depends(get_mem0),
) -> list[dict]:
    """Search memories by semantic similarity."""
    if namespace == "global":
        ns = MemoryNamespace.global_user()
    else:
        ns = MemoryNamespace.project(project_id or "default")
    return await mem0.search(query, **ns, limit=limit)


@router.delete("/{memory_id}")
async def delete_memory(
    memory_id: str,
    mem0: Mem0Client = Depends(get_mem0),
) -> dict:
    """Delete a specific memory."""
    await mem0.delete(memory_id)
    return {"deleted": memory_id}
