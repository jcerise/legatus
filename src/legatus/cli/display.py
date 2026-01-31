from rich.console import Console
from rich.table import Table

STATUS_ICONS = {
    "created": "[ ]",
    "planned": "[.]",
    "active": "[>]",
    "review": "[?]",
    "blocked": "[!]",
    "rejected": "[x]",
    "done": "[v]",
}

AGENT_STATUS_COLORS = {
    "idle": "dim",
    "starting": "yellow",
    "active": "green",
    "stopping": "yellow",
    "failed": "red",
}


def render_status_panel(
    console: Console,
    tasks: list[dict],
    agents: list[dict],
    recent_logs: list[dict],
    checkpoints: list[dict] | None = None,
) -> None:
    """Render the full status display with Rich panels and tables."""
    # Pending checkpoints (show prominently at the top)
    if checkpoints:
        for cp in checkpoints:
            cp_id = cp.get("id", "?")
            title = cp.get("title", "Checkpoint")
            console.print(
                f"[bold yellow]>>> Awaiting approval:[/bold yellow]"
                f" {title}"
            )
            console.print(
                f"    [bold]legion approve[/bold]  or"
                f"  [bold]legion reject {cp_id}"
                f' "reason"[/bold]'
            )
            console.print()

    # Agents
    if agents:
        agent_table = Table(title="Agents", show_header=True, title_style="bold")
        agent_table.add_column("ID", style="cyan")
        agent_table.add_column("Role")
        agent_table.add_column("Status")
        agent_table.add_column("Task")
        for agent in agents:
            status = agent.get("status", "unknown")
            color = AGENT_STATUS_COLORS.get(status, "white")
            agent_table.add_row(
                agent["id"],
                agent.get("role", "-"),
                f"[{color}]{status}[/{color}]",
                agent.get("task_id", "-"),
            )
        console.print(agent_table)
        console.print()

    # Tasks
    if tasks:
        task_table = Table(title="Tasks", show_header=True, title_style="bold")
        task_table.add_column("ID", style="cyan")
        task_table.add_column("Title", max_width=50)
        task_table.add_column("Status")
        task_table.add_column("Assigned")
        for task in tasks:
            status = task.get("status", "unknown")
            icon = STATUS_ICONS.get(status, "[ ]")
            title = (task.get("title", "") or "")[:50]
            if task.get("parent_id"):
                title = f"  |-- {title}"
            task_table.add_row(
                task["id"],
                title,
                f"{icon} {status}",
                task.get("assigned_to", "-") or "-",
            )
        console.print(task_table)
    else:
        console.print("[dim]No tasks yet.[/dim]")

    # Recent activity
    if recent_logs:
        console.print()
        console.print("[bold]Recent Activity[/bold]")
        for entry in recent_logs[:5]:
            ts = entry.get("timestamp", "")
            if isinstance(ts, str) and "T" in ts:
                ts = ts.split("T")[1][:8]
            data = entry.get("data", {})
            msg = data.get("message", entry.get("type", ""))
            agent = entry.get("agent_id", "")
            prefix = f"  {ts}"
            if agent:
                prefix += f" [{agent}]"
            console.print(f"{prefix} {msg}")
