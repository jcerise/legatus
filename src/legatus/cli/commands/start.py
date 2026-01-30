from pathlib import Path

import httpx
import typer
from rich.console import Console

console = Console()

DEFAULT_URL = "http://localhost:8420"


def _load_config() -> dict:
    """Load .legatus/config.yaml if it exists."""
    config_path = Path(".legatus/config.yaml")
    if config_path.exists():
        import yaml

        return yaml.safe_load(config_path.read_text()) or {}
    return {}


def _get_orchestrator_url() -> str:
    """Discover orchestrator URL from env, config, or default."""
    import os

    url = os.environ.get("LEGATUS_ORCHESTRATOR_URL")
    if url:
        return url

    config = _load_config()
    url = (config.get("orchestrator") or {}).get("url")
    if url:
        return url

    return DEFAULT_URL


def _get_project_name() -> str | None:
    """Read project name from .legatus/config.yaml."""
    config = _load_config()
    return (config.get("project") or {}).get("name")


def start(
    prompt: str = typer.Argument(..., help="Task description or prompt"),
    spec: Path | None = typer.Option(None, "--spec", "-s", help="Read prompt from a spec file"),
) -> None:
    """Start a new task."""
    if spec and spec.exists():
        prompt = spec.read_text()

    url = _get_orchestrator_url()
    project = _get_project_name()
    console.print("[bold]Starting task...[/bold]")

    payload: dict = {"prompt": prompt}
    if project:
        payload["project"] = project

    try:
        with httpx.Client(base_url=url, timeout=30.0) as client:
            response = client.post("/tasks/", json=payload)
            response.raise_for_status()
            task = response.json()
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]")
        console.print("  Is the orchestrator running? Try: [bold]make up[/bold]")
        raise typer.Exit(code=1) from None
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error: {e.response.status_code} {e.response.text}[/red]")
        raise typer.Exit(code=1) from None

    console.print(f"[green]Task created:[/green] {task['id']}")
    console.print(f"  Title: {task.get('title', 'Processing...')}")
    console.print(f"  Status: {task['status']}")
    if task.get("assigned_to"):
        console.print(f"  Agent: {task['assigned_to']}")
    console.print()
    console.print("Run [bold]legion status[/bold] to monitor progress")
    console.print("Run [bold]legion logs[/bold] to view activity")
