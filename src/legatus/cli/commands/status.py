from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.live import Live

from legatus.cli.display import render_status_panel

console = Console()


def _get_orchestrator_url() -> str:
    import os

    url = os.environ.get("LEGATUS_ORCHESTRATOR_URL")
    if url:
        return url

    config_path = Path(".legatus/config.yaml")
    if config_path.exists():
        import yaml

        config = yaml.safe_load(config_path.read_text())
        url = (config.get("orchestrator") or {}).get("url")
        if url:
            return url

    return "http://localhost:8420"


def _fetch_status(url: str) -> tuple[list, list, list, list]:
    with httpx.Client(base_url=url, timeout=10.0) as client:
        tasks = client.get("/tasks/").json()
        agents = client.get("/agents/").json()
        logs = client.get("/logs/", params={"limit": 5}).json()
        checkpoints = client.get("/checkpoints/").json()
    return tasks, agents, logs, checkpoints


def status(
    watch: bool = typer.Option(False, "--watch", "-w", help="Live updating status"),
) -> None:
    """Show current state of agents and tasks."""
    url = _get_orchestrator_url()

    if watch:
        _watch_status(url)
        return

    try:
        tasks, agents, logs, checkpoints = _fetch_status(url)
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]")
        raise typer.Exit(code=1) from None

    render_status_panel(console, tasks, agents, logs, checkpoints)


def _watch_status(url: str) -> None:
    """Poll status periodically with a live display."""
    import time

    try:
        with Live(console=console, refresh_per_second=1) as live:
            while True:
                try:
                    tasks, agents, logs, checkpoints = _fetch_status(url)
                    # Re-render to a temporary console to capture output
                    from io import StringIO

                    from rich.console import Console as TmpConsole

                    buf = StringIO()
                    tmp = TmpConsole(file=buf, force_terminal=True, width=console.width)
                    render_status_panel(tmp, tasks, agents, logs, checkpoints)
                    live.update(buf.getvalue())
                except httpx.ConnectError:
                    live.update("[red]Connection lost. Retrying...[/red]")
                time.sleep(2)
    except KeyboardInterrupt:
        pass
