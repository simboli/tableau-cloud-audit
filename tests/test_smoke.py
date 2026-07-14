"""Smoke tests: the package imports and the CLI entry point responds."""

from typer.testing import CliRunner

import tca
from tca.cli import app

runner = CliRunner()


def test_version_matches_package() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert tca.__version__ in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "collector" in result.output.lower()
