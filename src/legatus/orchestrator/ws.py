from fastapi import WebSocket, WebSocketDisconnect


async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for live CLI updates.

    On connect, registers in event_bus.ws_connections.
    All agent events are forwarded to connected clients.
    """
    await websocket.accept()
    event_bus = websocket.app.state.event_bus
    event_bus.ws_connections.append(websocket)
    try:
        while True:
            # Keep alive; receive any messages from CLI (future: team chat)
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in event_bus.ws_connections:
            event_bus.ws_connections.remove(websocket)
