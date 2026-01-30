from pathlib import Path

import typer
import yaml
from rich.console import Console

console = Console()


def _default_config(project_name: str) -> str:
    config = {
        "project": {
            "name": project_name,
        },
        "orchestrator": {
            "url": "http://localhost:8420",
        },
    }
    return yaml.dump(config, default_flow_style=False)


def init(
    path: Path = typer.Argument(
        Path("."),
        help="Directory to initialize (defaults to current directory)",
    ),
) -> None:
    """Initialize legatus in a project directory."""
    project_dir = path.resolve()
    legatus_dir = project_dir / ".legatus"

    if legatus_dir.exists():
        console.print("[yellow]Already initialized.[/yellow]")
        raise typer.Exit(code=1)

    if not project_dir.exists():
        project_dir.mkdir(parents=True)

    legatus_dir.mkdir()
    (legatus_dir / "config.yaml").write_text(_default_config(project_dir.name))

    console.print(f"[green]Initialized legatus in {project_dir}[/green]")
    console.print("  Created .legatus/config.yaml")
    console.print('  Run [bold]legion start "your task"[/bold] to begin')
