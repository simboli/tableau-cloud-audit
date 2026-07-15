"""Schema migrations: registry bookkeeping, legacy files heal forward,
files from a newer collector are refused, replays are idempotent."""

from importlib import resources
from pathlib import Path

import duckdb
import pytest

from tca.storage.writer import MIGRATIONS, SCHEMA_VERSION, PackageStore, StorageError


def test_fresh_file_records_all_migrations(tmp_path: Path) -> None:
    with PackageStore(tmp_path / "pkg.duckdb") as store:
        rows = store.con.execute(
            "SELECT version, filename, collector_version FROM meta.schema_migrations "
            "ORDER BY version"
        ).fetchall()
    assert [(r[0], r[1]) for r in rows] == list(MIGRATIONS)
    assert all(r[2] for r in rows)  # collector version recorded


def test_reopening_does_not_reapply(tmp_path: Path) -> None:
    path = tmp_path / "pkg.duckdb"
    with PackageStore(path) as store:
        first = store.con.execute(
            "SELECT version, applied_at FROM meta.schema_migrations"
        ).fetchall()
    with PackageStore(path) as store:
        second = store.con.execute(
            "SELECT version, applied_at FROM meta.schema_migrations"
        ).fetchall()
    assert first == second  # same rows, same timestamps: nothing re-ran


def test_legacy_v01_file_heals_forward(tmp_path: Path) -> None:
    """A file created before the registry existed (schema 0.1, no
    schema_migrations table) is brought to the current version on open."""
    path = tmp_path / "legacy.duckdb"
    con = duckdb.connect(str(path))
    sql = resources.files("tca.storage").joinpath("migrations/001_init.sql").read_text("utf-8")
    con.execute(sql)
    con.execute(
        "INSERT INTO meta.file_info VALUES ('site-1', 'acme', '10ax', now()::TIMESTAMP, "
        "'0.1', false)"
    )
    con.close()

    with PackageStore(path) as store:
        versions = {
            r[0] for r in store.con.execute("SELECT version FROM meta.schema_migrations").fetchall()
        }
        assert versions == {v for v, _ in MIGRATIONS}
        # history layer now exists and works
        run_id = store.begin_run(modules=["activity"])
        store.insert_events(
            run_id,
            [{"Event Id": 1, "Event Date": "2026-05-01T10:00:00"}],
        )
        # the stale label was updated
        label = store.con.execute("SELECT schema_version FROM meta.file_info").fetchone()[0]
        assert label == SCHEMA_VERSION


def test_file_from_newer_collector_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "pkg.duckdb"
    with PackageStore(path) as store:
        store.con.execute(
            "INSERT INTO meta.schema_migrations "
            "VALUES ('9.9', '099_future.sql', now()::TIMESTAMP, 'tca 9.9.9')"
        )
    with pytest.raises(StorageError, match="newer collector"):
        PackageStore(path).open()
