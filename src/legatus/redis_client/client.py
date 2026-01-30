import redis.asyncio as aioredis


class RedisClient:
    """Async Redis connection wrapper with lifecycle management."""

    def __init__(self, url: str = "redis://localhost:6379"):
        self.url = url
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        self._client = aioredis.from_url(self.url, decode_responses=True)

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> aioredis.Redis:
        if not self._client:
            raise RuntimeError("RedisClient not connected. Call connect() first.")
        return self._client
