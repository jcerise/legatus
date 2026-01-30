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

    def spawn_dev_agent(self, task: Task) -> AgentInfo:
        """Spawn an ephemeral Docker container for a dev agent.

        The container connects to the same Docker network as the compose
        services, mounts the workspace, and receives task/config via env vars.
        """
        agent_id = f"dev_{uuid4().hex[:8]}"

        environment = {
            "TASK_ID": task.id,
            "AGENT_ID": agent_id,
            "AGENT_ROLE": "dev",
            "REDIS_URL": self.settings.redis.url,
            "MEM0_URL": self.settings.mem0.url,
            "ANTHROPIC_API_KEY": self.settings.anthropic_api_key,
            "WORKSPACE_PATH": "/workspace",
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

        logger.info(
            "Spawning agent container: image=%s, agent=%s, task=%s, network=%s",
            self.settings.agent.image,
            agent_id,
            task.id,
            self.settings.agent.network,
        )

        container = self._docker.containers.run(
            image=self.settings.agent.image,
            name=f"legatus-agent-{agent_id}",
            environment=environment,
            volumes=volumes,
            network=self.settings.agent.network,
            detach=True,
            auto_remove=True,
        )

        return AgentInfo(
            id=agent_id,
            role=AgentRole.DEV,
            status=AgentStatus.STARTING,
            container_id=container.id,
            task_id=task.id,
            started_at=datetime.now(UTC),
        )

    def stop_agent(self, agent_info: AgentInfo) -> None:
        """Stop and remove an agent container."""
        if not agent_info.container_id:
            return
        try:
            container = self._docker.containers.get(agent_info.container_id)
            container.stop(timeout=10)
        except docker.errors.NotFound:
            logger.debug("Container %s already removed", agent_info.container_id)

    def get_container_status(self, container_id: str) -> str | None:
        """Check if a container is still running."""
        try:
            container = self._docker.containers.get(container_id)
            return container.status
        except docker.errors.NotFound:
            return None
