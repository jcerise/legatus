from pathlib import Path

import httpx
import typer
from rich.console import Console

from legatus.cli.display import render_history_table

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


def history(
    limit: int = typer.Option(20, "--limit", "-n", help="Max entries to show"),
) -> None:
    """Show completed and rejected tasks."""
    url = _get_orchestrator_url()

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            tasks = client.get("/tasks/history/", params={"limit": limit}).json()
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]")
        raise typer.Exit(code=1) from None

    render_history_table(console, tasks)
