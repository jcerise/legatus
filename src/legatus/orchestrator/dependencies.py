from fastapi import Request

from legatus.memory.client import Mem0Client
from legatus.models.config import LegatusSettings
from legatus.orchestrator.services.event_bus import EventBus
from legatus.redis_client.client import RedisClient
from legatus.redis_client.pubsub import PubSubManager
from legatus.redis_client.state import StateStore
from legatus.redis_client.task_store import TaskStore


def get_redis(request: Request) -> RedisClient:
    return request.app.state.redis


def get_task_store(request: Request) -> TaskStore:
    return request.app.state.task_store


def get_state_store(request: Request) -> StateStore:
    return request.app.state.state_store


def get_pubsub(request: Request) -> PubSubManager:
    return request.app.state.pubsub


def get_mem0(request: Request) -> Mem0Client:
    return request.app.state.mem0


def get_settings(request: Request) -> LegatusSettings:
    return request.app.state.settings


def get_event_bus(request: Request) -> EventBus:
    return request.app.state.event_bus
