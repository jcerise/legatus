import asyncio
from collections.abc import AsyncIterator

from legatus.models.messages import Message
from legatus.redis_client.client import RedisClient


class PubSubManager:
    """Publish/subscribe message bus over Redis."""

    CHANNEL_AGENT = "events:agent"
    CHANNEL_ORCHESTRATOR = "events:orchestrator"
    CHANNEL_CLI = "events:cli"

    def __init__(self, redis_client: RedisClient):
        self._redis = redis_client

    async def publish(self, channel: str, message: Message) -> None:
        r = self._redis.client
        data = message.model_dump_json()
        await r.publish(channel, data)

    async def listen(self, channel: str) -> AsyncIterator[Message]:
        """Async generator that yields Message objects from a channel."""
        r = self._redis.client
        pubsub = r.pubsub()
        await pubsub.subscribe(channel)
        try:
            while True:
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if msg is not None and msg["type"] == "message":
                    data = msg["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    yield Message.model_validate_json(data)
                else:
                    await asyncio.sleep(0.1)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
