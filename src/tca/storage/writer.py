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
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from types import TracebackType
from typing import Any

import duckdb

from tca import __version__

SCHEMA_VERSION = "0.1"
_CATALOG = "pkg"


class StorageError(RuntimeError):
    """A package-file problem the user can act on (clear message, no traceback noise)."""


def _now() -> datetime:
    # Naive UTC by convention: the schema uses TIMESTAMP (not TIMESTAMPTZ) so the
    # duckdb client never needs pytz. Every timestamp in the file is UTC.
    return datetime.now(UTC).replace(tzinfo=None)


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
            escaped = self._key.replace("'", "''")
            options = f" (ENCRYPTION_KEY '{escaped}')"
        try:
            con.execute(f"ATTACH '{self.path.as_posix()}' AS {_CATALOG}{options}")
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
        sql = resources.files("tca.storage").joinpath("schema.sql").read_text(encoding="utf-8")
        self.con.execute(sql)

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

    # -- summary -----------------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        info = self.con.execute("SELECT * FROM meta.file_info").fetchone()
        counts = self.con.execute(
            """
            SELECT
              (SELECT count(*) FROM meta.collection_runs) AS runs,
              (SELECT count(*) FROM raw.api_responses)    AS response_pages,
              (SELECT count(*) FROM identity.map)         AS known_users
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
            "last_run": last,
        }
