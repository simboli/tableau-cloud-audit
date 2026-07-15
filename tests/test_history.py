"""Event history: dedup across runs, coverage windows, gap detection, resume healing."""

from pathlib import Path
from typing import Any

import pytest

from tca.storage.writer import PackageStore, StorageError


def event(event_id: int, date: str, name: str = "Access View", actor: int = 42) -> dict[str, Any]:
    return {
        "Event Id": event_id,
        "Event Date": date,
        "Event Name": name,
        "Event Type": "Access",
        "Item Id": 1,
        "Item LUID": "wb-1",
        "Item Type": "Workbook",
        "Item Name": "Sales",
        "Project Name": "Finance",
        "Actor User Id": actor,
        "Actor Site Role": "Viewer",
        "Actor License Role": "Viewer",
        "Item Owner Id": 7,
        "Target User Id": None,
    }


@pytest.fixture
def store(tmp_path: Path):
    with PackageStore(tmp_path / "pkg.duckdb") as s:
        yield s


def test_events_deduplicate_across_runs(store: PackageStore) -> None:
    run1 = store.begin_run(modules=["activity"])
    inserted = store.insert_events(
        run1, [event(1, "2026-05-01T10:00:00"), event(2, "2026-05-02T11:00:00")]
    )
    assert inserted == 2

    run2 = store.begin_run(modules=["activity"])
    # overlapping window: event 2 again + new event 3
    inserted = store.insert_events(
        run2, [event(2, "2026-05-02T11:00:00"), event(3, "2026-06-20T09:00:00")]
    )
    assert inserted == 1  # only the new one

    rows = store.con.execute(
        "SELECT event_id, first_seen_run FROM history.events ORDER BY event_id"
    ).fetchall()
    assert rows == [(1, run1), (2, run1), (3, run2)]  # provenance preserved


def test_event_without_id_fails_loudly(store: PackageStore) -> None:
    run_id = store.begin_run(modules=["activity"])
    with pytest.raises(StorageError, match="Event Id"):
        store.insert_events(run_id, [{"Event Date": "2026-05-01T10:00:00"}])


def test_coverage_is_idempotent_per_run(store: PackageStore) -> None:
    run_id = store.begin_run(modules=["activity"])
    store.record_coverage(run_id, "vds:ts_events", "2026-05-01", "2026-05-30")
    store.record_coverage(run_id, "vds:ts_events", "2026-05-01", "2026-05-31")  # resume overwrite
    rows = store.con.execute("SELECT count(*) FROM meta.event_coverage").fetchone()
    assert rows[0] == 1


def test_gap_detection(store: PackageStore) -> None:
    r1 = store.begin_run(modules=["activity"])
    r2 = store.begin_run(modules=["activity"])
    store.record_coverage(r1, "vds:ts_events", "2026-01-01", "2026-03-31")
    # second window starts AFTER the first ends -> 61-day hole
    store.record_coverage(r2, "vds:ts_events", "2026-05-31", "2026-08-29")
    gaps = store.con.execute("SELECT source, gap_days FROM meta.v_event_gaps").fetchall()
    assert gaps == [("vds:ts_events", 61)]
    assert store.summary()["coverage_gaps"] == 1


def test_contiguous_windows_have_no_gap(store: PackageStore) -> None:
    r1 = store.begin_run(modules=["activity"])
    r2 = store.begin_run(modules=["activity"])
    store.record_coverage(r1, "vds:ts_events", "2026-01-01", "2026-03-31")
    store.record_coverage(r2, "vds:ts_events", "2026-03-15", "2026-06-10")  # overlap
    assert store.con.execute("SELECT count(*) FROM meta.v_event_gaps").fetchone()[0] == 0


def test_get_response_reads_back_landed_page(store: PackageStore) -> None:
    run_id = store.begin_run(modules=["activity"])
    store.write_response(
        run_id,
        "vds:ts_events",
        {"data": [event(9, "2026-07-01T08:00:00")]},
        entity_luid="ai-events",
    )
    payload = store.get_response(run_id, "vds:ts_events", entity_luid="ai-events")
    assert payload["data"][0]["Event Id"] == 9
    assert store.get_response(run_id, "vds:ts_events", entity_luid="nope") is None


def test_summary_includes_history(store: PackageStore) -> None:
    run_id = store.begin_run(modules=["activity"])
    store.insert_events(run_id, [event(1, "2026-05-01T10:00:00")])
    s = store.summary()
    assert s["events"] == 1
    assert str(s["events_from"]).startswith("2026-05-01")
