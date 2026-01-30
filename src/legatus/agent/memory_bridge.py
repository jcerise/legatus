import logging

from legatus.memory.client import Mem0Client
from legatus.memory.namespaces import MemoryNamespace
from legatus.models.task import Task

logger = logging.getLogger(__name__)


class MemoryBridge:
    """Handles memory injection (before task) and extraction (after task)."""

    def __init__(self, mem0: Mem0Client, project_id: str):
        self.mem0 = mem0
        self.project_id = project_id

    async def get_context(self, task: Task) -> str:
        """Query Mem0 for relevant memories and format as context string."""
        sections: list[str] = []

        try:
            project_ns = MemoryNamespace.project(self.project_id)
            project_memories = await self.mem0.search(
                query=task.description,
                **project_ns,
                limit=10,
            )
            if project_memories:
                lines = [m.get("memory", str(m)) for m in project_memories]
                sections.append(
                    "## Project Context\n" + "\n".join(f"- {line}" for line in lines)
                )
        except Exception:
            logger.debug("No project memories found", exc_info=True)

        try:
            global_ns = MemoryNamespace.global_user()
            global_memories = await self.mem0.search(
                query=task.description,
                **global_ns,
                limit=5,
            )
            if global_memories:
                lines = [m.get("memory", str(m)) for m in global_memories]
                sections.append(
                    "## User Preferences\n" + "\n".join(f"- {line}" for line in lines)
                )
        except Exception:
            logger.debug("No global memories found", exc_info=True)

        return "\n\n".join(sections)

    async def extract_learnings(self, task: Task, result: dict) -> None:
        """After task completion, save key learnings to Mem0 project memory."""
        output = result.get("output", "")
        if not output:
            return

        try:
            summary = f"Completed task '{task.title}': {output[:500]}"
            ns = MemoryNamespace.project(self.project_id)
            await self.mem0.add(
                summary,
                **ns,
                metadata={"task_id": task.id, "type": "task_completion"},
            )
            logger.info("Extracted memories for task %s", task.id)
        except Exception:
            logger.exception("Failed to extract memories for task %s", task.id)
