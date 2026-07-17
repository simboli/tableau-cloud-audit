"""metadata module: GraphQL pages landed via cursor, resume from raw, scrub gate."""

import json
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from tca.modules.base import RunContext
from tca.modules.metadata import MetadataModule
from tca.pseudo.manifest import GRAPHQL_MANIFEST, GraphqlQuerySpec
from tca.pseudo.scrubber import Scrubber, ScrubError
from tca.sources.metadata import PAGE_SIZE, MetadataApi
from tca.sources.rest import TableauRest
from tca.storage.writer import PackageStore
from tca.transport.auth import Credentials
from tca.transport.client import RestClient, TransportError

BASE = "https://test-pod.online.tableau.com"
GRAPHQL = f"{BASE}/api/metadata/graphql"

DATASOURCES = "graphql:datasources"
WORKBOOKS = "graphql:workbooks"


def ds_page(has_next: bool, cursor: str | None, name: str = "Sales Model") -> dict[str, Any]:
    return {
        "data": {
            "publishedDatasourcesConnection": {
                "totalCount": 2,
                "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                "nodes": [
                    {
                        "id": "node-ds-1",
                        "luid": "ds-luid-1",
                        "name": name,
                        "projectName": "Finance",
                        "hasExtracts": True,
                        "isCertified": False,
                        "fields": [
                            {
                                "id": "f-1",
                                "name": "Net Revenue",
                                "__typename": "CalculatedField",
                                "formula": "[Gross] - [Discounts]",
                                "role": "MEASURE",
                                "dataType": "REAL",
                            }
                        ],
                        "upstreamTables": [
                            {
                                "name": "orders",
                                "fullName": "[dbo].[orders]",
                                "database": {"name": "erp", "connectionType": "postgres"},
                            }
                        ],
                    }
                ],
            }
        }
    }


def wb_page(has_next: bool, cursor: str | None) -> dict[str, Any]:
    return {
        "data": {
            "workbooksConnection": {
                "totalCount": 1,
                "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                "nodes": [
                    {
                        "id": "node-wb-1",
                        "luid": "wb-luid-1",
                        "name": "Sales Dashboard",
                        "projectName": "Finance",
                        "upstreamDatasources": [{"luid": "ds-luid-1", "name": "Sales Model"}],
                        "embeddedDatasources": [],
                        "sheets": [
                            {
                                "id": "sh-1",
                                "name": "Overview",
                                "worksheetFields": [],
                                "datasourceFields": [
                                    {
                                        "id": "f-1",
                                        "name": "Net Revenue",
                                        "__typename": "CalculatedField",
                                    }
                                ],
                            }
                        ],
                        "dashboards": [],
                    }
                ],
            }
        }
    }


@pytest.fixture
def connected(httpx_mock: HTTPXMock, tmp_path: Path):
    httpx_mock.add_response(
        url=f"{BASE}/api/3.4/serverinfo", json={"serverInfo": {"restApiVersion": "3.26"}}
    )
    httpx_mock.add_response(
        url=f"{BASE}/api/3.26/auth/signin",
        json={"credentials": {"token": "tok", "site": {"id": "site-1", "contentUrl": "acme"}}},
    )
    client = RestClient(
        Credentials.build(pod="test-pod", site="acme", pat_name="n", pat_secret="s")
    )
    client.connect()
    store = PackageStore(tmp_path / "pkg.duckdb").open()
    yield client, store
    store.close()
    client.close()


def make_ctx(client: RestClient, store: PackageStore, run_id: int | None = None) -> RunContext:
    if run_id is None:
        run_id = store.begin_run(modules=["metadata"])
    return RunContext(
        rest=TableauRest(client),
        store=store,
        scrubber=Scrubber(store, run_id),
        run_id=run_id,
        metadata=MetadataApi(client),
        done=store.landed_units(run_id),
    )


def test_metadata_lands_cursor_paginated_pages(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    # consumed in order: datasources page 1 -> page 2, then workbooks page 1
    httpx_mock.add_response(url=GRAPHQL, json=ds_page(True, "cur-1"))
    httpx_mock.add_response(url=GRAPHQL, json=ds_page(False, None, name="HR Model"))
    httpx_mock.add_response(url=GRAPHQL, json=wb_page(False, None))

    stats = MetadataModule().run(make_ctx(client, store))

    assert stats.pages == {DATASOURCES: 2, WORKBOOKS: 1}
    # the second datasources request continued from the first page's cursor
    bodies = [json.loads(r.content) for r in httpx_mock.get_requests(url=GRAPHQL)]
    assert bodies[0]["variables"] == {"first": PAGE_SIZE, "after": None}
    assert bodies[1]["variables"] == {"first": PAGE_SIZE, "after": "cur-1"}
    # formulas land verbatim: content, not personal data (maintainer decision)
    raw = store.con.execute(
        "SELECT payload FROM raw.api_responses WHERE endpoint = ? AND page = 1", [DATASOURCES]
    ).fetchone()[0]
    assert "[Gross] - [Discounts]" in raw


def test_resume_continues_from_landed_cursor(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    run_id = store.begin_run(modules=["metadata"])
    first_ctx = make_ctx(client, store, run_id=run_id)
    first_ctx.land(DATASOURCES, ds_page(True, "cur-1"), page=1)

    # the resumed run must ask ONLY for datasources page 2 and workbooks
    httpx_mock.add_response(url=GRAPHQL, json=ds_page(False, None, name="HR Model"))
    httpx_mock.add_response(url=GRAPHQL, json=wb_page(False, None))

    stats = MetadataModule().run(make_ctx(client, store, run_id=run_id))

    assert stats.pages == {DATASOURCES: 1, WORKBOOKS: 1}
    assert stats.skipped == 1
    bodies = [json.loads(r.content) for r in httpx_mock.get_requests(url=GRAPHQL)]
    assert bodies[0]["variables"]["after"] == "cur-1"
    pages = store.con.execute(
        "SELECT page FROM raw.api_responses WHERE endpoint = ? ORDER BY page", [DATASOURCES]
    ).fetchall()
    assert pages == [(1,), (2,)]


def test_resume_skips_completed_queries(connected) -> None:
    client, store = connected
    run_id = store.begin_run(modules=["metadata"])
    first_ctx = make_ctx(client, store, run_id=run_id)
    first_ctx.land(DATASOURCES, ds_page(False, None), page=1)
    first_ctx.land(WORKBOOKS, wb_page(False, None), page=1)

    # no HTTP mocks registered: any request would fail the test
    stats = MetadataModule().run(make_ctx(client, store, run_id=run_id))

    assert stats.pages == {}
    assert stats.skipped == 2


def test_graphql_errors_are_fatal(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    httpx_mock.add_response(
        url=GRAPHQL,
        json={"data": None, "errors": [{"message": "NODE_LIMIT_EXCEEDED"}]},
    )
    with pytest.raises(TransportError, match="NODE_LIMIT_EXCEEDED"):
        MetadataModule().run(make_ctx(client, store))


def test_metadata_api_denied_is_skipped_not_fatal(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    for _ in GRAPHQL_MANIFEST:
        httpx_mock.add_response(url=GRAPHQL, status_code=404, json={})

    stats = MetadataModule().run(make_ctx(client, store))

    assert stats.pages == {}
    assert stats.denied == [(endpoint, "metadata-api") for endpoint in GRAPHQL_MANIFEST]


def test_unregistered_graphql_endpoint_is_refused(connected) -> None:
    client, store = connected
    scrubber = Scrubber(store, store.begin_run(modules=["metadata"]))
    with pytest.raises(ScrubError, match="not registered"):
        scrubber.scrub("graphql:everything", {"data": {}})


def test_graphql_user_paths_scrub_through_the_vault(
    connected, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, store = connected
    monkeypatch.setitem(
        GRAPHQL_MANIFEST,
        "graphql:test",
        GraphqlQuerySpec(
            connection="testConnection",
            query="{}",
            user_paths=["data.testConnection.nodes[*].owner"],
        ),
    )
    payload = {
        "data": {
            "testConnection": {
                "nodes": [
                    {
                        "luid": "wb-1",
                        "owner": {
                            "luid": "aaaa-1111",
                            "username": "mario.rossi@acme.it",
                            "name": "Mario Rossi",
                            "email": "mario.rossi@acme.it",
                        },
                    }
                ]
            }
        }
    }
    scrubber = Scrubber(store, store.begin_run(modules=["metadata"]))
    clean = scrubber.scrub("graphql:test", payload)

    owner = clean["data"]["testConnection"]["nodes"][0]["owner"]
    assert owner == {"luid": "U-0001", "username": "U-0001", "name": "U-0001", "email": "U-0001"}
    vault = store.resolve("U-0001")
    assert vault["user_luid"] == "aaaa-1111"
    assert vault["name"] == "mario.rossi@acme.it"
    assert vault["full_name"] == "Mario Rossi"
    assert vault["email"] == "mario.rossi@acme.it"


def test_safety_net_blocks_email_in_graphql_payload(connected) -> None:
    client, store = connected
    scrubber = Scrubber(store, store.begin_run(modules=["metadata"]))
    payload = wb_page(False, None)
    payload["data"]["workbooksConnection"]["nodes"][0]["name"] = "Report for mario.rossi@acme.it"
    with pytest.raises(ScrubError, match="e-mail-shaped"):
        scrubber.scrub(WORKBOOKS, payload)
