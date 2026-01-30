class MemoryNamespace:
    """Helpers to construct Mem0 user_id/agent_id for the three-tier namespace scheme."""

    @staticmethod
    def working(project_id: str, agent_id: str) -> dict[str, str]:
        """Working memory scope: ephemeral, per-task."""
        return {"user_id": f"working:{project_id}:{agent_id}"}

    @staticmethod
    def project(project_id: str) -> dict[str, str]:
        """Project memory scope: persistent per-project."""
        return {"user_id": f"project:{project_id}"}

    @staticmethod
    def global_user() -> dict[str, str]:
        """Global memory scope: cross-project user preferences."""
        return {"user_id": "global:user"}
