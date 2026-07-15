"""permissions module: grantees and owners pseudonymised, group grantees in clear."""

from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from tca.modules.base import RunContext
from tca.modules.permissions import PermissionsModule
from tca.pseudo.scrubber import Scrubber
from tca.sources.rest import TableauRest
from tca.storage.writer import PackageStore
from tca.transport.auth import Credentials
from tca.transport.client import RestClient

BASE = "https://test-pod.online.tableau.com"
SITE_LUID = "site-luid-1"
API = f"{BASE}/api/3.26/sites/{SITE_LUID}"

USER_LUID = "aaaaaaaa-1111-2222-3333-444444444444"


def page(block: str, singular: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "pagination": {"pageNumber": "1", "pageSize": "1000", "totalAvailable": str(len(items))},
        block: {singular: items},
    }


def permissions_payload(parent_key: str, parent_luid: str) -> dict[str, Any]:
    return {
        "permissions": {
            parent_key: {"id": parent_luid, "owner": {"id": USER_LUID}},
            "granteeCapabilities": [
                {
                    "group": {"id": "g-all-users"},
                    "capabilities": {"capability": [{"name": "Read", "mode": "Allow"}]},
                },
                {
                    "user": {"id": USER_LUID},
                    "capabilities": {"capability": [{"name": "Write", "mode": "Allow"}]},
                },
            ],
        }
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
    client = RestClient(
        Credentials.build(pod="test-pod", site="acme", pat_name="n", pat_secret="s")
    )
    client.connect()
    store = PackageStore(tmp_path / "pkg.duckdb").open()
    yield client, store
    store.close()
    client.close()


def test_permissions_module(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    httpx_mock.add_response(
        url=f"{API}/projects?pageSize=1000&pageNumber=1",
        json=page("projects", "project", [{"id": "p-1", "name": "Finance"}]),
    )
    httpx_mock.add_response(
        url=f"{API}/workbooks?fields=_all_&pageSize=1000&pageNumber=1",
        json=page("workbooks", "workbook", [{"id": "wb-1", "name": "Sales"}]),
    )
    httpx_mock.add_response(
        url=f"{API}/datasources?fields=_all_&pageSize=1000&pageNumber=1",
        json=page("datasources", "datasource", [{"id": "ds-1", "name": "DB"}]),
    )
    httpx_mock.add_response(
        url=f"{API}/projects/p-1/permissions", json=permissions_payload("project", "p-1")
    )
    httpx_mock.add_response(
        url=f"{API}/projects/p-1/default-permissions/workbooks",
        json=permissions_payload("project", "p-1"),
    )
    httpx_mock.add_response(
        url=f"{API}/projects/p-1/default-permissions/datasources",
        json=permissions_payload("project", "p-1"),
    )
    httpx_mock.add_response(
        url=f"{API}/workbooks/wb-1/permissions", json=permissions_payload("workbook", "wb-1")
    )
    httpx_mock.add_response(
        url=f"{API}/datasources/ds-1/permissions", json=permissions_payload("datasource", "ds-1")
    )

    run_id = store.begin_run(modules=["permissions"])
    ctx = RunContext(
        rest=TableauRest(client), store=store, scrubber=Scrubber(store, run_id), run_id=run_id
    )
    stats = PermissionsModule().run(ctx)

    assert stats.pages == {
        "/projects": 1,
        "/workbooks": 1,
        "/datasources": 1,
        "/projects/{luid}/permissions": 1,
        "/projects/{luid}/default-permissions/workbooks": 1,
        "/projects/{luid}/default-permissions/datasources": 1,
        "/workbooks/{luid}/permissions": 1,
        "/datasources/{luid}/permissions": 1,
    }

    # user grantee and owner pseudonymised; group grantee and capabilities intact
    row = store.con.execute(
        "SELECT json_extract_string(payload, '$.permissions.granteeCapabilities[0].group.id'), "
        "       json_extract_string(payload, '$.permissions.granteeCapabilities[1].user.id'), "
        "       json_extract_string(payload, '$.permissions.workbook.owner.id'), "
        "       json_extract_string(payload, "
        "         '$.permissions.granteeCapabilities[0].capabilities.capability[0].name') "
        "FROM raw.api_responses WHERE endpoint = '/workbooks/{luid}/permissions'"
    ).fetchone()
    assert row == ("g-all-users", "U-0001", "U-0001", "Read")

    # the real LUID appears nowhere in raw
    leaked = store.con.execute(
        "SELECT count(*) FROM raw.api_responses WHERE payload::VARCHAR LIKE ?",
        [f"%{USER_LUID}%"],
    ).fetchone()[0]
    assert leaked == 0


def test_403_on_single_item_is_skipped_not_fatal(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    httpx_mock.add_response(
        url=f"{API}/projects?pageSize=1000&pageNumber=1", json=page("projects", "project", [])
    )
    httpx_mock.add_response(
        url=f"{API}/workbooks?fields=_all_&pageSize=1000&pageNumber=1",
        json=page(
            "workbooks",
            "workbook",
            [{"id": "wb-personal", "name": "Private"}, {"id": "wb-ok", "name": "Public"}],
        ),
    )
    httpx_mock.add_response(
        url=f"{API}/datasources?fields=_all_&pageSize=1000&pageNumber=1",
        json=page("datasources", "datasource", []),
    )
    httpx_mock.add_response(
        url=f"{API}/workbooks/wb-personal/permissions",
        status_code=403,
        json={"error": {"code": "403004", "summary": "Forbidden", "detail": "not authorized"}},
    )
    httpx_mock.add_response(
        url=f"{API}/workbooks/wb-ok/permissions", json=permissions_payload("workbook", "wb-ok")
    )

    run_id = store.begin_run(modules=["permissions"])
    ctx = RunContext(
        rest=TableauRest(client), store=store, scrubber=Scrubber(store, run_id), run_id=run_id
    )
    stats = PermissionsModule().run(ctx)  # must NOT raise

    assert stats.denied == [("/workbooks/{luid}/permissions", "wb-personal")]
    assert stats.pages["/workbooks/{luid}/permissions"] == 1  # the accessible one landed
