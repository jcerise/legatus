from rich.console import Console
from rich.panel import Panel
from rich.table import Table

STATUS_ICONS = {
    "created": "[ ]",
    "planned": "[.]",
    "active": "[>]",
    "review": "[?]",
    "testing": "[T]",
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
    paused: bool = False,
) -> None:
    """Render the full status display with Rich panels and tables."""
    if paused:
        console.print(
            Panel(
                "[bold yellow]PAUSED[/bold yellow] — dispatch is suspended. "
                "Running agents will finish but no new tasks will be dispatched.\n"
                "Run [bold]legion resume[/bold] to restart.",
                border_style="yellow",
            )
        )
        console.print()

    # Pending checkpoints (show prominently at the top)
    if checkpoints:
        for cp in checkpoints:
            cp_id = cp.get("id", "?")
            title = cp.get("title", "Checkpoint")
            source = cp.get("source_role", "")
            description = cp.get("description", "")

            # Role badge
            role_badge = ""
            if source == "architect":
                role_badge = "[bold magenta][Architect][/bold magenta] "
            elif source == "pm":
                role_badge = "[bold blue][PM][/bold blue] "
            elif source == "reviewer":
                role_badge = "[bold green][Reviewer][/bold green] "
            elif source == "qa":
                role_badge = "[bold cyan][QA][/bold cyan] "
            elif source == "merge_conflict":
                role_badge = "[bold red][Merge][/bold red] "
            elif source == "agent_failed":
                role_badge = "[bold red][Agent][/bold red] "
            elif source == "pm_acceptance":
                role_badge = "[bold blue][PM Accept][/bold blue] "

            # Build checkpoint content
            lines = [
                f"{role_badge}[bold yellow]{title}[/bold yellow]",
            ]

            # Show description (truncated for readability)
            if description:
                desc_lines = description.strip().splitlines()
                # Show up to 20 lines of the description
                for line in desc_lines[:20]:
                    lines.append(f"  {line}")
                if len(desc_lines) > 20:
                    lines.append(f"  [dim]... ({len(desc_lines) - 20} more lines)[/dim]")

            lines.append("")
            lines.append(
                f'  [bold]legion approve[/bold]  or  [bold]legion reject {cp_id} "reason"[/bold]'
            )

            console.print(
                Panel(
                    "\n".join(lines),
                    title="Awaiting Approval",
                    title_align="left",
                    border_style="yellow",
                )
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
        # Build an id→index map for concise dependency display
        id_to_idx: dict[str, int] = {}
        subtask_idx = 0
        for t in tasks:
            if t.get("parent_id"):
                subtask_idx += 1
                id_to_idx[t["id"]] = subtask_idx

        # Map task_id → checkpoint for quick lookup
        cp_by_task: dict[str, dict] = {}
        if checkpoints:
            for cp in checkpoints:
                cp_by_task[cp.get("task_id", "")] = cp

        # Only show Deps column when any task declares dependencies
        has_deps = any(t.get("depends_on") for t in tasks)

        task_table = Table(title="Tasks", show_header=True, title_style="bold")
        task_table.add_column("ID", style="cyan")
        task_table.add_column("Title", max_width=50)
        task_table.add_column("Status")
        if has_deps:
            task_table.add_column("Deps", style="dim")
        task_table.add_column("Assigned")
        for task in tasks:
            status = task.get("status", "unknown")
            icon = STATUS_ICONS.get(status, "[ ]")
            title = (task.get("title", "") or "")[:50]
            if task.get("parent_id"):
                idx = id_to_idx.get(task["id"])
                if has_deps and idx is not None:
                    title = f"  |-- #{idx} {title}"
                else:
                    title = f"  |-- {title}"
            branch = task.get("branch_name", "")
            status_str = f"{icon} {status}"
            if branch:
                short_branch = branch.split("/")[-1] if "/" in branch else branch
                status_str += f" [dim]({short_branch})[/dim]"

            # Add failure/blocked context
            reason = _task_status_reason(task, cp_by_task)
            if reason:
                status_str += f"\n [dim]{reason}[/dim]"

            row = [
                task["id"],
                title,
                status_str,
            ]
            if has_deps:
                dep_ids = task.get("depends_on", [])
                if dep_ids:
                    dep_labels = []
                    for dep_id in dep_ids:
                        idx = id_to_idx.get(dep_id)
                        if idx is not None:
                            dep_labels.append(f"#{idx}")
                        else:
                            dep_labels.append(dep_id[:8])
                    row.append(", ".join(dep_labels))
                else:
                    row.append("-")
            row.append(task.get("assigned_to", "-") or "-")

            task_table.add_row(*row)
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


def render_history_table(
    console: Console,
    tasks: list[dict],
) -> None:
    """Render a table of completed/rejected tasks."""
    if not tasks:
        console.print("[dim]No finished tasks yet.[/dim]")
        return

    table = Table(title="Task History", show_header=True, title_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Title", max_width=40)
    table.add_column("Status")
    table.add_column("Duration", style="dim")
    table.add_column("Outcome")

    for task in tasks:
        status = task.get("status", "?")
        icon = STATUS_ICONS.get(status, "[ ]")
        title = (task.get("title") or "")[:40]

        # Compute duration from created_at to updated_at
        duration = ""
        created = task.get("created_at", "")
        updated = task.get("updated_at", "")
        if created and updated:
            try:
                from datetime import datetime

                c = datetime.fromisoformat(created)
                u = datetime.fromisoformat(updated)
                delta = u - c
                secs = int(delta.total_seconds())
                if secs >= 3600:
                    duration = f"{secs // 3600}h {(secs % 3600) // 60}m"
                elif secs >= 60:
                    duration = f"{secs // 60}m {secs % 60}s"
                else:
                    duration = f"{secs}s"
            except Exception:
                pass

        # Outcome: last meaningful history event detail
        outcome = ""
        history = task.get("history", [])
        for entry in reversed(history):
            detail = entry.get("detail", "")
            if detail:
                outcome = detail[:60]
                break

        status_str = f"{icon} {status}"
        table.add_row(task.get("id", "?"), title, status_str, duration, outcome)

    console.print(table)


_SOURCE_LABELS = {
    "reviewer": "Reviewer",
    "qa": "QA",
    "merge_conflict": "Merge conflict",
    "agent_failed": "Agent crashed",
    "pm_acceptance": "PM Acceptance",
}


def _task_status_reason(
    task: dict,
    cp_by_task: dict[str, dict],
) -> str:
    """Return a brief reason string for blocked/rejected tasks."""
    status = task.get("status", "")

    if status == "blocked":
        cp = cp_by_task.get(task.get("id", ""))
        if cp:
            source = cp.get("source_role", "")
            label = _SOURCE_LABELS.get(source, source)
            cp_title = cp.get("title", "")
            return f"{label}: {cp_title}" if label else cp_title
        return ""

    if status == "rejected":
        # Pull reason from the last history event with a useful detail
        history = task.get("history", [])
        for entry in reversed(history):
            detail = entry.get("detail", "")
            if detail and "status_change" in entry.get("event", ""):
                # Truncate long details
                if len(detail) > 80:
                    detail = detail[:77] + "..."
                return detail
        return ""

    return ""
