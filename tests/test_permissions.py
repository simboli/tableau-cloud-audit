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
from tca.transport.client import MAX_ATTEMPTS, RestClient, TransportError

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
        Credentials.build(pod="test-pod", site="acme", pat_name="n", pat_secret="s"),
        sleep=lambda _: None,
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


def mock_workbook_listings(httpx_mock: HTTPXMock, *workbook_luids: str) -> None:
    httpx_mock.add_response(
        url=f"{API}/projects?pageSize=1000&pageNumber=1", json=page("projects", "project", [])
    )
    httpx_mock.add_response(
        url=f"{API}/workbooks?fields=_all_&pageSize=1000&pageNumber=1",
        json=page("workbooks", "workbook", [{"id": luid} for luid in workbook_luids]),
    )
    httpx_mock.add_response(
        url=f"{API}/datasources?fields=_all_&pageSize=1000&pageNumber=1",
        json=page("datasources", "datasource", []),
    )


def fail_every_attempt(httpx_mock: HTTPXMock, workbook_luid: str) -> None:
    for _ in range(MAX_ATTEMPTS):
        httpx_mock.add_response(
            url=f"{API}/workbooks/{workbook_luid}/permissions",
            status_code=500,
            json={"error": {"code": "500000", "summary": "Internal Server Error"}},
        )


def succeed(httpx_mock: HTTPXMock, workbook_luid: str) -> None:
    httpx_mock.add_response(
        url=f"{API}/workbooks/{workbook_luid}/permissions",
        json=permissions_payload("workbook", workbook_luid),
    )


def run_module(client: RestClient, store: PackageStore):
    run_id = store.begin_run(modules=["permissions"])
    ctx = RunContext(
        rest=TableauRest(client), store=store, scrubber=Scrubber(store, run_id), run_id=run_id
    )
    return PermissionsModule().run(ctx)


def test_personal_space_workbooks_are_not_queried(httpx_mock: HTTPXMock, connected) -> None:
    # Tableau refuses them (403, then 500): asking cost the full retry backoff
    # per workbook, and three in a row aborted the run
    client, store = connected
    personal = {"type": "PersonalSpace", "name": "Personal Space"}
    httpx_mock.add_response(
        url=f"{API}/projects?pageSize=1000&pageNumber=1", json=page("projects", "project", [])
    )
    httpx_mock.add_response(
        url=f"{API}/workbooks?fields=_all_&pageSize=1000&pageNumber=1",
        json=page(
            "workbooks",
            "workbook",
            [{"id": f"wb-p{i}", "location": {"id": f"ps-{i}", **personal}} for i in range(1, 4)]
            + [{"id": "wb-ok", "location": {"id": "p-1", "type": "Project"}}],
        ),
    )
    httpx_mock.add_response(
        url=f"{API}/datasources?fields=_all_&pageSize=1000&pageNumber=1",
        json=page("datasources", "datasource", []),
    )
    succeed(httpx_mock, "wb-ok")  # any other permissions call fails: unmocked

    stats = run_module(client, store)

    assert stats.personal_space == 3
    assert stats.pages["/workbooks/{luid}/permissions"] == 1
    assert stats.denied == [] and stats.failed == []


def test_persistent_5xx_on_single_item_is_skipped_not_fatal(
    httpx_mock: HTTPXMock, connected
) -> None:
    # seen live: a Personal Space workbook answering 500 to its permissions query
    # (those are no longer queried; the 5xx skip stays for any other item)
    client, store = connected
    mock_workbook_listings(httpx_mock, "wb-personal", "wb-ok")
    fail_every_attempt(httpx_mock, "wb-personal")
    succeed(httpx_mock, "wb-ok")

    stats = run_module(client, store)  # must NOT raise

    assert stats.failed == [("/workbooks/{luid}/permissions", "wb-personal")]
    assert stats.pages["/workbooks/{luid}/permissions"] == 1
    landed = store.con.execute(
        "SELECT count(*) FROM raw.api_responses WHERE entity_luid = 'wb-personal'"
    ).fetchone()[0]
    assert landed == 0  # never landed, so the next collect retries it


def test_consecutive_5xx_items_abort_the_run(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    mock_workbook_listings(httpx_mock, "wb-1", "wb-2", "wb-3")
    for luid in ("wb-1", "wb-2", "wb-3"):
        fail_every_attempt(httpx_mock, luid)

    with pytest.raises(TransportError, match="3 items in a row failed") as excinfo:
        run_module(client, store)
    assert "/workbooks/wb-3/permissions" in str(excinfo.value)


def test_a_success_resets_the_5xx_streak(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    mock_workbook_listings(httpx_mock, "wb-1", "wb-2", "wb-3", "wb-4", "wb-5")
    for luid in ("wb-1", "wb-2", "wb-4", "wb-5"):
        fail_every_attempt(httpx_mock, luid)
    succeed(httpx_mock, "wb-3")

    stats = run_module(client, store)

    assert [luid for _, luid in stats.failed] == ["wb-1", "wb-2", "wb-4", "wb-5"]
    assert stats.pages["/workbooks/{luid}/permissions"] == 1
