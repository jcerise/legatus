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
                    "### Project Context\n" + "\n".join(f"- {line}" for line in lines)
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
                    "### User Preferences\n" + "\n".join(f"- {line}" for line in lines)
                )
        except Exception:
            logger.debug("No global memories found", exc_info=True)

        return "\n\n".join(sections)

    async def get_reviewer_context(self, task: Task) -> str:
        """Query Mem0 for standard context plus coding standards for reviewers."""
        base_context = await self.get_context(task)
        sections: list[str] = [base_context] if base_context else []

        try:
            project_ns = MemoryNamespace.project(self.project_id)
            standards = await self.mem0.search(
                query="coding standards conventions style rules",
                **project_ns,
                limit=10,
            )
            if standards:
                lines = [m.get("memory", str(m)) for m in standards]
                sections.append(
                    "### Project Coding Standards\n"
                    + "\n".join(f"- {line}" for line in lines)
                )
        except Exception:
            logger.debug("No coding standards memories found", exc_info=True)

        return "\n\n".join(sections)

    async def extract_learnings(self, task: Task, result: dict) -> None:
        """After task completion, parse structured learnings and store them."""
        output = result.get("output", "")
        if not output:
            return

        learnings = self._parse_learnings(output)

        # Store project-level memory
        project_ns = MemoryNamespace.project(self.project_id)
        if learnings:
            await self._store(
                f"Task '{task.title}' learnings:\n{learnings}",
                namespace=project_ns,
                metadata={"task_id": task.id, "type": "learnings"},
            )
        else:
            # Fallback: store a truncated summary if no structured section found
            await self._store(
                f"Completed task '{task.title}': {output[:500]}",
                namespace=project_ns,
                metadata={"task_id": task.id, "type": "task_completion"},
            )

        # Store general patterns under global namespace
        if learnings:
            global_ns = MemoryNamespace.global_user()
            await self._store(
                f"Patterns from '{task.title}':\n{learnings}",
                namespace=global_ns,
                metadata={"task_id": task.id, "type": "patterns"},
            )

        logger.info("Extracted memories for task %s", task.id)

    @staticmethod
    def _parse_learnings(output: str) -> str | None:
        """Extract the last ## Learnings section from Claude output."""
        marker = "## Learnings"
        idx = output.rfind(marker)
        if idx == -1:
            return None

        section = output[idx + len(marker):]

        # Trim at the next heading if present
        for next_heading in ("\n## ", "\n# "):
            end = section.find(next_heading)
            if end != -1:
                section = section[:end]

        return section.strip() or None

    async def _store(self, text: str, namespace: dict, metadata: dict) -> None:
        """Store a memory, logging failures without raising."""
        try:
            await self.mem0.add(text, **namespace, metadata=metadata)
        except Exception:
            logger.exception("Failed to store memory: %s", text[:80])
