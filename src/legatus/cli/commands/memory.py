import json
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

console = Console()
memory_app = typer.Typer(
    name="memory",
    help="Manage agent memories",
    no_args_is_help=True,
)


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


def _get_project_name() -> str | None:
    config_path = Path(".legatus/config.yaml")
    if config_path.exists():
        import yaml

        config = yaml.safe_load(config_path.read_text()) or {}
        return (config.get("project") or {}).get("name")
    return None


def _render_memories(title: str, memories: list[dict]) -> None:
    if not memories:
        console.print(f"[dim]No {title.lower()} found.[/dim]")
        return

    table = Table(title=title, show_header=True, title_style="bold")
    table.add_column("ID", style="cyan", max_width=20)
    table.add_column("Memory", max_width=60)
    table.add_column("Created", style="dim")
    for m in memories:
        mid = m.get("id", "?")[:20]
        text = m.get("memory", str(m))
        if len(text) > 60:
            text = text[:57] + "..."
        created = m.get("created_at", "")
        if isinstance(created, str) and "T" in created:
            created = created.split("T")[0]
        table.add_row(mid, text, created)
    console.print(table)


@memory_app.command("show")
def show() -> None:
    """List project and global memories."""
    url = _get_orchestrator_url()
    project = _get_project_name()

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            params: dict = {"namespace": "project"}
            if project:
                params["project_id"] = project
            project_memories = client.get("/memory/", params=params).json()

            global_memories = client.get(
                "/memory/", params={"namespace": "global"},
            ).json()
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]")
        raise typer.Exit(code=1) from None

    _render_memories("Project Memories", project_memories)
    console.print()
    _render_memories("Global Memories", global_memories)


@memory_app.command("search")
def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
) -> None:
    """Search memories by semantic similarity."""
    url = _get_orchestrator_url()
    project = _get_project_name()

    try:
        params: dict = {
            "query": query,
            "namespace": "project",
            "limit": limit,
        }
        if project:
            params["project_id"] = project
        with httpx.Client(base_url=url, timeout=10.0) as client:
            results = client.get("/memory/search", params=params).json()
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]")
        raise typer.Exit(code=1) from None

    _render_memories(f"Search: {query}", results)


@memory_app.command("forget")
def forget(
    memory_id: str = typer.Argument(..., help="Memory ID to delete"),
) -> None:
    """Delete a memory."""
    url = _get_orchestrator_url()

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            resp = client.delete(f"/memory/{memory_id}")
            resp.raise_for_status()
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]")
        raise typer.Exit(code=1) from None
    except httpx.HTTPStatusError as e:
        console.print(f"[red]Error: {e.response.status_code} {e.response.text}[/red]")
        raise typer.Exit(code=1) from None

    console.print(f"[green]Deleted memory {memory_id}[/green]")


@memory_app.command("export")
def export() -> None:
    """Dump all memories as JSON to stdout."""
    url = _get_orchestrator_url()
    project = _get_project_name()

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            params: dict = {"namespace": "project"}
            if project:
                params["project_id"] = project
            project_memories = client.get("/memory/", params=params).json()
            global_memories = client.get(
                "/memory/", params={"namespace": "global"},
            ).json()
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]", stderr=True)
        raise typer.Exit(code=1) from None

    output = {
        "project": project_memories,
        "global": global_memories,
    }
    print(json.dumps(output, indent=2))
