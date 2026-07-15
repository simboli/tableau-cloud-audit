"""content module: fake Tableau API -> scrubber (owners + redaction) -> package file."""

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
from tca.transport.client import RestClient

BASE = "https://test-pod.online.tableau.com"
SITE_LUID = "site-luid-1"
API = f"{BASE}/api/3.26/sites/{SITE_LUID}"

OWNER = {"id": "aaaaaaaa-1111-2222-3333-444444444444", "name": "mario.rossi@acme.it"}


def page(block: str, singular: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "pagination": {"pageNumber": "1", "pageSize": "1000", "totalAvailable": str(len(items))},
        block: {singular: items},
    }


@pytest.fixture
def connected(httpx_mock: HTTPXMock, tmp_path: Path):
    httpx_mock.add_response(
        url=f"{BASE}/api/3.4/serverinfo", json={"serverInfo": {"restApiVersion": "3.26"}}
    )
    httpx_mock.add_response(
        url=f"{BASE}/api/3.26/auth/signin",
        json={"credentials": {"token": "tok", "site": {"id": SITE_LUID, "contentUrl": "acme"}}},
    )
    creds = Credentials.build(pod="test-pod", site="acme", pat_name="n", pat_secret="s")
    client = RestClient(creds)
    client.connect()
    store = PackageStore(tmp_path / "pkg.duckdb").open()
    yield client, store
    store.close()
    client.close()


def test_content_module_lands_all_endpoints(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    httpx_mock.add_response(
        url=f"{API}/projects?pageSize=1000&pageNumber=1",
        json=page("projects", "project", [{"id": "p-1", "name": "Finance", "owner": dict(OWNER)}]),
    )
    httpx_mock.add_response(
        url=f"{API}/workbooks?fields=_all_&pageSize=1000&pageNumber=1",
        json=page(
            "workbooks",
            "workbook",
            [
                {
                    "id": "wb-1",
                    "name": "Sales Dashboard",
                    "owner": dict(OWNER),
                    "project": {"id": "p-1", "name": "Finance"},
                    "size": "2048",
                }
            ],
        ),
    )
    httpx_mock.add_response(
        url=f"{API}/views?includeUsageStatistics=true&pageSize=1000&pageNumber=1",
        json=page(
            "views",
            "view",
            [
                {
                    "id": "v-1",
                    "name": "Overview",
                    "owner": dict(OWNER),
                    "usage": {"totalViewCount": "412"},
                }
            ],
        ),
    )
    httpx_mock.add_response(
        url=f"{API}/datasources?fields=_all_&pageSize=1000&pageNumber=1",
        json=page(
            "datasources",
            "datasource",
            [{"id": "ds-1", "name": "Sales DB", "owner": dict(OWNER), "isCertified": True}],
        ),
    )
    httpx_mock.add_response(
        url=f"{API}/workbooks/wb-1/connections",
        json={
            "connections": {
                "connection": [
                    {
                        "id": "c-1",
                        "type": "postgres",
                        "serverAddress": "db.acme.it",
                        "userName": "svc.tableau@acme.it",
                        "embedPassword": True,
                    }
                ]
            }
        },
    )
    httpx_mock.add_response(
        url=f"{API}/datasources/ds-1/connections",
        json={
            "connections": {
                "connection": [
                    {
                        "id": "c-2",
                        "type": "snowflake",
                        "serverAddress": "sf.acme.it",
                        "userName": "",
                    }
                ]
            }
        },
    )

    run_id = store.begin_run(modules=["content"])
    ctx = RunContext(
        rest=TableauRest(client), store=store, scrubber=Scrubber(store, run_id), run_id=run_id
    )
    stats = ContentModule().run(ctx)

    assert stats.pages == {
        "/projects": 1,
        "/workbooks": 1,
        "/views": 1,
        "/datasources": 1,
        "/workbooks/{luid}/connections": 1,
        "/datasources/{luid}/connections": 1,
    }

    # owners pseudonymised everywhere, same person -> same pseudonym
    for endpoint, json_path in [
        ("/projects", "$.projects.project[0].owner.id"),
        ("/workbooks", "$.workbooks.workbook[0].owner.id"),
        ("/views", "$.views.view[0].owner.id"),
        ("/datasources", "$.datasources.datasource[0].owner.id"),
    ]:
        value = store.con.execute(
            f"SELECT payload->>'{json_path}' FROM raw.api_responses WHERE endpoint = ?",
            [endpoint],
        ).fetchone()[0]
        assert value == "U-0001", endpoint

    # content names and non-PII fields survive
    name = store.con.execute(
        "SELECT payload->>'$.workbooks.workbook[0].name' FROM raw.api_responses "
        "WHERE endpoint = '/workbooks'"
    ).fetchone()[0]
    assert name == "Sales Dashboard"

    # credential userName redacted (presence kept), empty one untouched
    conn = store.con.execute(
        "SELECT entity_luid, payload->>'$.connections.connection[0].userName', "
        "payload->>'$.connections.connection[0].serverAddress' "
        "FROM raw.api_responses WHERE endpoint = '/workbooks/{luid}/connections'"
    ).fetchone()
    assert conn == ("wb-1", "[redacted]", "db.acme.it")

    # nothing PII-shaped anywhere in raw
    leaked = store.con.execute(
        "SELECT count(*) FROM raw.api_responses "
        "WHERE payload::VARCHAR ILIKE '%mario%' OR payload::VARCHAR ILIKE '%svc.tableau%'"
    ).fetchone()[0]
    assert leaked == 0
