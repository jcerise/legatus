from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

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


def _get_project_name() -> str | None:
    config_path = Path(".legatus/config.yaml")
    if config_path.exists():
        import yaml

        config = yaml.safe_load(config_path.read_text()) or {}
        return (config.get("project") or {}).get("name")
    return None


def cost() -> None:
    """Show API cost breakdown."""
    url = _get_orchestrator_url()
    project = _get_project_name()

    try:
        params = {}
        if project:
            params["project_id"] = project
        with httpx.Client(base_url=url, timeout=10.0) as client:
            data = client.get("/costs/", params=params).json()
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]")
        raise typer.Exit(code=1) from None

    total = data.get("total", 0.0)
    by_role = data.get("by_role", {})
    entries = data.get("entries", [])

    # Per-role table
    if by_role:
        table = Table(title="Cost by Role", show_header=True, title_style="bold")
        table.add_column("Role", style="cyan")
        table.add_column("Cost (USD)", justify="right")
        for role, amount in sorted(by_role.items()):
            table.add_row(role, f"${amount:.4f}")
        table.add_section()
        table.add_row("[bold]Total[/bold]", f"[bold]${total:.4f}[/bold]")
        console.print(table)
    else:
        console.print("[dim]No cost data recorded yet.[/dim]")

    # Recent entries
    if entries:
        console.print()
        detail = Table(title="Recent Entries", show_header=True, title_style="bold")
        detail.add_column("Time", style="dim")
        detail.add_column("Role")
        detail.add_column("Task", style="cyan")
        detail.add_column("Cost (USD)", justify="right")
        for entry in entries[:20]:
            ts = entry.get("timestamp", "")
            if isinstance(ts, str) and "T" in ts:
                ts = ts.split("T")[1][:8]
            detail.add_row(
                ts,
                entry.get("agent_role", "?"),
                entry.get("task_id", "?")[:16],
                f"${entry.get('cost', 0):.4f}",
            )
        console.print(detail)
