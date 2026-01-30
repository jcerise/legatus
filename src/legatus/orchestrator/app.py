import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from legatus.memory.client import Mem0Client
from legatus.models.config import LegatusSettings
from legatus.orchestrator.routers.agents import router as agents_router
from legatus.orchestrator.routers.checkpoints import router as checkpoints_router
from legatus.orchestrator.routers.health import router as health_router
from legatus.orchestrator.routers.logs import router as logs_router
from legatus.orchestrator.routers.tasks import router as tasks_router
from legatus.orchestrator.services.event_bus import EventBus
from legatus.orchestrator.services.git_ops import GitOps
from legatus.orchestrator.ws import websocket_endpoint
from legatus.redis_client.client import RedisClient
from legatus.redis_client.pubsub import PubSubManager
from legatus.redis_client.state import StateStore
from legatus.redis_client.task_store import TaskStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = LegatusSettings()

    # Connect Redis
    redis = RedisClient(settings.redis.url)
    await redis.connect()
    logger.info("Connected to Redis at %s", settings.redis.url)

    # Connect Mem0
    mem0 = Mem0Client(settings.mem0.url)
    await mem0.connect()
    logger.info("Connected to Mem0 at %s", settings.mem0.url)

    # Initialize git repo in workspace
    git_ops = GitOps(settings.workspace_path)
    git_ops.init_repo()

    # Store shared state
    task_store = TaskStore(redis)
    state_store = StateStore(redis)
    pubsub = PubSubManager(redis)

    app.state.redis = redis
    app.state.task_store = task_store
    app.state.state_store = state_store
    app.state.pubsub = pubsub
    app.state.mem0 = mem0
    app.state.settings = settings

    # Start event bus
    event_bus = EventBus(
        task_store=task_store,
        state_store=state_store,
        pubsub=pubsub,
        mem0=mem0,
        workspace_path=settings.workspace_path,
    )
    app.state.event_bus = event_bus
    event_bus_task = asyncio.create_task(event_bus.start())

    yield

    # Shutdown
    import contextlib

    event_bus_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await event_bus_task
    await redis.disconnect()
    await mem0.disconnect()
    logger.info("Orchestrator shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Legatus Orchestrator",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(tasks_router)
    app.include_router(agents_router)
    app.include_router(checkpoints_router)
    app.include_router(logs_router)
    app.add_websocket_route("/ws", websocket_endpoint)

    return app


def run() -> None:
    """Entry point for the `legatus-orchestrator` console script."""
    import uvicorn

    settings = LegatusSettings()
    uvicorn.run(
        "legatus.orchestrator.app:create_app",
        factory=True,
        host=settings.orchestrator.host,
        port=settings.orchestrator.rest_port,
    )
