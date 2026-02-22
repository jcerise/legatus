class MemoryNamespace:
    """Helpers to construct Mem0 user_id/agent_id for the three-tier namespace scheme."""

    @staticmethod
    def working(project_id: str, agent_id: str) -> dict[str, str]:
        """Working memory scope: ephemeral, per-task."""
        return {"user_id": f"working:{project_id}:{agent_id}"}

    @staticmethod
    def campaign(project_id: str, parent_id: str) -> dict[str, str]:
        """Campaign working memory: shared across all agents in a campaign.

        Agents write completion summaries here so sibling agents can
        see what work has been done and avoid conflicts.  Cleared
        when the campaign finishes.
        """
        return {"user_id": f"working:{project_id}:campaign:{parent_id}"}

    @staticmethod
    def project(project_id: str) -> dict[str, str]:
        """Project memory scope: persistent per-project."""
        return {"user_id": f"project:{project_id}"}

    @staticmethod
    def global_user() -> dict[str, str]:
        """Global memory scope: cross-project user preferences."""
        return {"user_id": "global:user"}
