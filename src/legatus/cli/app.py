import typer

from legatus.cli.commands.approve import approve, reject
from legatus.cli.commands.init import init
from legatus.cli.commands.logs import logs
from legatus.cli.commands.start import start
from legatus.cli.commands.status import status

app = typer.Typer(
    name="team",
    help="Legatus - Multi-agent software engineering orchestration",
    no_args_is_help=True,
)

app.command()(init)
app.command()(start)
app.command()(status)
app.command()(approve)
app.command()(reject)
app.command()(logs)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
