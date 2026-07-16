"""automation module: refresh tasks, jobs, subscriptions — subscriber pseudonymised."""

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from tca.modules.automation import AutomationModule
from tca.modules.base import RunContext
from tca.pseudo.scrubber import Scrubber
from tca.sources.rest import TableauRest
from tca.storage.writer import PackageStore
from tca.transport.auth import Credentials
from tca.transport.client import RestClient

BASE = "https://test-pod.online.tableau.com"
SITE_LUID = "site-luid-1"
API = f"{BASE}/api/3.26/sites/{SITE_LUID}"


@pytest.fixture
def connected(httpx_mock: HTTPXMock, tmp_path: Path):
    httpx_mock.add_response(
        url=f"{BASE}/api/3.4/serverinfo", json={"serverInfo": {"restApiVersion": "3.26"}}
    )
    httpx_mock.add_response(
        url=f"{BASE}/api/3.26/auth/signin",
        json={"credentials": {"token": "tok", "site": {"id": SITE_LUID, "contentUrl": "acme"}}},
    )
    client = RestClient(
        Credentials.build(pod="test-pod", site="acme", pat_name="n", pat_secret="s")
    )
    client.connect()
    store = PackageStore(tmp_path / "pkg.duckdb").open()
    yield client, store
    store.close()
    client.close()


def test_automation_lands_all_endpoints(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    httpx_mock.add_response(
        url=f"{API}/tasks/extractRefreshes?pageSize=1000&pageNumber=1",
        json={
            "tasks": {
                "task": [
                    {
                        "extractRefresh": {
                            "id": "t-1",
                            "priority": 50,
                            "consecutiveFailedCount": 3,
                            "datasource": {"id": "ds-1"},
                            "schedule": {"frequency": "Daily"},
                        }
                    }
                ]
            }
        },
    )
    httpx_mock.add_response(
        url=f"{API}/jobs?pageSize=1000&pageNumber=1",
        json={
            "backgroundJobs": {
                "backgroundJob": [
                    {
                        "id": "j-1",
                        "jobType": "refresh_extracts",
                        "status": "Failed",
                        "createdAt": "2026-07-15T05:00:00Z",
                        "endedAt": "2026-07-15T05:02:11Z",
                    }
                ]
            },
            "pagination": {"pageNumber": "1", "pageSize": "1000", "totalAvailable": "1"},
        },
    )
    httpx_mock.add_response(
        url=f"{API}/subscriptions?pageSize=1000&pageNumber=1",
        json={
            "pagination": {"pageNumber": "1", "pageSize": "1000", "totalAvailable": "1"},
            "subscriptions": {
                "subscription": [
                    {
                        "id": "s-1",
                        "subject": "Weekly sales",
                        "content": {"id": "wb-1", "type": "Workbook"},
                        "schedule": {"name": "Weekly"},
                        "user": {"id": "aaaa-1111-2222", "name": "mario.rossi@acme.it"},
                    }
                ]
            },
        },
    )

    run_id = store.begin_run(modules=["automation"])
    ctx = RunContext(
        rest=TableauRest(client), store=store, scrubber=Scrubber(store, run_id), run_id=run_id
    )
    stats = AutomationModule().run(ctx)

    assert stats.pages == {"/tasks/extractRefreshes": 1, "/jobs": 1, "/subscriptions": 1}

    # subscriber pseudonymised, subscription metadata intact
    row = store.con.execute(
        "SELECT json_extract_string(payload, '$.subscriptions.subscription[0].user.id'), "
        "       json_extract_string(payload, '$.subscriptions.subscription[0].user.name'), "
        "       json_extract_string(payload, '$.subscriptions.subscription[0].subject') "
        "FROM raw.api_responses WHERE endpoint = '/subscriptions'"
    ).fetchone()
    assert row == ("U-0001", "U-0001", "Weekly sales")

    # task and job payloads landed untouched (no identity fields)
    failed = store.con.execute(
        "SELECT json_extract_string(payload, '$.backgroundJobs.backgroundJob[0].status') "
        "FROM raw.api_responses WHERE endpoint = '/jobs'"
    ).fetchone()[0]
    assert failed == "Failed"

    leaked = store.con.execute(
        "SELECT count(*) FROM raw.api_responses WHERE payload::VARCHAR ILIKE '%acme.it%'"
    ).fetchone()[0]
    assert leaked == 0
