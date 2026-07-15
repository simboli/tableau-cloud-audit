"""Checkpoint & resume: a crash mid-run loses nothing, resume completes without duplicates."""

from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from tca.modules.base import RunContext
from tca.modules.content import ContentModule
from tca.pseudo.scrubber import Scrubber
from tca.sources.rest import TableauRest
from tca.storage.writer import PackageStore
from tca.transport.auth import Credentials
from tca.transport.client import RestClient, TransportError

BASE = "https://test-pod.online.tableau.com"
SITE_LUID = "site-luid-1"
API = f"{BASE}/api/3.26/sites/{SITE_LUID}"


def page(block: str, singular: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "pagination": {"pageNumber": "1", "pageSize": "1000", "totalAvailable": str(len(items))},
        block: {singular: items},
    }


def mock_listings(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{API}/projects?pageSize=1000&pageNumber=1", json=page("projects", "project", [])
    )
    httpx_mock.add_response(
        url=f"{API}/workbooks?fields=_all_&pageSize=1000&pageNumber=1",
        json=page(
            "workbooks", "workbook", [{"id": "wb-1", "name": "A"}, {"id": "wb-2", "name": "B"}]
        ),
    )
    httpx_mock.add_response(
        url=f"{API}/views?includeUsageStatistics=true&pageSize=1000&pageNumber=1",
        json=page("views", "view", []),
    )
    httpx_mock.add_response(
        url=f"{API}/datasources?fields=_all_&pageSize=1000&pageNumber=1",
        json=page("datasources", "datasource", []),
    )


def connections(conn_id: str) -> dict[str, Any]:
    return {"connections": {"connection": [{"id": conn_id, "type": "postgres"}]}}


@pytest.fixture
def client(httpx_mock: HTTPXMock) -> RestClient:
    httpx_mock.add_response(
        url=f"{BASE}/api/3.4/serverinfo", json={"serverInfo": {"restApiVersion": "3.26"}}
    )
    httpx_mock.add_response(
        url=f"{BASE}/api/3.26/auth/signin",
        json={"credentials": {"token": "tok", "site": {"id": SITE_LUID, "contentUrl": "acme"}}},
    )
    c = RestClient(
        Credentials.build(pod="test-pod", site="acme", pat_name="n", pat_secret="s"),
        sleep=lambda s: None,  # no real backoff waits in tests
    )
    c.connect()
    return c


def test_crash_then_resume_completes_without_duplicates(
    httpx_mock: HTTPXMock, client: RestClient, tmp_path: Path
) -> None:
    store = PackageStore(tmp_path / "pkg.duckdb").open()
    module = ContentModule()

    # ---- first attempt: wb-1 connections OK, wb-2 fails hard (5xx to exhaustion)
    mock_listings(httpx_mock)
    httpx_mock.add_response(url=f"{API}/workbooks/wb-1/connections", json=connections("c-1"))
    for _ in range(5):
        httpx_mock.add_response(url=f"{API}/workbooks/wb-2/connections", status_code=503)

    run_id = store.begin_run(modules=["content"])
    ctx = RunContext(
        rest=TableauRest(client), store=store, scrubber=Scrubber(store, run_id), run_id=run_id
    )
    with pytest.raises(TransportError):
        module.run(ctx)
    store.finish_run(run_id, "partial", notes="boom")

    landed = store.landed_units(run_id)
    assert ("/workbooks/{luid}/connections", "wb-1", 1) in landed  # progress survived
    assert len(landed) == 5  # 4 listings + wb-1 connections

    # ---- resume: only wb-2 connections is fetched again (plus cheap listings)
    assert store.resumable_run() == run_id
    store.reopen_run(run_id)
    mock_listings(httpx_mock)
    httpx_mock.add_response(url=f"{API}/workbooks/wb-2/connections", json=connections("c-2"))

    ctx2 = RunContext(
        rest=TableauRest(client),
        store=store,
        scrubber=Scrubber(store, run_id),
        run_id=run_id,
        done=store.landed_units(run_id),
    )
    stats = module.run(ctx2)
    store.finish_run(run_id, "ok")

    assert stats.skipped == 5  # everything already landed was skipped
    assert stats.pages == {"/workbooks/{luid}/connections": 1}  # only the missing piece

    # no wb-1 connections request happened during resume
    resumed_urls = [str(r.url) for r in httpx_mock.get_requests()]
    assert resumed_urls.count(f"{API}/workbooks/wb-1/connections") == 1

    # zero duplicate units in raw
    dup = store.con.execute(
        "SELECT count(*) - count(DISTINCT (endpoint, coalesce(entity_luid, ''), page)) "
        "FROM raw.api_responses WHERE run_id = ?",
        [run_id],
    ).fetchone()[0]
    assert dup == 0
    store.close()


def test_stale_runs_are_aborted_by_a_new_run(tmp_path: Path) -> None:
    store = PackageStore(tmp_path / "pkg.duckdb").open()
    old = store.begin_run(modules=["rest_core"])  # stays 'running' (simulated crash)
    assert store.resumable_run() == old
    assert store.abort_stale_runs() == 1
    assert store.resumable_run() is None
    status = store.con.execute(
        "SELECT status FROM meta.collection_runs WHERE run_id = ?", [old]
    ).fetchone()[0]
    assert status == "aborted"
    store.close()
