"""Transport tests: sign-in flow, silent re-auth, backoff, pagination, secret hygiene."""

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from tca.transport.auth import Credentials
from tca.transport.client import RestClient, TransportError

BASE = "https://test-pod.online.tableau.com"
SITE_LUID = "site-luid-1"


def make_client(sleeps: list[float] | None = None) -> RestClient:
    creds = Credentials.build(
        pod="test-pod", site="acme", pat_name="tca-collector", pat_secret="s3cret-token-value"
    )
    recorder = sleeps if sleeps is not None else []
    return RestClient(creds, sleep=recorder.append)


def mock_connect(httpx_mock: HTTPXMock, api_version: str = "3.26", token: str = "tok-1") -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/3.4/serverinfo",
        json={"serverInfo": {"restApiVersion": api_version, "productVersion": {"value": "x"}}},
    )
    httpx_mock.add_response(
        url=f"{BASE}/api/{api_version}/auth/signin",
        json={"credentials": {"token": token, "site": {"id": SITE_LUID, "contentUrl": "acme"}}},
    )


def users_page(page: int, total: int, size: int, *ids: str) -> dict[str, Any]:
    return {
        "pagination": {
            "pageNumber": str(page),
            "pageSize": str(size),
            "totalAvailable": str(total),
        },
        "users": {"user": [{"id": i} for i in ids]},
    }


def test_connect_resolves_version_and_signs_in(httpx_mock: HTTPXMock) -> None:
    mock_connect(httpx_mock)
    client = make_client()
    client.connect()
    assert client.api_version == "3.26"
    assert client.site_luid == SITE_LUID

    signin_request = httpx_mock.get_requests()[1]
    body = signin_request.read().decode()
    assert "tca-collector" in body
    assert '"contentUrl":"acme"' in body


def test_requests_carry_auth_token(httpx_mock: HTTPXMock) -> None:
    mock_connect(httpx_mock)
    httpx_mock.add_response(
        url=f"{BASE}/api/3.26/sites/{SITE_LUID}/groups?pageSize=1000&pageNumber=1",
        match_headers={"X-Tableau-Auth": "tok-1"},
        json={"groups": {}},
    )
    client = make_client()
    client.connect()
    pages = list(client.paginate("/groups"))
    assert pages == [(1, {"groups": {}})]


def test_pagination_walks_all_pages(httpx_mock: HTTPXMock) -> None:
    mock_connect(httpx_mock)
    base_url = f"{BASE}/api/3.26/sites/{SITE_LUID}/users?fields=_all_&pageSize=1000"
    httpx_mock.add_response(url=f"{base_url}&pageNumber=1", json=users_page(1, 3, 2, "a", "b"))
    httpx_mock.add_response(url=f"{base_url}&pageNumber=2", json=users_page(2, 3, 2, "c"))
    client = make_client()
    client.connect()
    pages = list(client.paginate("/users", params={"fields": "_all_"}))
    assert [p for p, _ in pages] == [1, 2]
    assert pages[1][1]["users"]["user"] == [{"id": "c"}]


def test_429_honours_retry_after(httpx_mock: HTTPXMock) -> None:
    mock_connect(httpx_mock)
    url = f"{BASE}/api/3.26/sites/{SITE_LUID}/groups?pageSize=1000&pageNumber=1"
    httpx_mock.add_response(url=url, status_code=429, headers={"Retry-After": "7"})
    httpx_mock.add_response(url=url, json={"groups": {}})
    sleeps: list[float] = []
    client = make_client(sleeps)
    client.connect()
    assert list(client.paginate("/groups")) == [(1, {"groups": {}})]
    assert sleeps == [7.0]


def test_gives_up_after_max_attempts(httpx_mock: HTTPXMock) -> None:
    mock_connect(httpx_mock)
    url = f"{BASE}/api/3.26/sites/{SITE_LUID}/groups?pageSize=1000&pageNumber=1"
    for _ in range(5):
        httpx_mock.add_response(url=url, status_code=503)
    client = make_client()
    client.connect()
    with pytest.raises(TransportError, match="Giving up after 5 attempts"):
        list(client.paginate("/groups"))


def test_401_triggers_silent_reauth_once(httpx_mock: HTTPXMock) -> None:
    mock_connect(httpx_mock, token="tok-1")
    url = f"{BASE}/api/3.26/sites/{SITE_LUID}/groups?pageSize=1000&pageNumber=1"
    httpx_mock.add_response(url=url, status_code=401, json={"error": {"code": "401002"}})
    httpx_mock.add_response(  # second sign-in issues a fresh token
        url=f"{BASE}/api/3.26/auth/signin",
        json={"credentials": {"token": "tok-2", "site": {"id": SITE_LUID, "contentUrl": "acme"}}},
    )
    httpx_mock.add_response(url=url, match_headers={"X-Tableau-Auth": "tok-2"}, json={"groups": {}})
    client = make_client()
    client.connect()
    assert list(client.paginate("/groups")) == [(1, {"groups": {}})]


def test_signin_failure_is_actionable_and_secret_free(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/3.4/serverinfo", json={"serverInfo": {"restApiVersion": "3.26"}}
    )
    httpx_mock.add_response(
        url=f"{BASE}/api/3.26/auth/signin",
        status_code=401,
        json={"error": {"code": "401001", "summary": "Signin Error", "detail": "Login failed."}},
    )
    client = make_client()
    with pytest.raises(TransportError) as excinfo:
        client.connect()
    message = str(excinfo.value)
    assert "TCA_PAT_SECRET" in message
    assert "Signin Error" in message
    assert "s3cret-token-value" not in message


def test_tableau_error_body_is_surfaced(httpx_mock: HTTPXMock) -> None:
    mock_connect(httpx_mock)
    httpx_mock.add_response(
        url=f"{BASE}/api/3.26/sites/{SITE_LUID}/groups?pageSize=1000&pageNumber=1",
        status_code=404,
        json={"error": {"code": "404005", "summary": "Resource Not Found", "detail": "nope"}},
    )
    client = make_client()
    client.connect()
    with pytest.raises(TransportError, match="404005.*Resource Not Found"):
        list(client.paginate("/groups"))


def test_signout_clears_token(httpx_mock: HTTPXMock) -> None:
    mock_connect(httpx_mock)
    httpx_mock.add_response(url=f"{BASE}/api/3.26/auth/signout", json={})
    client = make_client()
    client.connect()
    client.signout()
    with pytest.raises(TransportError, match="Not signed in"):
        client.get_site("/groups")


def test_credentials_repr_hides_secret() -> None:
    creds = Credentials.build(pod="p", site="s", pat_name="n", pat_secret="hidden-value")
    assert "hidden-value" not in repr(creds)


def test_full_base_url_accepted() -> None:
    creds = Credentials.build(
        pod="https://tableau.example.com/", site="s", pat_name="n", pat_secret="x"
    )
    assert creds.server == "https://tableau.example.com"
