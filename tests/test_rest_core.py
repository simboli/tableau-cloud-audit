"""rest_core module: fake Tableau API -> scrubber -> package file, end to end."""

from pathlib import Path
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from tca.modules.base import RunContext
from tca.modules.rest_core import RestCoreModule
from tca.pseudo.scrubber import Scrubber
from tca.sources.rest import TableauRest
from tca.storage.writer import PackageStore
from tca.transport.auth import Credentials
from tca.transport.client import RestClient

BASE = "https://test-pod.online.tableau.com"
SITE_LUID = "site-luid-1"
API = f"{BASE}/api/3.26/sites/{SITE_LUID}"

USER_A = {
    "id": "aaaaaaaa-1111-2222-3333-444444444444",
    "name": "mario.rossi@acme.it",
    "fullName": "Mario Rossi",
    "email": "mario.rossi@acme.it",
    "siteRole": "Creator",
}
USER_B = {
    "id": "bbbbbbbb-1111-2222-3333-444444444444",
    "name": "lbianchi",
    "email": "lucia.bianchi@acme.it",
    "siteRole": "Viewer",
}


def page(block: str, items: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    singular = block[:-1]  # users -> user, groups -> group
    return {
        "pagination": {
            "pageNumber": "1",
            "pageSize": "1000",
            "totalAvailable": str(total if total is not None else len(items)),
        },
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


def test_rest_core_lands_scrubbed_pages(httpx_mock: HTTPXMock, connected) -> None:
    client, store = connected
    httpx_mock.add_response(
        url=f"{API}/users?fields=_all_&pageSize=1000&pageNumber=1",
        json=page("users", [USER_A, USER_B]),
    )
    httpx_mock.add_response(
        url=f"{API}/groups?pageSize=1000&pageNumber=1",
        json=page("groups", [{"id": "g-1", "name": "Finance"}, {"id": "g-2", "name": "Sales"}]),
    )
    httpx_mock.add_response(
        url=f"{API}/groups/g-1/users?pageSize=1000&pageNumber=1",
        json=page("users", [{"id": USER_A["id"]}]),
    )
    httpx_mock.add_response(
        url=f"{API}/groups/g-2/users?pageSize=1000&pageNumber=1",
        json=page("users", []),
    )

    run_id = store.begin_run(modules=["rest_core"])
    ctx = RunContext(
        rest=TableauRest(client), store=store, scrubber=Scrubber(store, run_id), run_id=run_id
    )
    stats = RestCoreModule().run(ctx)

    assert stats.pages == {"/users": 1, "/groups": 1, "/groups/{luid}/users": 2}

    # users page landed pseudonymised
    email = store.con.execute(
        "SELECT payload->>'$.users.user[0].email' FROM raw.api_responses WHERE endpoint = '/users'"
    ).fetchone()[0]
    assert email == "U-0001"

    # membership rows carry the group LUID and reuse the same pseudonym
    member = store.con.execute(
        "SELECT entity_luid, payload->>'$.users.user[0].id' FROM raw.api_responses "
        "WHERE endpoint = '/groups/{luid}/users' AND entity_luid = 'g-1'"
    ).fetchone()
    assert member == ("g-1", "U-0001")

    # vault knows both users, and nothing else in raw contains their real data
    assert store.resolve("U-0001")["email"] == "mario.rossi@acme.it"
    leaked = store.con.execute(
        "SELECT count(*) FROM raw.api_responses WHERE payload::VARCHAR LIKE '%acme.it%'"
    ).fetchone()[0]
    assert leaked == 0
