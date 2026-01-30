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


def approve(
    checkpoint_id: str | None = typer.Argument(
        None, help="Checkpoint ID (approves first pending if omitted)"
    ),
) -> None:
    """Approve a checkpoint."""
    url = _get_orchestrator_url()

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            if not checkpoint_id:
                resp = client.get("/checkpoints/")
                resp.raise_for_status()
                checkpoints = resp.json()
                pending = [c for c in checkpoints if c["status"] == "pending"]
                if not pending:
                    console.print("[yellow]No pending checkpoints.[/yellow]")
                    raise typer.Exit()
                checkpoint_id = pending[0]["id"]
                console.print(f"Approving: {pending[0].get('title', checkpoint_id)}")

            resp = client.post(f"/checkpoints/{checkpoint_id}/approve")
            resp.raise_for_status()
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]")
        raise typer.Exit(code=1) from None

    console.print(f"[green]Checkpoint {checkpoint_id} approved.[/green]")


def reject(
    checkpoint_id: str = typer.Argument(..., help="Checkpoint ID"),
    reason: str = typer.Argument("", help="Rejection reason"),
) -> None:
    """Reject a checkpoint with feedback."""
    url = _get_orchestrator_url()

    try:
        with httpx.Client(base_url=url, timeout=10.0) as client:
            resp = client.post(
                f"/checkpoints/{checkpoint_id}/reject",
                params={"reason": reason},
            )
            resp.raise_for_status()
    except httpx.ConnectError:
        console.print(f"[red]Cannot connect to orchestrator at {url}[/red]")
        raise typer.Exit(code=1) from None

    console.print(f"[red]Checkpoint {checkpoint_id} rejected.[/red]")
    if reason:
        console.print(f"  Reason: {reason}")
