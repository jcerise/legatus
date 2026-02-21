from pathlib import Path

import httpx
import typer
from rich.console import Console

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


def pause() -> None:
    """Pause task dispatch. Running agents finish, but no new tasks are dispatched."""
    url = _get_orchestrator_url()

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            resp = client.post("/system/pause")
            resp.raise_for_status()
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]")
        raise typer.Exit(code=1) from None

    console.print("[yellow]Dispatch paused.[/yellow] Running agents will finish.")
    console.print("Run [bold]legion resume[/bold] to restart dispatch.")


def resume() -> None:
    """Resume task dispatch."""
    url = _get_orchestrator_url()

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            resp = client.post("/system/resume")
            resp.raise_for_status()
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]")
        raise typer.Exit(code=1) from None

    console.print("[green]Dispatch resumed.[/green]")
