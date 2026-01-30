import httpx


class Mem0Client:
    """HTTP client for the Mem0 REST API server."""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self._http: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=30.0)

    async def disconnect(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None

    @property
    def http(self) -> httpx.AsyncClient:
        if not self._http:
            raise RuntimeError("Mem0Client not connected. Call connect() first.")
        return self._http

    async def add(
        self,
        text: str,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Add a memory."""
        payload: dict = {"messages": [{"role": "user", "content": text}]}
        if user_id:
            payload["user_id"] = user_id
        if agent_id:
            payload["agent_id"] = agent_id
        if metadata:
            payload["metadata"] = metadata

        resp = await self.http.post("/memories", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def search(
        self,
        query: str,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search memories by semantic similarity."""
        payload: dict = {"query": query, "limit": limit}
        if user_id:
            payload["user_id"] = user_id
        if agent_id:
            payload["agent_id"] = agent_id

        resp = await self.http.post("/search", json=payload)
        resp.raise_for_status()
        result = resp.json()
        return result.get("results", result) if isinstance(result, dict) else result

    async def list_memories(
        self,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict]:
        """List all memories for a given scope."""
        params: dict = {}
        if user_id:
            params["user_id"] = user_id
        if agent_id:
            params["agent_id"] = agent_id

        resp = await self.http.get("/memories", params=params)
        resp.raise_for_status()
        result = resp.json()
        return result.get("results", result) if isinstance(result, dict) else result

    async def delete(self, memory_id: str) -> None:
        """Delete a specific memory."""
        resp = await self.http.delete(f"/memories/{memory_id}")
        resp.raise_for_status()
