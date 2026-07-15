"""activity module: VDS rows scrubbed (TS Users via vault), minimization at query time."""

import json
from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from tca.modules.activity import ActivityModule
from tca.modules.base import RunContext
from tca.pseudo.manifest import VDS_MANIFEST
from tca.pseudo.scrubber import Scrubber, ScrubError
from tca.sources.rest import TableauRest
from tca.sources.vds import VizqlDataService
from tca.storage.writer import PackageStore
from tca.transport.auth import Credentials
from tca.transport.client import RestClient

BASE = "https://test-pod.online.tableau.com"
SITE_LUID = "site-luid-1"
API = f"{BASE}/api/3.26/sites/{SITE_LUID}"
VDS = f"{BASE}/api/v1/vizql-data-service"

AI = [
    ("TS Events", "ai-events"),
    ("TS Users", "ai-users"),
    ("Site Content", "ai-content"),
]


def ds_listing() -> dict[str, Any]:
    items = [
        {"id": luid, "name": name, "project": {"id": "p-ai", "name": "Admin Insights"}}
        for name, luid in AI
    ]
    items.append({"id": "ds-normal", "name": "Sales DB", "project": {"id": "p-1", "name": "Fin"}})
    return {
        "pagination": {"pageNumber": "1", "pageSize": "1000", "totalAvailable": str(len(items))},
        "datasources": {"datasource": items},
    }


def metadata_response(captions: list[str]) -> dict[str, Any]:
    return {"data": [{"fieldCaption": c, "dataType": "STRING"} for c in captions]}


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


def make_ctx(client: RestClient, store: PackageStore) -> RunContext:
    run_id = store.begin_run(modules=["activity"])
    return RunContext(
        rest=TableauRest(client),
        store=store,
        scrubber=Scrubber(store, run_id),
        run_id=run_id,
        vds=VizqlDataService(client),
    )


def test_activity_lands_scrubbed_vds_rows(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    httpx_mock.add_response(
        url=f"{API}/datasources?fields=_all_&pageSize=1000&pageNumber=1", json=ds_listing()
    )

    for spec in VDS_MANIFEST.values():
        # metadata: expose all curated fields plus one extra we must NOT request
        httpx_mock.add_response(
            url=f"{VDS}/read-metadata",
            match_json={"datasource": {"datasourceLuid": dict(AI)[spec.datasource_name]}},
            json=metadata_response([*spec.fields, "Actor User Name", "Owner Email"]),
        )
    httpx_mock.add_response(
        url=f"{VDS}/query-datasource",
        match_json={
            "datasource": {"datasourceLuid": "ai-events"},
            "query": {
                "fields": [{"fieldCaption": c} for c in VDS_MANIFEST["vds:ts_events"].fields]
            },
        },
        json={
            "data": [
                {
                    "Event Id": 1,
                    "Event Date": "2026-07-01T10:00:00",
                    "Event Name": "access-view",
                    "Actor User Id": 42,
                }
            ]
        },
    )
    httpx_mock.add_response(
        url=f"{VDS}/query-datasource",
        match_json={
            "datasource": {"datasourceLuid": "ai-users"},
            "query": {"fields": [{"fieldCaption": c} for c in VDS_MANIFEST["vds:ts_users"].fields]},
        },
        json={
            "data": [
                {
                    "User ID": 42,
                    "User LUID": "aaaa-1111-2222-3333",
                    "User Name": "mario.rossi@acme.it",
                    "User Email": "mario.rossi@acme.it",
                    "User Friendly Name": "Mario Rossi",
                    "User Site Role": "Creator",
                    "Days Since Last Login": 12,
                }
            ]
        },
    )
    httpx_mock.add_response(
        url=f"{VDS}/query-datasource",
        match_json={
            "datasource": {"datasourceLuid": "ai-content"},
            "query": {
                "fields": [{"fieldCaption": c} for c in VDS_MANIFEST["vds:site_content"].fields]
            },
        },
        json={"data": [{"Item LUID": "wb-1", "Item Name": "Sales", "Item Type": "Workbook"}]},
    )

    ctx = make_ctx(client, store)
    stats = ActivityModule().run(ctx)

    assert stats.pages == {
        "/datasources": 1,
        "vds:ts_events": 1,
        "vds:ts_users": 1,
        "vds:site_content": 1,
    }

    # the event flowed into the deduplicated history with its provenance
    hist = store.con.execute(
        "SELECT event_id, event_name, actor_user_id, first_seen_run FROM history.events"
    ).fetchall()
    assert hist == [(1, "access-view", 42, ctx.run_id)]
    coverage = store.con.execute(
        "SELECT source FROM meta.event_coverage WHERE run_id = ?", [ctx.run_id]
    ).fetchall()
    assert coverage == [("vds:ts_events",)]

    # TS Users row fully pseudonymised, vault enriched, numeric id kept as join key
    row = json.loads(
        store.con.execute(
            "SELECT payload FROM raw.api_responses WHERE endpoint = 'vds:ts_users'"
        ).fetchone()[0]
    )["data"][0]
    assert row["User LUID"] == row["User Name"] == row["User Email"] == "U-0001"
    assert row["User Friendly Name"] == "U-0001"
    assert row["User ID"] == 42
    assert row["User Site Role"] == "Creator"
    vault = store.resolve("U-0001")
    assert vault["email"] == "mario.rossi@acme.it"
    assert vault["full_name"] == "Mario Rossi"

    # numeric actor id in events stays (join key), nothing PII-shaped in raw
    leaked = store.con.execute(
        "SELECT count(*) FROM raw.api_responses WHERE payload::VARCHAR ILIKE '%acme.it%'"
    ).fetchone()[0]
    assert leaked == 0


def test_missing_admin_insights_is_skipped_not_fatal(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    listing = {
        "pagination": {"pageNumber": "1", "pageSize": "1000", "totalAvailable": "1"},
        "datasources": {
            "datasource": [{"id": "ds-1", "name": "Sales", "project": {"name": "Fin"}}]
        },
    }
    httpx_mock.add_response(
        url=f"{API}/datasources?fields=_all_&pageSize=1000&pageNumber=1", json=listing
    )
    ctx = make_ctx(client, store)
    stats = ActivityModule().run(ctx)  # must NOT raise
    assert len(stats.denied) == 3  # all three sources unavailable
    assert "vds:ts_users" not in stats.pages


def test_vds_403_is_skipped_not_fatal(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    httpx_mock.add_response(
        url=f"{API}/datasources?fields=_all_&pageSize=1000&pageNumber=1", json=ds_listing()
    )
    for _ in range(3):
        httpx_mock.add_response(
            url=f"{VDS}/read-metadata",
            status_code=403,
            json={"error": {"code": "403", "summary": "Forbidden", "detail": "no API access"}},
        )
    ctx = make_ctx(client, store)
    stats = ActivityModule().run(ctx)
    assert len(stats.denied) == 3


def test_unregistered_vds_source_is_refused(connected) -> None:
    client, store = connected
    ctx = make_ctx(client, store)
    with pytest.raises(ScrubError, match="not registered"):
        ctx.scrubber.scrub("vds:tokens", {"data": []})


def test_vds_safety_net_blocks_leaked_email(connected) -> None:
    client, store = connected
    ctx = make_ctx(client, store)
    # site_content has no luid_column: rows pass through, but the safety net
    # must still catch an email-shaped string (e.g. surfaced in Item Name)
    with pytest.raises(ScrubError, match="e-mail-shaped"):
        ctx.scrubber.scrub(
            "vds:site_content", {"data": [{"Item Name": "report for mario.rossi@acme.it"}]}
        )
