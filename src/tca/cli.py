"""The `tca` command-line interface.

Commands: init, verify, collect, summary, runs, diagnostics, peek, resolve,
export. Secrets come from environment variables only (TCA_PAT_SECRET,
TCA_DB_KEY) — never from files, never from flags.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Iterator, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)
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
from tca.modules.activity import ActivityModule  # noqa: F401  (registers itself)
from tca.modules.automation import AutomationModule  # noqa: F401  (registers itself)
from tca.modules.content import ContentModule  # noqa: F401  (registers itself)
from tca.modules.metadata import MetadataModule  # noqa: F401  (registers itself)
from tca.modules.permissions import PermissionsModule  # noqa: F401  (registers itself)
from tca.modules.rest_core import RestCoreModule  # noqa: F401  (registers itself)
from tca.normalize import normalize_run
from tca.pseudo.scrubber import Scrubber, ScrubError, redact_pii
from tca.sources.metadata import MetadataApi
from tca.sources.rest import TableauRest
from tca.sources.vds import VizqlDataService
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
    pod = typer.prompt("Pod name (e.g. '10ax' for 10ax.online.tableau.com)")
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
            _verify_admin_insights(client)
            _verify_metadata_api(client)
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


def _verify_admin_insights(client: RestClient) -> None:
    """Admin Insights + VDS access is the #1 predicted setup failure: check it
    at verify time, not mid-run. Warns (doesn't fail) — the REST modules work
    without it, only the activity module would be skipped."""
    from tca.modules.activity import ADMIN_INSIGHTS_PROJECT, _admin_insights_luids
    from tca.pseudo.manifest import VDS_MANIFEST

    found: dict[str, str] = {}
    for _, payload in client.paginate("/datasources"):
        found.update(_admin_insights_luids(payload))
    wanted = [spec.datasource_name for spec in VDS_MANIFEST.values()]
    missing = [name for name in wanted if name not in found]
    if missing:
        console.print(
            f"[yellow]⚠ Admin Insights datasources not found: {', '.join(missing)}. "
            f"Open the '{ADMIN_INSIGHTS_PROJECT}' project once as an admin to "
            "provision them; the activity module will skip them until then.[/yellow]"
        )
        return
    try:
        VizqlDataService(client).field_captions(found[wanted[0]])
        console.print(
            f"[green]✓[/green] Admin Insights present ({len(found)} datasources), "
            "VizQL Data Service reachable"
        )
    except TransportError as exc:
        if exc.status_code in (403, 404):
            console.print(
                "[yellow]⚠ Admin Insights found but VDS access was denied — the "
                "PAT user needs query access on the Admin Insights datasources. "
                "The activity module will skip them until granted.[/yellow]"
            )
        else:
            raise


def _verify_metadata_api(client: RestClient) -> None:
    """The Metadata API powers the metadata module (lineage, formulas). On
    Tableau Cloud it is always provisioned, so a failure is a warning, not a
    hard stop — every other module works without it."""
    try:
        totals = MetadataApi(client).totals()
    except TransportError as exc:
        console.print(
            f"[yellow]⚠ Metadata API not reachable ({exc}). "
            "The metadata module will be skipped.[/yellow]"
        )
        return
    console.print(
        f"[green]✓[/green] Metadata API reachable — {totals['workbooks']} workbooks, "
        f"{totals['datasources']} published data sources indexed"
    )


# ------------------------------------------------------------------- collect


@app.command()
def collect(
    config: Path = CONFIG_OPTION,
    modules: str = typer.Option(
        "rest_core,content,automation,permissions,activity,metadata",
        "--modules",
        "-m",
        help="Comma-separated module names.",
    ),
    resume: bool = typer.Option(
        False, "--resume", help="Continue the last interrupted run instead of starting a new one."
    ),
) -> None:
    """Run a collection: fetch, pseudonymise, land into the package file.

    Every landed page commits immediately: an interrupted run (crash, Ctrl-C)
    loses nothing and continues with `tca collect --resume`.
    """
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

                done: set[tuple[str, str | None, int]] = set()
                if resume:
                    run_id_or_none = store.resumable_run()
                    if run_id_or_none is None:
                        raise _fail("Nothing to resume: no interrupted run found.")
                    run_id = run_id_or_none
                    store.reopen_run(run_id)
                    done = store.landed_units(run_id)
                    console.print(
                        f"[green]✓[/green] resuming run #{run_id} — "
                        f"{len(done)} pages already collected"
                    )
                else:
                    stale = store.abort_stale_runs()
                    if stale:
                        console.print(
                            f"[yellow]⚠ {stale} interrupted run(s) marked as aborted "
                            "(use --resume next time to continue them instead).[/yellow]"
                        )
                    run_id = store.begin_run(
                        modules=[m.name for m in selected], rest_api_version=client.api_version
                    )

                progress = Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    MofNCompleteColumn(),
                    TimeRemainingColumn(),
                    console=console,
                    transient=True,
                )
                active_tracks = 0

                def track(items: Sequence[Any], label: str) -> Iterator[Any]:
                    nonlocal active_tracks
                    task_id = progress.add_task(label, total=len(items))
                    active_tracks += 1
                    try:
                        for item in items:
                            yield item
                            progress.advance(task_id)
                    finally:
                        active_tracks -= 1
                        progress.remove_task(task_id)

                def on_page(endpoint: str, page: int) -> None:
                    # per-item loops report through the progress bar instead
                    if active_tracks == 0:
                        console.print(f"  [dim]{endpoint} — page {page}[/dim]")

                ctx = modules_base.RunContext(
                    rest=TableauRest(client),
                    store=store,
                    scrubber=Scrubber(store, run_id),
                    run_id=run_id,
                    on_page=on_page,
                    track=track,
                    done=done,
                    vds=VizqlDataService(client),
                    metadata=MetadataApi(client),
                )
                all_stats: dict[str, int] = {}
                skipped = 0
                denied: list[tuple[str, str]] = []
                try:
                    with progress:
                        for module in selected:
                            console.print(f"[bold]→ module {module.name}[/bold]")
                            stats = module.run(ctx)
                            all_stats.update(stats.pages)
                            skipped += stats.skipped
                            denied.extend(stats.denied)
                except KeyboardInterrupt:
                    store.finish_run(run_id, "partial", notes="interrupted by user")
                    console.print(
                        f"\n[yellow]Interrupted. Run #{run_id} is resumable: "
                        "tca collect --resume[/yellow]"
                    )
                    raise typer.Exit(code=130) from None
                except Exception as exc:
                    store.finish_run(run_id, "partial", notes=str(exc)[:500])
                    console.print(
                        f"[yellow]Run #{run_id} stopped early and is resumable: "
                        "tca collect --resume[/yellow]"
                    )
                    raise
                console.print("[bold]→ normalizing raw pages into state tables[/bold]")
                state_counts = normalize_run(store, run_id)

                notes = None
                if denied:
                    notes = f"{len(denied)} item(s) denied (403/404), e.g. Personal Space content"
                store.finish_run(run_id, "ok", notes=notes)
                pruned = store.prune_raw(cfg.retention.raw_days, keep_run_id=run_id)
                if pruned:
                    console.print(
                        f"[dim]retention: pruned {pruned} raw page(s) from runs older "
                        f"than {cfg.retention.raw_days}d[/dim]"
                    )
                if skipped:
                    console.print(f"[dim]{skipped} already-collected pages skipped (resume)[/dim]")
                if denied:
                    console.print(
                        f"[yellow]⚠ {len(denied)} item(s) refused a permissions query "
                        "(403/404) and were skipped — typical for Personal Space "
                        "content; recorded in the run notes.[/yellow]"
                    )
                if state_counts:
                    state_table = Table(title="typed state (rebuilt from raw)")
                    state_table.add_column("table")
                    state_table.add_column("rows", justify="right")
                    for name, count in sorted(state_counts.items()):
                        state_table.add_row(name, str(count))
                    console.print(state_table)
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
            table.add_row("events in history", str(info["events"]))
            table.add_row("job runs in history", str(info["job_runs"]))
            if info["events"]:
                table.add_row(
                    "event history window", f"{info['events_from']} → {info['events_to']}"
                )
                table.add_row("coverage gaps", str(info["coverage_gaps"]))
            console.print(table)
            if info["coverage_gaps"]:
                console.print(
                    "[yellow]⚠ The event history has gaps (runs were further apart "
                    "than the retention window). Details: meta.v_event_gaps[/yellow]"
                )
            if info["last_run"] is not None:
                run = info["last_run"]
                console.print(
                    f"Last run: #{run[0]} — {run[3]} — started {run[1]} — modules {run[4]}"
                )
    except _USER_ERRORS as exc:
        raise _fail(str(exc)) from exc


_RUN_STATUS_STYLE = {
    "ok": "green",
    "partial": "yellow",
    "running": "cyan",
    "failed": "red",
    "aborted": "red",
}


def _fmt_duration(started: Any, finished: Any) -> str:
    if started is None or finished is None:
        return "—"
    seconds = (finished - started).total_seconds()
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m{secs:02d}s"


@app.command()
def runs(
    config: Path = CONFIG_OPTION,
    limit: int | None = typer.Option(
        None, "--limit", "-n", help="Show only the most recent N runs (default: all)."
    ),
    last: bool = typer.Option(False, "--last", help="Only the most recent run."),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Print only each run's status, one per line (machine-readable). "
        "Pair with --last to get the last run's outcome for scripting.",
    ),
) -> None:
    """List every collection run with its status — the run log.

    `tca runs --last -q` prints just the last run's status (ok, partial,
    failed, ...) so a scheduled pipeline can branch on it, e.g. resume a
    `partial` run instead of starting a fresh one.
    """
    try:
        cfg = Config.load(config)
        if not cfg.database_path.exists():
            raise _fail(f"No package file at {cfg.database_path} — run `tca collect` first.")
        with PackageStore(cfg.database_path, db_key()) as store:
            rows = store.runs(1 if last else limit)
            if quiet:
                for row in rows:
                    typer.echo(row[1])
                return
            if not rows:
                console.print("No collection runs yet — run `tca collect` first.")
                return
            table = Table(title="collection runs")
            table.add_column("run", justify="right")
            table.add_column("status")
            table.add_column("started")
            table.add_column("duration", justify="right")
            table.add_column("pages", justify="right")
            table.add_column("modules")
            notes: list[tuple[Any, Any]] = []
            for run_id, status, started, finished, modules, note, pages in rows:
                style = _RUN_STATUS_STYLE.get(status, "white")
                table.add_row(
                    str(run_id),
                    f"[{style}]{status}[/{style}]",
                    str(started).split(".")[0],
                    _fmt_duration(started, finished),
                    str(pages),
                    ", ".join(modules or []),
                )
                if note:
                    notes.append((run_id, note))
            console.print(table)
            for run_id, note in notes:
                console.print(f"[dim]run #{run_id}: {note}[/dim]")
    except _USER_ERRORS as exc:
        raise _fail(str(exc)) from exc


# ---------------------------------------------------------------- diagnostics


class DiagFormat(StrEnum):
    text = "text"
    markdown = "markdown"
    json = "json"


_DIAG_BANNER = "tca diagnostics — safe to share (no names, e-mails, LUIDs or site identity)"


def _fmt_size(n: int | None) -> str:
    if n is None:
        return "—"
    mb = n / (1024 * 1024)
    return f"{mb:.1f} MB" if mb >= 1 else f"{n / 1024:.0f} KB"


def _diagnostics_report(
    cfg: Config, data: dict[str, Any] | None, file_size: int | None, known: list[str]
) -> dict[str, Any]:
    """Assemble the share-safe diagnostics as a plain serializable dict. Run
    notes (the only free text) are scrubbed; site name, pod and PAT name are
    omitted by design (they identify the organisation)."""
    env: dict[str, Any] = {
        "tca": __version__,
        "python": sys.version.split()[0],
        "duckdb": duckdb.__version__,
        "platform": sys.platform,
    }
    report: dict[str, Any] = {"environment": env}
    if data is None:
        report["package_file"] = "absent — run `tca collect` first"
        report["config"] = {"retention_raw_days": cfg.retention.raw_days}
        return report

    env["schema"] = f"v{data['schema_version']}"
    env["encrypted"] = bool(data["is_encrypted"])
    runs_out: list[dict[str, Any]] = []
    for run_id, status, started, finished, modules, note, pages in data["last_runs"]:
        runs_out.append(
            {
                "run": run_id,
                "status": status,
                "duration": _fmt_duration(started, finished),
                "pages": pages,
                "modules": list(modules or []),
                "note": redact_pii(note, known) if note else None,
            }
        )
    report["scale"] = {
        "runs": data["runs"],
        "raw_pages": data["response_pages"],
        "identity_vault": data["known_users"],
        "file_size_bytes": file_size,
        "history_events": data["events"],
        "history_window": [str(data["events_from"]), str(data["events_to"])]
        if data["events"]
        else None,
        "coverage_gaps": data["coverage_gaps"],
        "job_runs": data["job_runs"],
        "state": data["state_counts"],
    }
    report["runs"] = runs_out
    report["config"] = {
        "retention_raw_days": cfg.retention.raw_days,
        "last_run_modules": runs_out[0]["modules"] if runs_out else [],
    }
    report["integrity"] = {
        "migrations": data["migrations"],
        "raw_email_hits": data["raw_email_hits"],
        "raw_luid_hits": data["raw_luid_hits"],
    }
    return report


def _integrity_mark(integrity: dict[str, Any]) -> str:
    clean = not (integrity["raw_email_hits"] or integrity["raw_luid_hits"])
    return "✓" if clean else "⚠"


def _render_diag_text(r: dict[str, Any]) -> str:
    e = r["environment"]
    env_line = f"tca {e['tca']} · python {e['python']} · duckdb {e['duckdb']} · {e['platform']}"
    if "schema" in e:
        env_line += f" · schema {e['schema']} · encrypted: {e['encrypted']}"
    lines = [_DIAG_BANNER, "", "Environment", f"  {env_line}", ""]
    if "scale" not in r:
        lines += [
            f"Package file: {r['package_file']}",
            f"Config: retention raw_days={r['config']['retention_raw_days']}",
        ]
        return "\n".join(lines)

    s = r["scale"]
    state_bits = " · ".join(f"{t.split('.')[-1]} {c}" for t, c in s["state"].items() if c)
    hist = f"  history.events {s['history_events']}"
    if s["history_window"]:
        win = s["history_window"]
        hist += f"  (window {win[0]} → {win[1]}, gaps: {s['coverage_gaps']})"
    lines += [
        "Scale profile",
        f"  runs {s['runs']} · raw pages {s['raw_pages']} · identity vault "
        f"{s['identity_vault']} · file size {_fmt_size(s['file_size_bytes'])}",
        hist,
        f"  state: {state_bits}",
        "",
        f"Runs (last {len(r['runs'])})",
    ]
    for run in r["runs"]:
        lines.append(
            f"  #{run['run']} {run['status']} {run['duration']} {run['pages']}p "
            f"[{', '.join(run['modules'])}]"
        )
        if run["note"]:
            lines.append(f"       note: {run['note']}")
    c = r["config"]
    i = r["integrity"]
    mods = ", ".join(c["last_run_modules"])
    lines += [
        "",
        "Config",
        f"  modules: {mods} · retention: raw_days={c['retention_raw_days']}",
        "  (site name, pod, PAT name omitted by design)",
        "",
        "Integrity",
        f"  migrations {i['migrations'][0]} … {i['migrations'][-1]}",
        f"  raw PII scan: {i['raw_email_hits']} e-mail-shaped, {i['raw_luid_hits']} known LUIDs  "
        f"{_integrity_mark(i)}",
    ]
    return "\n".join(lines)


def _render_diag_markdown(r: dict[str, Any]) -> str:
    e = r["environment"]
    env_line = f"tca {e['tca']} · python {e['python']} · duckdb {e['duckdb']} · {e['platform']}"
    if "schema" in e:
        env_line += f" · schema {e['schema']} · encrypted: {e['encrypted']}"
    lines = [
        "## tca diagnostics",
        "_Safe to share — no names, e-mails, LUIDs or site identity._",
        "",
        f"**Environment:** {env_line}",
    ]
    if "scale" not in r:
        lines += [
            "",
            f"**Package file:** {r['package_file']}",
            f"**Config:** retention raw_days={r['config']['retention_raw_days']}",
        ]
        return "\n".join(lines)

    s = r["scale"]
    state_bits = " · ".join(f"{t.split('.')[-1]} {c}" for t, c in s["state"].items() if c)
    window = (
        f" (window {s['history_window'][0]} → {s['history_window'][1]}, gaps: {s['coverage_gaps']})"
        if s["history_window"]
        else ""
    )
    c = r["config"]
    i = r["integrity"]
    lines += [
        "",
        "**Scale profile**",
        f"- runs: {s['runs']} · raw pages: {s['raw_pages']} · identity vault: "
        f"{s['identity_vault']} · file size: {_fmt_size(s['file_size_bytes'])}",
        f"- history.events: {s['history_events']}{window}",
        f"- state: {state_bits}",
        "",
        "**Runs**",
        "",
        "| run | status | duration | pages | modules |",
        "| --- | --- | --- | --- | --- |",
    ]
    for run in r["runs"]:
        note = f" — {run['note']}" if run["note"] else ""
        lines.append(
            f"| {run['run']} | {run['status']}{note} | {run['duration']} | "
            f"{run['pages']} | {', '.join(run['modules'])} |"
        )
    mods = ", ".join(c["last_run_modules"])
    scan = f"{i['raw_email_hits']} e-mails, {i['raw_luid_hits']} LUIDs {_integrity_mark(i)}"
    lines += [
        "",
        f"**Config:** modules {mods} · retention raw_days={c['retention_raw_days']} "
        "(site/pod/PAT omitted)",
        "",
        f"**Integrity:** migrations {i['migrations'][0]}…{i['migrations'][-1]} · "
        f"raw PII scan {scan}",
    ]
    return "\n".join(lines)


@app.command()
def diagnostics(
    config: Path = CONFIG_OPTION,
    fmt: DiagFormat = typer.Option(
        DiagFormat.text, "--format", "-f", help="Output format: text | markdown | json."
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Write to a file instead of stdout."
    ),
) -> None:
    """Print a PII-free health & scale report, safe to paste into a bug report.

    Reads the package file only (no network): versions, run history, per-table
    row counts and integrity signals — never names, e-mails, LUIDs or your site
    identity. Run notes are scrubbed and the whole report passes a final PII
    self-gate before it is emitted. `--format markdown` is ready for a GitHub
    issue.
    """
    try:
        cfg = Config.load(config)
        data: dict[str, Any] | None = None
        file_size: int | None = None
        known: list[str] = []
        if cfg.database_path.exists():
            file_size = cfg.database_path.stat().st_size
            with PackageStore(cfg.database_path, db_key()) as store:
                data = store.diagnostics()
                known = list(store.known_luids())
        report = _diagnostics_report(cfg, data, file_size, known)
        if fmt is DiagFormat.json:
            text = json.dumps(report, indent=2, default=str)
        elif fmt is DiagFormat.markdown:
            text = _render_diag_markdown(report)
        else:
            text = _render_diag_text(report)
        text = redact_pii(text, known)  # final self-gate: never emit PII, whatever the source
        if output is not None:
            output.write_text(text + "\n", encoding="utf-8")
            console.print(f"[green]✓[/green] wrote diagnostics to {output}")
        else:
            typer.echo(text)
    except _USER_ERRORS as exc:
        raise _fail(str(exc)) from exc


# -------------------------------------------------------------------- export


@app.command()
def export(
    output: Path | None = typer.Argument(
        None, help="Destination file (default: <database>-export.duckdb)."
    ),
    config: Path = CONFIG_OPTION,
    force: bool = typer.Option(False, "--force", help="Overwrite an existing export."),
) -> None:
    """Produce the shareable copy: everything EXCEPT the identity vault.

    The export contains pseudonyms only, is unencrypted (inspectable by
    anyone), and is verified before completion. This is the only file that
    may leave this machine.
    """
    try:
        cfg = Config.load(config)
        if not cfg.database_path.exists():
            raise _fail(f"No package file at {cfg.database_path} — run `tca collect` first.")
        dest = output or cfg.database_path.with_name(cfg.database_path.stem + "-export.duckdb")
        if dest.exists():
            if not force:
                raise _fail(f"'{dest}' already exists. Use --force to overwrite.")
            dest.unlink()

        with PackageStore(cfg.database_path, db_key()) as store:
            counts = store.export_redacted(dest)

        digest = hashlib.sha256(dest.read_bytes()).hexdigest()
        checksum_path = Path(f"{dest}.sha256")
        checksum_path.write_text(f"{digest}  {dest.name}\n", encoding="utf-8")

        table = Table(title=f"exported to {dest.name}")
        table.add_column("table")
        table.add_column("rows", justify="right")
        for name, rows in counts.items():
            table.add_row(name, str(rows))
        console.print(table)
        console.print("[green]✓[/green] identity vault NOT exported — pseudonyms only")
        console.print("[green]✓[/green] verified: no e-mail-shaped strings in the export")
        console.print(f"[green]✓[/green] SHA-256 written to {checksum_path.name}:\n  {digest}")
        console.print(
            "[dim]The export is unencrypted plain DuckDB — open it with any DuckDB "
            "client to inspect exactly what would be shared.[/dim]"
        )
    except _USER_ERRORS as exc:
        raise _fail(str(exc)) from exc


# ---------------------------------------------------------------------- peek

PEEK_VIEWS = {
    "users": "clear.users",
    "members": "clear.group_members",
    "content": "clear.content_owners",
    "rules": "clear.permission_rules",
}


@app.command()
def peek(
    view: str = typer.Argument(..., help=f"One of: {', '.join(PEEK_VIEWS)}."),
    config: Path = CONFIG_OPTION,
    limit: int = typer.Option(50, "--limit", "-n", help="Max rows to display."),
) -> None:
    """Browse the latest snapshot WITH real identities (local file only).

    Computed at read time by joining state with the identity vault — nothing
    clear is stored, and the export physically lacks the data to do this.
    """
    if view not in PEEK_VIEWS:
        raise _fail(f"Unknown view '{view}'. Available: {', '.join(PEEK_VIEWS)}.")
    try:
        cfg = Config.load(config)
        if not cfg.database_path.exists():
            raise _fail(f"No package file at {cfg.database_path} — run `tca collect` first.")
        with PackageStore(cfg.database_path, db_key()) as store:
            cursor = store.con.execute(f"SELECT * FROM {PEEK_VIEWS[view]} LIMIT {int(limit)}")
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
        console.print(
            "[yellow]⚠ REAL identities below — resolved locally from the identity "
            "vault. This is exactly what `tca export` can NOT reproduce.[/yellow]"
        )
        table = Table(title=f"{PEEK_VIEWS[view]} (latest collected snapshot, max {limit} rows)")
        for column in columns:
            table.add_column(column)
        for row in rows:
            table.add_row(*[str(v) if v is not None else "—" for v in row])
        console.print(table)
        if not rows:
            console.print("[dim]No rows — has a collect completed successfully?[/dim]")
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
