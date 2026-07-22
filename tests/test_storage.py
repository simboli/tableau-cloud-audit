"""Storage layer tests: schema bootstrap, run bookkeeping, identity vault, encryption."""

from pathlib import Path

import pytest

from tca.storage.writer import SCHEMA_VERSION, PackageStore, StorageError


@pytest.fixture
def store(tmp_path: Path):
    with PackageStore(tmp_path / "test.duckdb") as s:
        yield s


def test_schema_bootstrap_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "pkg.duckdb"
    with PackageStore(path):
        pass
    with PackageStore(path) as s:  # second open re-runs schema.sql
        rows = s.con.execute("SELECT schema_name FROM information_schema.schemata").fetchall()
        schemas = {r[0] for r in rows}
    assert {"meta", "raw", "identity"} <= schemas


def test_file_info_stamped_once_and_site_locked(store: PackageStore) -> None:
    store.init_file_info("luid-1", "acme", "eu-west-1a")
    store.init_file_info("luid-1", "acme", "eu-west-1a")  # same site: fine
    rows = store.con.execute("SELECT * FROM meta.file_info").fetchall()
    assert len(rows) == 1
    assert rows[0][4] == SCHEMA_VERSION
    with pytest.raises(StorageError, match="belongs to site"):
        store.init_file_info("luid-2", "other-site", None)


def test_run_lifecycle(store: PackageStore) -> None:
    run_id = store.begin_run(modules=["rest_core"], rest_api_version="3.24")
    assert run_id == 1
    store.finish_run(run_id, status="ok")
    row = store.con.execute(
        "SELECT status, finished_at, modules_run FROM meta.collection_runs WHERE run_id = ?",
        [run_id],
    ).fetchone()
    assert row[0] == "ok"
    assert row[1] is not None
    assert row[2] == ["rest_core"]


def test_write_response_lands_json(store: PackageStore) -> None:
    run_id = store.begin_run(modules=["rest_core"])
    store.write_response(run_id, "/users", {"users": {"user": [{"id": "U-0001"}]}}, page=1)
    store.write_response(run_id, "/groups/{luid}/users", {"users": {}}, page=1, entity_luid="g-1")
    got = store.con.execute(
        "SELECT payload->>'$.users.user[0].id' FROM raw.api_responses WHERE endpoint = '/users'"
    ).fetchone()
    assert got[0] == "U-0001"
    per_item = store.con.execute(
        "SELECT entity_luid FROM raw.api_responses WHERE endpoint LIKE '/groups%'"
    ).fetchone()
    assert per_item[0] == "g-1"


def test_pseudonyms_are_stable_and_sequential(store: PackageStore) -> None:
    run1 = store.begin_run(modules=["rest_core"])
    p1 = store.upsert_identity(run1, "luid-a", name="mrossi", email="mario@acme.it")
    p2 = store.upsert_identity(run1, "luid-b", name="lbianchi")
    assert (p1, p2) == ("U-0001", "U-0002")
    run2 = store.begin_run(modules=["rest_core"])
    assert store.upsert_identity(run2, "luid-a") == "U-0001"  # stable across runs
    row = store.resolve("U-0001")
    assert row["email"] == "mario@acme.it"  # coalesce kept earlier value
    assert row["first_seen_run"] == run1
    assert row["last_seen_run"] == run2
    assert store.known_luids() == {"luid-a", "luid-b"}
    assert store.resolve("U-9999") is None


def test_transaction_rolls_back_cleanly(store: PackageStore) -> None:
    run_id = store.begin_run(modules=["rest_core"])
    with pytest.raises(RuntimeError, match="boom"), store.transaction():
        store.write_response(run_id, "/users", {"x": 1})
        raise RuntimeError("boom")
    count = store.con.execute("SELECT count(*) FROM raw.api_responses").fetchone()
    assert count[0] == 0


def test_encryption_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "enc.duckdb"
    with PackageStore(path, encryption_key="s3cret") as s:
        s.init_file_info("luid-1", "acme", None)
        assert s.con.execute("SELECT is_encrypted FROM meta.file_info").fetchone()[0] is True

    with PackageStore(path, encryption_key="s3cret") as s:
        assert s.con.execute("SELECT site_name FROM meta.file_info").fetchone()[0] == "acme"

    with pytest.raises(StorageError, match="TCA_DB_KEY"):
        PackageStore(path).open()

    with pytest.raises(StorageError, match="Wrong encryption key"):
        PackageStore(path, encryption_key="nope").open()


def test_summary(store: PackageStore) -> None:
    store.init_file_info("luid-1", "acme", "eu-west-1a")
    run_id = store.begin_run(modules=["rest_core"])
    store.write_response(run_id, "/users", {"a": 1})
    store.upsert_identity(run_id, "luid-a")
    store.finish_run(run_id, "ok")
    s = store.summary()
    assert (s["runs"], s["response_pages"], s["known_users"]) == (1, 1, 1)
    assert s["last_run"][3] == "ok"


def test_runs(store: PackageStore) -> None:
    store.init_file_info("luid-1", "acme", "eu-west-1a")
    r1 = store.begin_run(modules=["rest_core"])
    store.write_response(r1, "/users", {"a": 1})
    store.finish_run(r1, "ok")
    r2 = store.begin_run(modules=["rest_core", "content"])
    store.write_response(r2, "/users", {"a": 1})
    store.write_response(r2, "/groups", {"b": 2})
    store.finish_run(r2, "partial", notes="interrupted")

    rows = store.runs()
    assert [r[0] for r in rows] == [r2, r1]  # newest first
    # columns: run_id, status, started_at, finished_at, modules_run, notes, pages
    newest = rows[0]
    assert newest[1] == "partial"
    assert newest[5] == "interrupted"
    assert newest[6] == 2  # pages landed in r2
    assert rows[1][6] == 1  # pages landed in r1
    assert [r[0] for r in store.runs(limit=1)] == [r2]
