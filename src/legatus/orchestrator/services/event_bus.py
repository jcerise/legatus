import asyncio
import logging

from fastapi import WebSocket

from legatus.models.messages import Message, MessageType
from legatus.orchestrator.services.agent_spawner import AgentSpawner
from legatus.orchestrator.services.git_ops import GitOps
from legatus.orchestrator.services.task_manager import TaskManager
from legatus.redis_client.pubsub import PubSubManager
from legatus.redis_client.state import StateStore
from legatus.redis_client.task_store import TaskStore

logger = logging.getLogger(__name__)


class EventBus:
    """Listens on Redis pub/sub channels and dispatches events to handlers."""

    def __init__(
        self,
        task_store: TaskStore,
        state_store: StateStore,
        pubsub: PubSubManager,
        workspace_path: str,
        spawner: AgentSpawner,
    ):
        self.task_manager = TaskManager(task_store)
        self.state_store = state_store
        self.pubsub = pubsub
        self.git_ops = GitOps(workspace_path)
        self.spawner = spawner
        self.ws_connections: list[WebSocket] = []
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Begin listening for agent events."""
        logger.info("EventBus started, listening on %s", PubSubManager.CHANNEL_AGENT)
        try:
            async for message in self.pubsub.listen(PubSubManager.CHANNEL_AGENT):
                await self._handle_agent_message(message)
        except asyncio.CancelledError:
            logger.info("EventBus shutting down")
        except Exception:
            logger.exception("EventBus error")

    async def _handle_agent_message(self, msg: Message) -> None:
        logger.info("Received event: type=%s task=%s agent=%s", msg.type, msg.task_id, msg.agent_id)

        await self.state_store.append_log(msg.model_dump(mode="json"))

        match msg.type:
            case MessageType.TASK_COMPLETE:
                await self._on_task_complete(msg)
            case MessageType.TASK_FAILED:
                await self._on_task_failed(msg)
            case MessageType.LOG_ENTRY:
                pass  # Already logged above
            case _:
                logger.warning("Unhandled message type: %s", msg.type)

        await self._broadcast_to_ws(msg)

    async def _on_task_complete(self, msg: Message) -> None:
        if not msg.task_id:
            return

        task = await self.task_manager.on_task_complete(msg.task_id, msg.data)

        # Git commit
        commit_hash = self.git_ops.commit_changes(
            f"legatus: {task.title} ({task.id})"
        )
        if commit_hash:
            logger.info("Git commit: %s", commit_hash)

        # Clean up agent state and container
        if msg.agent_id:
            await self._cleanup_agent(msg.agent_id)

    async def _on_task_failed(self, msg: Message) -> None:
        if not msg.task_id:
            return

        error = msg.data.get("error", "Unknown error")
        await self.task_manager.on_task_failed(msg.task_id, error)

        if msg.agent_id:
            await self._cleanup_agent(msg.agent_id)

    async def _cleanup_agent(self, agent_id: str) -> None:
        """Remove agent state from Redis and clean up its Docker container."""
        agent_info = await self.state_store.get_agent_info(agent_id)
        if agent_info and agent_info.container_id:
            logs = self.spawner.collect_logs_and_remove(agent_info.container_id)
            if logs:
                logger.debug("Agent %s container logs:\n%s", agent_id, logs[-2000:])
        await self.state_store.remove_agent(agent_id)

    async def _broadcast_to_ws(self, msg: Message) -> None:
        """Forward event to all connected WebSocket clients."""
        if not self.ws_connections:
            return

        data = msg.model_dump_json()
        disconnected = []
        for ws in self.ws_connections:
            try:
                await ws.send_text(data)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            self.ws_connections.remove(ws)
