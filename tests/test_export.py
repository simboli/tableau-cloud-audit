"""Redacted export: identity never leaves, verification blocks leaks, output opens plain."""

from pathlib import Path

import duckdb
import pytest

from tca.storage.writer import PackageStore, StorageError


def make_populated_store(path: Path, encryption_key: str | None = None) -> PackageStore:
    store = PackageStore(path, encryption_key).open()
    store.init_file_info("site-1", "acme", "10ax")
    run_id = store.begin_run(modules=["rest_core"])
    store.upsert_identity(run_id, "luid-a", name="mrossi", email="mario.rossi@acme.it")
    store.write_response(run_id, "/users", {"users": {"user": [{"id": "U-0001"}]}})
    store.finish_run(run_id, "ok")
    return store


def test_export_copies_everything_but_identity(tmp_path: Path) -> None:
    src = tmp_path / "pkg.duckdb"
    dest = tmp_path / "export.duckdb"
    store = make_populated_store(src, encryption_key="s3cret")
    counts = store.export_redacted(dest)
    store.close()

    assert counts == {
        "meta.collection_runs": 1,
        "meta.event_coverage": 0,
        "meta.file_info": 1,
        "meta.schema_migrations": 2,
        "history.events": 0,
        "raw.api_responses": 1,
    }

    # opens WITHOUT a key (plain duckdb), identity schema absent, flag updated
    con = duckdb.connect(str(dest), read_only=True)
    schemas = {r[0] for r in con.execute("SELECT schema_name FROM duckdb_tables()").fetchall()}
    assert "identity" not in schemas
    assert {"meta", "raw"} <= schemas
    assert con.execute("SELECT is_encrypted FROM meta.file_info").fetchone()[0] is False
    assert "mario" not in str(con.execute("SELECT * FROM raw.api_responses").fetchall())
    con.close()

    # source untouched: identity still there, still encrypted
    with PackageStore(src, encryption_key="s3cret") as reopened:
        assert reopened.resolve("U-0001")["email"] == "mario.rossi@acme.it"
        assert reopened.con.execute("SELECT is_encrypted FROM meta.file_info").fetchone()[0]


def test_export_refuses_existing_destination(tmp_path: Path) -> None:
    store = make_populated_store(tmp_path / "pkg.duckdb")
    dest = tmp_path / "export.duckdb"
    dest.touch()
    with pytest.raises(StorageError, match="refusing to overwrite"):
        store.export_redacted(dest)
    store.close()


def test_export_verification_blocks_email_leak(tmp_path: Path) -> None:
    store = make_populated_store(tmp_path / "pkg.duckdb")
    # simulate a scrubber gap by writing a payload with an email DIRECTLY
    # (bypassing the scrubber, which would have blocked it)
    run_id = store.begin_run(modules=["rest_core"])
    store.write_response(run_id, "/users", {"users": {"user": [{"email": "leak@acme.it"}]}})
    dest = tmp_path / "export.duckdb"
    with pytest.raises(StorageError, match="verification failed"):
        store.export_redacted(dest)
    assert not dest.exists()  # nothing left behind
    store.close()
