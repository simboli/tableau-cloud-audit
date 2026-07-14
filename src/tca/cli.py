"""The `tca` command-line interface.

Commands are added incrementally as the corresponding layers are built;
this module is the only entry point (`tca = "tca.cli:app"` in pyproject).
"""

import typer
from rich.console import Console

from tca import __version__

app = typer.Typer(
    name="tca",
    help="Tableau Cloud audit collector — your entire estate in one DuckDB file.",
    no_args_is_help=True,
)
console = Console()


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"tca {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show the collector version and exit.",
    ),
) -> None:
    """Tableau Cloud audit collector."""
