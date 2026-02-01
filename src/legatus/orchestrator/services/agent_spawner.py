import logging
from datetime import UTC, datetime
from uuid import uuid4

import docker
import docker.errors

from legatus.models.agent import AgentInfo, AgentRole, AgentStatus
from legatus.models.config import LegatusSettings
from legatus.models.task import Task

logger = logging.getLogger(__name__)


class AgentSpawner:
    """Manages ephemeral Docker containers for agent execution."""

    def __init__(self, settings: LegatusSettings):
        self.settings = settings
        self._docker = docker.from_env()

    def spawn_agent(self, task: Task, role: AgentRole) -> AgentInfo:
        """Spawn an ephemeral Docker container for an agent.

        The container connects to the same Docker network as the compose
        services, mounts the workspace, and receives task/config via env vars.
        """
        agent_id = f"{role.value}_{uuid4().hex[:8]}"

        timeout = self.settings.agent.timeout
        max_turns = self.settings.agent.max_turns

        # PM and Architect agents get tighter limits — they plan, not code
        if role in (AgentRole.PM, AgentRole.ARCHITECT):
            timeout = min(timeout, 300)
            max_turns = min(max_turns, 30)

        environment = {
            "TASK_ID": task.id,
            "AGENT_ID": agent_id,
            "AGENT_ROLE": role.value,
            "REDIS_URL": self.settings.redis.url,
            "MEM0_URL": self.settings.mem0.url,
            "ANTHROPIC_API_KEY": self.settings.anthropic_api_key,
            "WORKSPACE_PATH": "/workspace",
            # Claude Code headless operation
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            # Agent config from orchestrator settings
            "AGENT_TIMEOUT": str(timeout),
            "AGENT_MAX_TURNS": str(max_turns),
            "PROJECT_ID": task.project or "",
        }

        # Use host_workspace_path for Docker volume mount (must be a host path,
        # not the container-internal path). Falls back to workspace_path if unset.
        host_path = self.settings.agent.host_workspace_path or self.settings.workspace_path
        volumes = {
            host_path: {
                "bind": "/workspace",
                "mode": "rw",
            }
        }

        # Validate the Docker network exists
        network_name = self._resolve_network()

        logger.info(
            "Spawning %s agent container: image=%s, agent=%s, task=%s",
            role.value,
            self.settings.agent.image,
            agent_id,
            task.id,
        )

        container = self._docker.containers.run(
            image=self.settings.agent.image,
            name=f"legatus-agent-{agent_id}",
            environment=environment,
            volumes=volumes,
            network=network_name,
            detach=True,
            auto_remove=False,
        )

        return AgentInfo(
            id=agent_id,
            role=role,
            status=AgentStatus.STARTING,
            container_id=container.id,
            task_id=task.id,
            started_at=datetime.now(UTC),
        )

    def spawn_dev_agent(self, task: Task) -> AgentInfo:
        """Convenience wrapper for spawning a dev agent."""
        return self.spawn_agent(task, AgentRole.DEV)

    def _resolve_network(self) -> str:
        """Validate the configured network exists, with fallback discovery."""
        name = self.settings.agent.network
        try:
            self._docker.networks.get(name)
            return name
        except docker.errors.NotFound:
            pass

        # Try to discover a matching network
        networks = self._docker.networks.list(names=["legatus"])
        if networks:
            discovered = networks[0].name
            logger.warning(
                "Configured network %s not found, using discovered network: %s",
                name,
                discovered,
            )
            return discovered

        available = [n.name for n in self._docker.networks.list()]
        raise RuntimeError(
            f"Docker network '{name}' not found. "
            f"Is Docker Compose running? Available networks: {available}"
        )

    def stop_agent(self, agent_info: AgentInfo) -> None:
        """Stop and remove an agent container."""
        if not agent_info.container_id:
            return
        try:
            container = self._docker.containers.get(agent_info.container_id)
            container.stop(timeout=10)
            container.remove()
        except docker.errors.NotFound:
            logger.debug("Container %s already removed", agent_info.container_id)

    def collect_logs_and_remove(self, container_id: str) -> str | None:
        """Collect logs from a stopped container, then remove it."""
        try:
            container = self._docker.containers.get(container_id)
            logs = container.logs(stdout=True, stderr=True).decode("utf-8", errors="replace")
            container.remove(force=True)
            return logs
        except docker.errors.NotFound:
            return None

    def get_container_status(self, container_id: str) -> str | None:
        """Check if a container is still running."""
        try:
            container = self._docker.containers.get(container_id)
            return container.status
        except docker.errors.NotFound:
            return None
