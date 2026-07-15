"""The `tca` command-line interface.

Five commands: init, verify, collect, summary, resolve. Secrets come from
environment variables only (TCA_PAT_SECRET, TCA_DB_KEY) — never from files,
never from flags.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from tca import __version__
from tca.config import (
    DB_KEY_ENV,
    DEFAULT_CONFIG_FILE,
    Config,
    ConfigError,
    db_key,
    pat_secret,
)
from tca.modules import base as modules_base
from tca.modules.rest_core import RestCoreModule  # noqa: F401  (registers itself)
from tca.pseudo.scrubber import Scrubber, ScrubError
from tca.sources.rest import TableauRest
from tca.storage.writer import PackageStore, StorageError
from tca.transport.auth import Credentials
from tca.transport.client import RestClient, TransportError

app = typer.Typer(
    name="tca",
    help="Tableau Cloud audit collector — your entire estate in one DuckDB file.",
    no_args_is_help=True,
)
console = Console()

_USER_ERRORS = (ConfigError, StorageError, TransportError, ScrubError, ValueError)

CONFIG_OPTION = typer.Option(
    Path(DEFAULT_CONFIG_FILE), "--config", "-c", help="Path to collector.toml."
)


def _fail(message: str) -> typer.Exit:
    console.print(f"[bold red]✗[/bold red] {message}")
    return typer.Exit(code=1)


def _warn_if_unencrypted(key: str | None) -> None:
    if key is None:
        console.print(
            f"[yellow]⚠ {DB_KEY_ENV} is not set — the package file will NOT be "
            "encrypted at rest.[/yellow]"
        )


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


# ---------------------------------------------------------------------- init


@app.command()
def init(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing collector.toml."),
) -> None:
    """Interactive wizard: create collector.toml and the package file."""
    config_path = Path(DEFAULT_CONFIG_FILE)
    if config_path.exists() and not force:
        raise _fail(f"'{config_path}' already exists. Use --force to overwrite.")

    console.print(Panel.fit("[bold]tableau-cloud-audit — setup[/bold]"))
    site = typer.prompt("Site name (the part after /site/ in your Tableau Cloud URL)")
    pod = typer.prompt("Pod (e.g. 'eu-west-1a', or a full https:// URL)")
    pat_name = typer.prompt("PAT name (the token's name, not its secret)", default="tca-collector")
    database = typer.prompt("Package file name", default=f"{site}.duckdb")

    config_path.write_text(
        "# tableau-cloud-audit collector configuration\n"
        "# Secrets are environment variables, never stored here:\n"
        f"#   TCA_PAT_SECRET (required) and {DB_KEY_ENV} (optional encryption).\n"
        f'site = "{site}"\n'
        f'pod = "{pod}"\n'
        f'pat_name = "{pat_name}"\n'
        f'database = "{database}"\n',
        encoding="utf-8",
    )
    console.print(f"[green]✓[/green] wrote {config_path}")

    key = db_key()
    _warn_if_unencrypted(key)
    db_path = Config.load(config_path).database_path
    if db_path.exists():
        console.print(f"[green]✓[/green] package file already exists: {db_path}")
    else:
        try:
            with PackageStore(db_path, key):
                pass
        except StorageError as exc:
            raise _fail(str(exc)) from exc
        console.print(
            f"[green]✓[/green] created {'encrypted ' if key else ''}package file: {db_path}"
        )

    console.print(
        Panel.fit(
            "Next steps:\n"
            "  1. export TCA_PAT_SECRET='<your PAT secret>'\n"
            "  2. tca verify\n"
            "  3. tca collect",
            title="ready",
        )
    )


# -------------------------------------------------------------------- verify


@app.command()
def verify(config: Path = CONFIG_OPTION) -> None:
    """Check prerequisites: config, secret, connectivity, sign-in, package file."""
    try:
        cfg = Config.load(config)
        console.print(f"[green]✓[/green] config: site '{cfg.site}', pod '{cfg.pod}'")
        secret = pat_secret()
        console.print("[green]✓[/green] TCA_PAT_SECRET is set")

        client = RestClient(Credentials.build(cfg.pod, cfg.site, cfg.pat_name, secret))
        try:
            client.connect()
            console.print(
                f"[green]✓[/green] signed in — REST API {client.api_version}, "
                f"site LUID {client.site_luid}"
            )
            client.signout()
        finally:
            client.close()

        key = db_key()
        _warn_if_unencrypted(key)
        if cfg.database_path.exists():
            with PackageStore(cfg.database_path, key) as store:
                info = store.summary()
                console.print(
                    f"[green]✓[/green] package file OK: {cfg.database_path} "
                    f"({info['runs']} runs, {info['known_users']} known users)"
                )
        else:
            console.print(
                f"[green]✓[/green] package file will be created at first collect: "
                f"{cfg.database_path}"
            )
    except _USER_ERRORS as exc:
        raise _fail(str(exc)) from exc
    console.print("[bold green]All checks passed.[/bold green]")


# ------------------------------------------------------------------- collect


@app.command()
def collect(
    config: Path = CONFIG_OPTION,
    modules: str = typer.Option(
        "rest_core", "--modules", "-m", help="Comma-separated module names."
    ),
) -> None:
    """Run a collection: fetch, pseudonymise, land into the package file."""
    started = time.monotonic()
    try:
        cfg = Config.load(config)
        secret = pat_secret()
        key = db_key()
        _warn_if_unencrypted(key)
        selected = modules_base.resolve_modules(
            [m.strip() for m in modules.split(",") if m.strip()]
        )

        client = RestClient(Credentials.build(cfg.pod, cfg.site, cfg.pat_name, secret))
        try:
            client.connect()
            assert client.site_luid is not None
            console.print(
                f"[green]✓[/green] signed in to '{cfg.site}' (REST API {client.api_version})"
            )

            with PackageStore(cfg.database_path, key) as store:
                store.init_file_info(client.site_luid, cfg.site, cfg.pod)
                run_id = store.begin_run(
                    modules=[m.name for m in selected], rest_api_version=client.api_version
                )
                ctx = modules_base.RunContext(
                    rest=TableauRest(client),
                    store=store,
                    scrubber=Scrubber(store, run_id),
                    run_id=run_id,
                    on_page=lambda endpoint, page: console.print(
                        f"  [dim]{endpoint} — page {page}[/dim]"
                    ),
                )
                all_stats: dict[str, int] = {}
                try:
                    with store.transaction():
                        for module in selected:
                            console.print(f"[bold]→ module {module.name}[/bold]")
                            stats = module.run(ctx)
                            all_stats.update(stats.pages)
                except Exception as exc:
                    store.finish_run(run_id, "failed", notes=str(exc)[:500])
                    raise
                store.finish_run(run_id, "ok")
                _print_run_report(store, run_id, all_stats, time.monotonic() - started)
        finally:
            client.signout()
            client.close()
    except _USER_ERRORS as exc:
        raise _fail(str(exc)) from exc


def _print_run_report(
    store: PackageStore, run_id: int, pages: dict[str, int], elapsed: float
) -> None:
    table = Table(title=f"run #{run_id} — completed in {elapsed:.1f}s")
    table.add_column("endpoint")
    table.add_column("pages", justify="right")
    for endpoint, count in sorted(pages.items()):
        table.add_row(endpoint, str(count))
    console.print(table)
    info = store.summary()
    console.print(
        f"Package file now holds [bold]{info['response_pages']}[/bold] response pages "
        f"across [bold]{info['runs']}[/bold] runs; [bold]{info['known_users']}[/bold] "
        "users in the identity vault."
    )
    console.print(
        "[dim]No real identities were written outside identity.map — "
        "enforced by the scrubber and its safety net.[/dim]"
    )


# ------------------------------------------------------------------- summary


@app.command()
def summary(config: Path = CONFIG_OPTION) -> None:
    """What is in the package file: file identity, runs, row counts."""
    try:
        cfg = Config.load(config)
        if not cfg.database_path.exists():
            raise _fail(f"No package file at {cfg.database_path} — run `tca collect` first.")
        with PackageStore(cfg.database_path, db_key()) as store:
            info = store.summary()
            file_info = info["file_info"]
            if file_info is None:
                console.print("Empty package file (no collection has run yet).")
                return
            console.print(
                Panel.fit(
                    f"site: [bold]{file_info[1]}[/bold] ({file_info[0]})\n"
                    f"pod: {file_info[2]}\n"
                    f"created: {file_info[3]}\n"
                    f"schema: v{file_info[4]} · encrypted: {file_info[5]}",
                    title=str(store.path.name),
                )
            )
            table = Table(title="contents")
            table.add_column("metric")
            table.add_column("value", justify="right")
            table.add_row("collection runs", str(info["runs"]))
            table.add_row("response pages", str(info["response_pages"]))
            table.add_row("users in identity vault", str(info["known_users"]))
            console.print(table)
            if info["last_run"] is not None:
                run = info["last_run"]
                console.print(
                    f"Last run: #{run[0]} — {run[3]} — started {run[1]} — modules {run[4]}"
                )
    except _USER_ERRORS as exc:
        raise _fail(str(exc)) from exc


# ------------------------------------------------------------------- resolve


@app.command()
def resolve(
    pseudonym: str = typer.Argument(..., help="A pseudonym like U-0042."),
    config: Path = CONFIG_OPTION,
) -> None:
    """Pseudonym → real identity (reads the LOCAL identity vault only)."""
    try:
        cfg = Config.load(config)
        if not cfg.database_path.exists():
            raise _fail(f"No package file at {cfg.database_path}.")
        with PackageStore(cfg.database_path, db_key()) as store:
            row = store.resolve(pseudonym)
        if row is None:
            raise _fail(f"'{pseudonym}' is not in the identity vault.")
        table = Table(title=pseudonym)
        table.add_column("field")
        table.add_column("value")
        for key_name in ("user_luid", "name", "full_name", "email", "external_auth_user_id"):
            table.add_row(key_name, str(row.get(key_name) or "—"))
        table.add_row("first/last seen run", f"{row['first_seen_run']} / {row['last_seen_run']}")
        console.print(table)
    except _USER_ERRORS as exc:
        raise _fail(str(exc)) from exc
