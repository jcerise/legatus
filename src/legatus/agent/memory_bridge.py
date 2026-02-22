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
        """Query Mem0 for relevant memories and format as context string.

        Searches three tiers:
        1. Campaign working memory — what sibling agents have done
        2. Project memory — persistent project knowledge
        3. Global memory — user preferences
        """
        sections: list[str] = []

        # Campaign working memory: what sibling agents have done/are doing
        if task.parent_id:
            try:
                campaign_ns = MemoryNamespace.campaign(
                    self.project_id, task.parent_id,
                )
                campaign_memories = await self.mem0.search(
                    query=task.description,
                    **campaign_ns,
                    limit=15,
                )
                if campaign_memories:
                    lines = [
                        m.get("memory", str(m))
                        for m in campaign_memories
                    ]
                    sections.append(
                        "### Campaign Progress (from sibling agents)\n"
                        + "\n".join(f"- {line}" for line in lines)
                    )
            except Exception:
                logger.debug(
                    "No campaign working memories found",
                    exc_info=True,
                )

        # Project memory: persistent project knowledge
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

        # Global memory: user preferences
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

    async def store_campaign_start(
        self,
        task: Task,
        agent_role: str,
    ) -> None:
        """Write an 'in progress' entry to campaign working memory.

        Called before an agent begins work so that sibling agents
        can see what is being worked on and avoid conflicts.
        """
        if not task.parent_id:
            return

        campaign_ns = MemoryNamespace.campaign(
            self.project_id, task.parent_id,
        )

        summary = (
            f"[{agent_role.upper()}] In progress: '{task.title}'\n"
            f"Description: {task.description[:400]}"
        )

        await self._store(
            summary,
            namespace=campaign_ns,
            metadata={
                "task_id": task.id,
                "agent_role": agent_role,
                "type": "campaign_start",
            },
        )
        logger.info(
            "Stored campaign start for task %s (role=%s)",
            task.id,
            agent_role,
        )

    async def store_campaign_progress(
        self,
        task: Task,
        result: dict,
        agent_role: str,
    ) -> None:
        """Write a completion summary to campaign working memory.

        Called after an agent finishes its subtask so that subsequent
        sibling agents can see what was done, which files were touched,
        and what approach was taken.
        """
        if not task.parent_id:
            return

        output = result.get("output", "")
        if not output:
            return

        campaign_ns = MemoryNamespace.campaign(
            self.project_id, task.parent_id,
        )

        # Build a concise summary for siblings
        summary_parts = [
            f"[{agent_role.upper()}] Completed: '{task.title}'",
        ]

        # Extract file list from learnings if available
        learnings = self._parse_learnings(output)
        if learnings:
            summary_parts.append(f"Details: {learnings[:500]}")
        else:
            summary_parts.append(f"Output summary: {output[:400]}")

        summary = "\n".join(summary_parts)

        await self._store(
            summary,
            namespace=campaign_ns,
            metadata={
                "task_id": task.id,
                "agent_role": agent_role,
                "type": "campaign_progress",
            },
        )
        logger.info(
            "Stored campaign progress for task %s (role=%s)",
            task.id,
            agent_role,
        )

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

    async def clear_campaign_memory(self, parent_id: str) -> None:
        """Clear all working memory for a completed campaign."""
        campaign_ns = MemoryNamespace.campaign(self.project_id, parent_id)
        try:
            memories = await self.mem0.list_memories(**campaign_ns)
            for mem in memories:
                mem_id = mem.get("id")
                if mem_id:
                    await self.mem0.delete(mem_id)
            if memories:
                logger.info(
                    "Cleared %d campaign working memories for %s",
                    len(memories),
                    parent_id,
                )
        except Exception:
            logger.debug(
                "Failed to clear campaign memory for %s",
                parent_id,
                exc_info=True,
            )

    @staticmethod
    def format_sibling_context(
        current_task_id: str,
        siblings: list[Task],
    ) -> str:
        """Format sibling task status as context from the task store.

        Gives agents a live view of what other agents in the same
        campaign are working on, independent of Mem0.
        """
        lines: list[str] = []
        for sib in siblings:
            if sib.id == current_task_id:
                continue
            status = sib.status.value.upper()
            line = f"- [{status}] {sib.title}"
            # Show description excerpt for in-flight siblings
            if sib.status.value in ("active", "review", "testing"):
                line += f"\n  {sib.description[:200]}"
            lines.append(line)

        if not lines:
            return ""

        return (
            "### Sibling Tasks (live status)\n"
            + "\n".join(lines)
        )

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
