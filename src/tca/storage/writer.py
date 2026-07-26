"""DuckDB package-file access layer.

The single write path to the package file. No analysis logic lives here —
only schema bootstrap, run bookkeeping, raw payload landing, and the identity
vault primitives used by the pseudonymizer.

Encryption: if an encryption key is provided, the file is opened with DuckDB
native AES encryption (``ATTACH ... (ENCRYPTION_KEY ...)``). Whether to warn
when no key is set is the CLI's job, not ours.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from types import TracebackType
from typing import Any

import duckdb

from tca import __version__

# Ordered, additive-only migrations. NEVER edit a released file: a new need
# means a new numbered entry. The last label is the current schema version.
MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("0.1", "001_init.sql"),
    ("0.2", "002_event_history.sql"),
    ("0.3", "003_typed_state.sql"),
    ("0.4", "004_clear_views.sql"),
    ("0.5", "005_job_history.sql"),
    ("0.6", "006_vds_metadata_state.sql"),
    ("0.7", "007_current_view_semantics.sql"),
    ("0.8", "008_state_current_only.sql"),
)
SCHEMA_VERSION = MIGRATIONS[-1][0]
_CATALOG = "pkg"

# TS Events caption -> history.events column. Captions MUST stay in sync with
# the curated field list in the VDS manifest (tca/pseudo/manifest.py).
TS_EVENTS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Event Id", "event_id"),
    ("Event Date", "event_date"),
    ("Event Name", "event_name"),
    ("Event Type", "event_type"),
    ("Item Id", "item_id"),
    ("Item LUID", "item_luid"),
    ("Item Type", "item_type"),
    ("Item Name", "item_name"),
    ("Project Name", "project_name"),
    ("Actor User Id", "actor_user_id"),
    ("Actor Site Role", "actor_site_role"),
    ("Actor License Role", "actor_license_role"),
    ("Item Owner Id", "item_owner_id"),
    ("Target User Id", "target_user_id"),
)

# Job Performance caption -> history.job_runs column. Same sync rule as above.
JOB_RUNS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Job ID", "job_id"),
    ("Job LUID", "job_luid"),
    ("Job Type", "job_type"),
    ("Job Result", "job_result"),
    ("Final Job Result", "final_job_result"),
    ("Was Manual Run", "was_manual_run"),
    ("Item ID", "item_id"),
    ("Item LUID", "item_luid"),
    ("Item Type", "item_type"),
    ("Item Name", "item_name"),
    ("Parent Project Name", "parent_project_name"),
    ("Schedule LUID", "schedule_luid"),
    ("Schedule Name", "schedule_name"),
    ("Created At", "created_at"),
    ("Queued At", "queued_at"),
    ("Started At", "started_at"),
    ("Completed At", "completed_at"),
    ("Job Duration", "job_duration"),
    ("Job Queued Duration", "job_queued_duration"),
    ("Job Execution Duration", "job_execution_duration"),
    ("Job Overflow Queued Duration", "job_overflow_queued_duration"),
    ("Was Overflow Queued", "was_overflow_queued"),
    ("Extract File Size", "extract_file_size"),
    ("Subscriber ID", "subscriber_id"),
    ("Owner Email", "owner_email"),
)


class StorageError(RuntimeError):
    """A package-file problem the user can act on (clear message, no traceback noise)."""


def _now() -> datetime:
    # Naive UTC by convention: the schema uses TIMESTAMP (not TIMESTAMPTZ) so the
    # duckdb client never needs pytz. Every timestamp in the file is UTC.
    return datetime.now(UTC).replace(tzinfo=None)


def _sql_literal(value: str) -> str:
    """Escape a string for safe inlining as a single-quoted SQL literal.

    ATTACH accepts no bound parameters for its path or its options, so the file
    path, the export destination and the encryption key are all inlined — each
    must be escaped. A lone apostrophe (common in macOS paths, e.g.
    ``/Users/O'Brien/...``) would otherwise break the statement or let SQL be
    injected through a crafted path.
    """
    return value.replace("'", "''")


class PackageStore:
    """Owns the connection to one package file.

    Usage::

        with PackageStore(path, encryption_key=key) as store:
            store.init_file_info(site_luid=..., site_name=..., pod=...)
            run_id = store.begin_run(modules=["rest_core"])
            ...
            store.finish_run(run_id, status="ok")
    """

    def __init__(self, path: Path | str, encryption_key: str | None = None) -> None:
        self.path = Path(path)
        self._key = encryption_key or None
        self._con: duckdb.DuckDBPyConnection | None = None

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> PackageStore:
        con = duckdb.connect()
        options = ""
        if self._key is not None:
            options = f" (ENCRYPTION_KEY '{_sql_literal(self._key)}')"
        attach_path = _sql_literal(self.path.as_posix())
        try:
            con.execute(f"ATTACH '{attach_path}' AS {_CATALOG}{options}")
        except duckdb.CatalogException as exc:
            if "without a key" in str(exc):
                raise StorageError(
                    f"'{self.path}' is encrypted but no key was provided. "
                    "Set the TCA_DB_KEY environment variable."
                ) from exc
            raise StorageError(f"Cannot open '{self.path}': {exc}") from exc
        except duckdb.InvalidInputException as exc:
            if "encryption key" in str(exc).lower():
                raise StorageError(
                    f"Wrong encryption key for '{self.path}'. Check TCA_DB_KEY."
                ) from exc
            raise StorageError(f"Cannot open '{self.path}': {exc}") from exc
        except duckdb.IOException as exc:
            if "lock" in str(exc).lower():
                raise StorageError(
                    f"'{self.path}' is in use by another process. Is another tca run in progress?"
                ) from exc
            raise StorageError(f"Cannot open '{self.path}': {exc}") from exc
        con.execute(f"USE {_CATALOG}")
        self._con = con
        self._ensure_schema()
        return self

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    def __enter__(self) -> PackageStore:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def con(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            raise StorageError("Package file is not open.")
        return self._con

    def _ensure_schema(self) -> None:
        """Bring the file up to the current schema by applying pending migrations.

        The registry (meta.schema_migrations) records what was applied, when,
        and by which collector version. Files touched by a NEWER collector are
        refused instead of silently mangled. Files created before the registry
        existed are healed forward: every migration is idempotent
        (IF NOT EXISTS), so replaying them on an already-shaped file is safe.
        """
        self.con.execute("CREATE SCHEMA IF NOT EXISTS meta")
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS meta.schema_migrations ("
            "  version VARCHAR PRIMARY KEY,"
            "  filename VARCHAR NOT NULL,"
            "  applied_at TIMESTAMP NOT NULL,"
            "  collector_version VARCHAR NOT NULL)"
        )
        applied = {
            r[0] for r in self.con.execute("SELECT version FROM meta.schema_migrations").fetchall()
        }
        known = {version for version, _ in MIGRATIONS}
        from_future = sorted(applied - known)
        if from_future:
            raise StorageError(
                f"'{self.path}' uses schema version {from_future[-1]}, written by a "
                f"newer collector than this one (which knows up to {SCHEMA_VERSION}). "
                "Update tableau-cloud-audit and retry."
            )
        migrations_dir = resources.files("tca.storage").joinpath("migrations")
        for version, filename in MIGRATIONS:
            if version in applied:
                continue
            sql = migrations_dir.joinpath(filename).read_text(encoding="utf-8")
            self.con.execute(sql)
            self.con.execute(
                "INSERT INTO meta.schema_migrations VALUES (?, ?, ?, ?)",
                [version, filename, _now(), __version__],
            )
        # keep the single-row label in sync (no-op while file_info is empty)
        self.con.execute("UPDATE meta.file_info SET schema_version = ?", [SCHEMA_VERSION])

    # -- transactions ----------------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """One transaction per run: a crash rolls back to a clean file."""
        self.con.execute("BEGIN TRANSACTION")
        try:
            yield
        except BaseException:
            self.con.execute("ROLLBACK")
            raise
        else:
            self.con.execute("COMMIT")

    # -- meta ------------------------------------------------------------------

    def init_file_info(self, site_luid: str, site_name: str, pod: str | None) -> None:
        """First open: stamp the file. Later opens: refuse a different site."""
        row = self.con.execute("SELECT site_luid, site_name FROM meta.file_info").fetchone()
        if row is None:
            self.con.execute(
                "INSERT INTO meta.file_info VALUES (?, ?, ?, ?, ?, ?)",
                [site_luid, site_name, pod, _now(), SCHEMA_VERSION, self._key is not None],
            )
        elif row[0] != site_luid:
            raise StorageError(
                f"'{self.path}' belongs to site '{row[1]}' ({row[0]}), "
                f"but this run targets '{site_name}' ({site_luid}). "
                "One package file per site — use a different database path."
            )

    def begin_run(self, modules: list[str], rest_api_version: str | None = None) -> int:
        row = self.con.execute(
            """
            INSERT INTO meta.collection_runs
              (started_at, status, collector_version, rest_api_version,
               duckdb_version, modules_run)
            VALUES (?, 'running', ?, ?, ?, ?)
            RETURNING run_id
            """,
            [_now(), __version__, rest_api_version, duckdb.__version__, modules],
        ).fetchone()
        assert row is not None
        return int(row[0])

    def finish_run(self, run_id: int, status: str, notes: str | None = None) -> None:
        self.con.execute(
            "UPDATE meta.collection_runs SET finished_at = ?, status = ?, notes = ? "
            "WHERE run_id = ?",
            [_now(), status, notes, run_id],
        )

    # -- resume support -------------------------------------------------------

    def resumable_run(self) -> int | None:
        """The most recent interrupted run (crashed or Ctrl-C'd), if any."""
        row = self.con.execute(
            "SELECT max(run_id) FROM meta.collection_runs WHERE status IN ('running', 'partial')"
        ).fetchone()
        return int(row[0]) if row is not None and row[0] is not None else None

    def reopen_run(self, run_id: int) -> None:
        self.con.execute(
            "UPDATE meta.collection_runs SET status = 'running', finished_at = NULL "
            "WHERE run_id = ?",
            [run_id],
        )

    def abort_stale_runs(self) -> int:
        """Mark leftover 'running'/'partial' runs as aborted (a NEW run supersedes them)."""
        before = self.con.execute(
            "SELECT count(*) FROM meta.collection_runs WHERE status IN ('running', 'partial')"
        ).fetchone()
        self.con.execute(
            "UPDATE meta.collection_runs SET status = 'aborted', "
            "finished_at = coalesce(finished_at, ?) "
            "WHERE status IN ('running', 'partial')",
            [_now()],
        )
        assert before is not None
        return int(before[0])

    def landed_units(self, run_id: int) -> set[tuple[str, str | None, int]]:
        """Everything already collected in a run — the checkpoint IS raw itself."""
        rows = self.con.execute(
            "SELECT endpoint, entity_luid, page FROM raw.api_responses WHERE run_id = ?",
            [run_id],
        ).fetchall()
        return {(r[0], r[1], int(r[2])) for r in rows}

    # -- raw ---------------------------------------------------------------------

    def write_response(
        self,
        run_id: int,
        endpoint: str,
        payload: dict[str, Any],
        page: int = 1,
        entity_luid: str | None = None,
    ) -> None:
        """Land one (already pseudonymised) API response page."""
        self.con.execute(
            "INSERT INTO raw.api_responses VALUES (?, ?, ?, ?, ?, ?)",
            [run_id, endpoint, entity_luid, page, _now(), json.dumps(payload)],
        )

    def get_response(
        self, run_id: int, endpoint: str, entity_luid: str | None = None, page: int = 1
    ) -> dict[str, Any] | None:
        """Read back a landed (already sanitized) page — used on resume so the
        history layer can be rebuilt from raw without refetching."""
        row = self.con.execute(
            "SELECT payload FROM raw.api_responses "
            "WHERE run_id = ? AND endpoint = ? AND page = ? "
            "AND coalesce(entity_luid, '') = coalesce(?, '')",
            [run_id, endpoint, page, entity_luid],
        ).fetchone()
        return json.loads(row[0]) if row is not None else None

    # -- event history --------------------------------------------------------

    def insert_events(self, run_id: int, rows: list[dict[str, Any]]) -> int:
        """Dedup-append TS Events rows (INSERT OR IGNORE on event_id).

        Returns the number of NEW events; re-inserting known ones is a no-op,
        so this is safe to call on every run and on resume.
        """
        return self._insert_history("history.events", TS_EVENTS_COLUMNS, "Event Id", run_id, rows)

    def insert_job_runs(self, run_id: int, rows: list[dict[str, Any]]) -> int:
        """Dedup-append Job Performance rows (INSERT OR IGNORE on job_id)."""
        return self._insert_history("history.job_runs", JOB_RUNS_COLUMNS, "Job ID", run_id, rows)

    def _insert_history(
        self,
        table: str,
        columns: tuple[tuple[str, str], ...],
        key_caption: str,
        run_id: int,
        rows: list[dict[str, Any]],
    ) -> int:
        params: list[list[Any]] = []
        for row in rows:
            if row.get(key_caption) is None:
                raise StorageError(
                    f"A row for {table} has no '{key_caption}' — cannot deduplicate. "
                    "Field captions may have drifted; check the VDS manifest."
                )
            params.append([row.get(caption) for caption, _ in columns] + [run_id])
        if not params:
            return 0
        before = self._row_count(table)
        names = ", ".join(column for _, column in columns) + ", first_seen_run"
        placeholders = ", ".join("?" for _ in range(len(columns) + 1))
        self.con.executemany(
            f"INSERT OR IGNORE INTO {table} ({names}) VALUES ({placeholders})", params
        )
        return self._row_count(table) - before

    def _row_count(self, table: str) -> int:
        row = self.con.execute(f"SELECT count(*) FROM {table}").fetchone()
        assert row is not None
        return int(row[0])

    def record_coverage(self, run_id: int, source: str, start: Any, end: Any) -> None:
        """Idempotent per (run_id, source): resume replaces instead of duplicating."""
        self.con.execute(
            "DELETE FROM meta.event_coverage WHERE run_id = ? AND source = ?", [run_id, source]
        )
        self.con.execute(
            "INSERT INTO meta.event_coverage VALUES (?, ?, ?, ?)", [run_id, source, start, end]
        )

    # -- identity vault -----------------------------------------------------------

    def upsert_identity(
        self,
        run_id: int,
        user_luid: str,
        name: str | None = None,
        full_name: str | None = None,
        email: str | None = None,
        external_auth_user_id: str | None = None,
    ) -> str:
        """Return the stable pseudonym for a user LUID, creating it on first sight.

        Identity attributes are kept fresh (latest non-null wins) but the
        pseudonym never changes once assigned.
        """
        row = self.con.execute(
            "SELECT pseudonym FROM identity.map WHERE user_luid = ?", [user_luid]
        ).fetchone()
        if row is not None:
            self.con.execute(
                """
                UPDATE identity.map SET
                  name = coalesce(?, name),
                  full_name = coalesce(?, full_name),
                  email = coalesce(?, email),
                  external_auth_user_id = coalesce(?, external_auth_user_id),
                  last_seen_run = ?
                WHERE user_luid = ?
                """,
                [name, full_name, email, external_auth_user_id, run_id, user_luid],
            )
            return str(row[0])

        seq = self.con.execute("SELECT nextval('identity.pseudo_seq')").fetchone()
        assert seq is not None
        pseudonym = f"U-{int(seq[0]):04d}"
        self.con.execute(
            "INSERT INTO identity.map VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [pseudonym, user_luid, name, full_name, email, external_auth_user_id, run_id, run_id],
        )
        return pseudonym

    def resolve(self, pseudonym: str) -> dict[str, Any] | None:
        """Pseudonym -> identity. The only read path for real identities."""
        cur = self.con.execute("SELECT * FROM identity.map WHERE pseudonym = ?", [pseudonym])
        row = cur.fetchone()
        if row is None:
            return None
        columns = [d[0] for d in cur.description]
        return dict(zip(columns, row, strict=True))

    def known_luids(self) -> set[str]:
        """All user LUIDs ever seen — the safety net checks payloads against these."""
        rows = self.con.execute("SELECT user_luid FROM identity.map").fetchall()
        return {r[0] for r in rows}

    def pseudonym_by_email(self, email: str) -> str | None:
        """Reverse vault lookup (case-insensitive) for sources that carry only
        an e-mail (Tokens, Job Performance). Matches on email OR username —
        Tableau Cloud usernames usually ARE the e-mail address."""
        row = self.con.execute(
            "SELECT pseudonym FROM identity.map "
            "WHERE lower(email) = lower(?) OR lower(name) = lower(?)",
            [email, email],
        ).fetchone()
        return str(row[0]) if row is not None else None

    # -- redacted export -----------------------------------------------------------

    # Convenience views recreated inside the export (views are not tables, so
    # the table copy alone would drop them). ONLY views that touch no identity
    # data belong here — the clear.* views must never appear. Keep definitions
    # in sync with the migrations (002/003/008). Order matters: dependencies
    # first. State is current-only (migration 008), so v_*_current read state
    # directly and no longer need meta.v_endpoint_latest_run.
    EXPORT_SAFE_VIEWS: tuple[tuple[str, str], ...] = (
        (
            "meta.v_latest_run",
            "SELECT max(run_id) AS run_id FROM meta.collection_runs WHERE status = 'ok'",
        ),
        (
            "meta.v_event_gaps",
            """
            WITH w AS (
              SELECT source, window_start, window_end,
                     lag(window_end) OVER (PARTITION BY source ORDER BY window_start) AS prev_end
              FROM meta.event_coverage)
            SELECT source, prev_end AS gap_start, window_start AS gap_end,
                   date_diff('day', prev_end, window_start) AS gap_days
            FROM w WHERE prev_end IS NOT NULL AND window_start > prev_end
            """,
        ),
        ("state.v_users_current", "SELECT * FROM state.users"),
        ("state.v_groups_current", "SELECT * FROM state.groups"),
        ("state.v_content_current", "SELECT * FROM state.content_items"),
        ("state.v_permission_rules_current", "SELECT * FROM state.permission_rules"),
    )

    def _assert_export_table_clean(self, schema: str, table: str) -> None:
        """Abort if an exported table holds an e-mail-shaped string or a known
        user LUID. ``to_json(t)`` serialises the whole row — every column, nested
        JSON payloads included — so the scan covers all fields, not just raw."""
        qualified = f'exp."{schema}"."{table}" t'
        emails = self.con.execute(
            f"SELECT count(*) FROM {qualified} "
            r"WHERE regexp_matches(to_json(t)::VARCHAR, "
            r"'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')"
        ).fetchone()
        assert emails is not None
        if int(emails[0]) > 0:
            raise StorageError(
                f"Export verification failed: {emails[0]} row(s) in "
                f"'{schema}.{table}' contain an e-mail-shaped string. "
                "The export was NOT produced."
            )
        # UUID-shaped guard: a degenerate vault token (e.g. "NA") can never make
        # this substring scan pathological — matches the safety net.
        luids = self.con.execute(
            f"SELECT count(*) FROM {qualified} WHERE EXISTS ("
            "  SELECT 1 FROM identity.map i "
            "  WHERE length(i.user_luid) = 36 AND contains(to_json(t)::VARCHAR, i.user_luid))"
        ).fetchone()
        assert luids is not None
        if int(luids[0]) > 0:
            raise StorageError(
                f"Export verification failed: {luids[0]} row(s) in "
                f"'{schema}.{table}' contain a known user LUID. "
                "The export was NOT produced."
            )

    def export_redacted(self, dest: Path) -> dict[str, int]:
        """Copy every schema EXCEPT ``identity`` into a new, unencrypted file.

        This is the only artifact allowed to leave the client machine: it holds
        pseudonyms only, is plain DuckDB (inspectable by anyone), and is
        verified before returning — an e-mail-shaped string OR a known user LUID
        anywhere in the exported raw payloads aborts the export and deletes the
        file (the same two checks the write-time safety net runs, re-applied at
        the trust boundary).
        """
        if dest.exists():
            raise StorageError(f"'{dest}' already exists — refusing to overwrite.")
        self.con.execute(f"ATTACH '{_sql_literal(dest.as_posix())}' AS exp")
        try:
            tables = self.con.execute(
                f"""
                SELECT schema_name, table_name FROM duckdb_tables()
                WHERE database_name = '{_CATALOG}' AND schema_name <> 'identity'
                ORDER BY schema_name, table_name
                """
            ).fetchall()
            counts: dict[str, int] = {}
            for schema, table in tables:
                self.con.execute(f'CREATE SCHEMA IF NOT EXISTS exp."{schema}"')
                self.con.execute(
                    f'CREATE TABLE exp."{schema}"."{table}" AS '
                    f'SELECT * FROM {_CATALOG}."{schema}"."{table}"'
                )
                row = self.con.execute(f'SELECT count(*) FROM exp."{schema}"."{table}"').fetchone()
                assert row is not None
                counts[f"{schema}.{table}"] = int(row[0])

            # the copy describes itself: it is not encrypted
            self.con.execute("UPDATE exp.meta.file_info SET is_encrypted = false")

            # verification: no identity schema, and no e-mail-shaped strings or
            # known user LUIDs anywhere in the exported data — the same leak
            # checks the write-time safety net runs, re-applied at the trust
            # boundary. Scanned over EVERY exported table, not just raw:
            # history.* rows outlive their source raw pages once retention
            # pruning runs, and meta.collection_runs.notes is free text, so a
            # leak confined to those would slip past a raw-only scan.
            leftover = self.con.execute(
                "SELECT count(*) FROM duckdb_tables() "
                "WHERE database_name = 'exp' AND schema_name = 'identity'"
            ).fetchone()
            assert leftover is not None and int(leftover[0]) == 0
            for schema, table in tables:
                self._assert_export_table_clean(schema, table)
        except BaseException:
            self.con.execute("DETACH exp")
            dest.unlink(missing_ok=True)
            raise
        self.con.execute("DETACH exp")
        # Recreate the identity-free convenience views with a direct connection
        # to the export, so their definitions bind inside the export's own
        # catalog (a catalog-qualified definition would break on standalone open).
        export_con = duckdb.connect(str(dest))
        try:
            for view_name, view_sql in self.EXPORT_SAFE_VIEWS:
                export_con.execute(f"CREATE OR REPLACE VIEW {view_name} AS {view_sql}")
        finally:
            export_con.close()
        return counts

    # -- summary -----------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        info = self.con.execute("SELECT * FROM meta.file_info").fetchone()
        counts = self.con.execute(
            """
            SELECT
              (SELECT count(*) FROM meta.collection_runs) AS runs,
              (SELECT count(*) FROM raw.api_responses)    AS response_pages,
              (SELECT count(*) FROM identity.map)         AS known_users,
              (SELECT count(*) FROM history.events)       AS events,
              (SELECT min(event_date) FROM history.events) AS events_from,
              (SELECT max(event_date) FROM history.events) AS events_to,
              (SELECT count(*) FROM meta.v_event_gaps)    AS coverage_gaps,
              (SELECT count(*) FROM history.job_runs)     AS job_runs
            """
        ).fetchone()
        assert counts is not None
        last = self.con.execute(
            "SELECT run_id, started_at, finished_at, status, modules_run "
            "FROM meta.collection_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        return {
            "file_info": info,
            "runs": counts[0],
            "response_pages": counts[1],
            "known_users": counts[2],
            "events": counts[3],
            "events_from": counts[4],
            "events_to": counts[5],
            "coverage_gaps": counts[6],
            "job_runs": counts[7],
            "last_run": last,
        }

    def runs(self, limit: int | None = None) -> list[tuple[Any, ...]]:
        """Every collection run, newest first: run_id, status, started_at,
        finished_at, modules_run, notes, and the number of pages it landed."""
        sql = (
            "SELECT c.run_id, c.status, c.started_at, c.finished_at, "
            "c.modules_run, c.notes, "
            "(SELECT count(*) FROM raw.api_responses r WHERE r.run_id = c.run_id) AS pages "
            "FROM meta.collection_runs c ORDER BY c.run_id DESC"
        )
        if limit is not None:
            return self.con.execute(sql + " LIMIT ?", [limit]).fetchall()
        return self.con.execute(sql).fetchall()

    def prune_raw(self, retain_days: int, keep_run_id: int) -> int:
        """Delete raw pages of runs older than ``retain_days`` days, reclaiming
        disk. ``keep_run_id`` (the just-finished run) is always kept. Returns the
        number of pages deleted; a no-op when ``retain_days <= 0``.

        Safe because state is current-only and no view depends on old raw: raw is
        pruned as a pure archive-retention step, then a CHECKPOINT reclaims the
        bytes (DuckDB does not shrink the file on DELETE alone). The lightweight
        run bookkeeping (meta.collection_runs, event_coverage) is left intact, so
        a pruned run stays visible in the run log as metadata only.
        """
        if retain_days <= 0:
            return 0
        cutoff = _now() - timedelta(days=retain_days)
        victims = "SELECT run_id FROM meta.collection_runs WHERE run_id <> ? AND started_at < ?"
        deleted = self.con.execute(
            f"SELECT count(*) FROM raw.api_responses WHERE run_id IN ({victims})",
            [keep_run_id, cutoff],
        ).fetchone()
        assert deleted is not None
        if deleted[0]:
            self.con.execute(
                f"DELETE FROM raw.api_responses WHERE run_id IN ({victims})",
                [keep_run_id, cutoff],
            )
            self.con.execute("CHECKPOINT")
        return int(deleted[0])

    def diagnostics(self, run_limit: int = 5) -> dict[str, Any]:
        """Offline, share-safe facts about this package file for ``tca
        diagnostics``: schema, a scale profile (per-table row counts), applied
        migrations, an independent PII self-scan over raw, and the last few runs.

        Everything here is aggregate; the only free text is the runs' notes,
        which the caller redacts before display.
        """
        info = self.con.execute("SELECT * FROM meta.file_info").fetchone()
        counts = self.con.execute(
            """
            SELECT
              (SELECT count(*) FROM meta.collection_runs)  AS runs,
              (SELECT count(*) FROM raw.api_responses)     AS response_pages,
              (SELECT count(*) FROM identity.map)          AS known_users,
              (SELECT count(*) FROM history.events)        AS events,
              (SELECT min(event_date) FROM history.events) AS events_from,
              (SELECT max(event_date) FROM history.events) AS events_to,
              (SELECT count(*) FROM meta.v_event_gaps)     AS coverage_gaps,
              (SELECT count(*) FROM history.job_runs)      AS job_runs
            """
        ).fetchone()
        assert counts is not None
        state_tables = [
            r[0]
            for r in self.con.execute(
                "SELECT table_name FROM duckdb_tables() "
                "WHERE database_name = ? AND schema_name = 'state' ORDER BY table_name",
                [_CATALOG],
            ).fetchall()
        ]
        state_counts: dict[str, int] = {}
        for table in state_tables:
            row = self.con.execute(f'SELECT count(*) FROM state."{table}"').fetchone()
            assert row is not None
            state_counts[table] = int(row[0])
        migrations = [
            r[0]
            for r in self.con.execute(
                "SELECT version FROM meta.schema_migrations ORDER BY version"
            ).fetchall()
        ]
        # independent PII self-scan over raw — doubles as an integrity signal
        email_hits = self.con.execute(
            r"SELECT count(*) FROM raw.api_responses "
            r"WHERE regexp_matches(payload::VARCHAR, "
            r"'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')"
        ).fetchone()
        luid_hits = self.con.execute(
            "SELECT count(*) FROM raw.api_responses r WHERE EXISTS ("
            "  SELECT 1 FROM identity.map i "
            "  WHERE length(i.user_luid) = 36 AND contains(r.payload::VARCHAR, i.user_luid))"
        ).fetchone()
        assert email_hits is not None and luid_hits is not None
        return {
            "schema_version": info[4] if info else None,
            "is_encrypted": info[5] if info else None,
            "runs": counts[0],
            "response_pages": counts[1],
            "known_users": counts[2],
            "events": counts[3],
            "events_from": counts[4],
            "events_to": counts[5],
            "coverage_gaps": counts[6],
            "job_runs": counts[7],
            "state_counts": state_counts,
            "migrations": migrations,
            "raw_email_hits": int(email_hits[0]),
            "raw_luid_hits": int(luid_hits[0]),
            "last_runs": self.runs(run_limit),
        }
