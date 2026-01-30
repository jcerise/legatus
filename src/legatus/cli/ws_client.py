import json

import websockets


async def stream_events(url: str = "ws://localhost:8420/ws"):
    """Connect to the orchestrator WebSocket and yield parsed events."""
    async with websockets.connect(url) as ws:
        while True:
            data = await ws.recv()
            yield json.loads(data)
