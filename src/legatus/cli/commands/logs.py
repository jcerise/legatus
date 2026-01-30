import os
from pathlib import Path

import httpx
import typer
from rich.console import Console

console = Console()


def _get_orchestrator_url() -> str:
    url = os.environ.get("LEGATUS_ORCHESTRATOR_URL")
    if url:
        return url

    config_path = Path(".agent-team/config.yaml")
    if config_path.exists():
        import yaml

        config = yaml.safe_load(config_path.read_text())
        url = (config.get("orchestrator") or {}).get("url")
        if url:
            return url

    return "http://localhost:8420"


def _print_log_entry(entry: dict) -> None:
    ts = entry.get("timestamp", "")
    if isinstance(ts, str) and "T" in ts:
        ts = ts.split("T")[1][:8]

    msg_type = entry.get("type", "")
    agent = entry.get("agent_id", "")
    task = entry.get("task_id", "")
    data = entry.get("data", {})
    message = data.get("message", "")

    parts = [f"[dim]{ts}[/dim]"]
    if agent:
        parts.append(f"[cyan]{agent}[/cyan]")
    if task:
        parts.append(f"[dim]({task})[/dim]")
    if message:
        parts.append(message)
    elif msg_type:
        parts.append(f"[yellow]{msg_type}[/yellow]")

    console.print(" ".join(parts))


def logs(
    limit: int = typer.Option(50, "--limit", "-n", help="Number of log entries"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
) -> None:
    """View activity logs."""
    url = _get_orchestrator_url()

    if follow:
        _follow_logs(url)
        return

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            resp = client.get("/logs/", params={"limit": limit})
            resp.raise_for_status()
            entries = resp.json()
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]")
        raise typer.Exit(code=1) from None

    if not entries:
        console.print("[dim]No log entries yet.[/dim]")
        return

    # Logs are stored newest-first, reverse for display
    for entry in reversed(entries):
        _print_log_entry(entry)


def _follow_logs(url: str) -> None:
    """Stream logs via WebSocket."""
    import asyncio

    from legatus.cli.ws_client import stream_events

    ws_url = url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"

    async def _stream() -> None:
        console.print(f"[dim]Connecting to {ws_url}...[/dim]")
        try:
            async for event in stream_events(ws_url):
                _print_log_entry(event)
        except Exception as e:
            console.print(f"[red]WebSocket error: {e}[/red]")

    import contextlib

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_stream())
