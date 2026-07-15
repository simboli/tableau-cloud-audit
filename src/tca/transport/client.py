"""The httpx wrapper every API call goes through.

Centralises everything the modules must never think about:

* auth header injection and **silent re-auth** (the Tableau session token has
  a ~4h sliding expiry: on a 401 mid-run we sign in again, once, and retry);
* retry with exponential backoff on 429 (honouring ``Retry-After``) and 5xx;
* pagination as an iterator of ``(page_number, payload)`` tuples;
* friendly, **secret-free** error messages (the PAT and session token never
  appear in exceptions or logs).
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from tca import __version__
from tca.transport.auth import BOOTSTRAP_API_VERSION, Credentials, signin_body

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0
PAGE_SIZE = 1000  # Tableau REST maximum


class TransportError(RuntimeError):
    """An API problem the user can act on (clear message, no secrets)."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RestClient:
    """One authenticated session against one Tableau Cloud site."""

    def __init__(
        self,
        creds: Credentials,
        *,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._creds = creds
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=creds.server,
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": f"tca/{__version__}"},
        )
        self._token: str | None = None
        self.api_version: str | None = None
        self.site_luid: str | None = None

    # -- lifecycle -------------------------------------------------------------

    def connect(self) -> None:
        """Resolve the API version, then sign in with the PAT."""
        info = self._request_json("GET", f"/api/{BOOTSTRAP_API_VERSION}/serverinfo", authed=False)
        self.api_version = str(info["serverInfo"]["restApiVersion"])
        self._signin()

    def signout(self) -> None:
        if self._token is None:
            return
        # Best effort: the PAT will be revoked by the client anyway.
        with contextlib.suppress(TransportError):
            self._request_json("POST", f"/api/{self.api_version}/auth/signout")
        self._token = None

    def close(self) -> None:
        self._http.close()

    # -- request plumbing --------------------------------------------------------

    def _signin(self) -> None:
        data = self._request_json(
            "POST",
            f"/api/{self.api_version}/auth/signin",
            json=signin_body(self._creds),
            authed=False,
        )
        credentials = data["credentials"]
        self._token = str(credentials["token"])
        self.site_luid = str(credentials["site"]["id"])

    def _request_json(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        authed: bool = True,
    ) -> dict[str, Any]:
        attempt = 0
        reauthed = False
        while True:
            headers = {}
            if authed:
                if self._token is None:
                    raise TransportError("Not signed in — call connect() first.")
                headers["X-Tableau-Auth"] = self._token
            try:
                response = self._http.request(
                    method, url, params=params, json=json, headers=headers
                )
            except httpx.HTTPError as exc:
                raise TransportError(
                    f"Network error calling {method} {url}: {exc.__class__.__name__}: {exc}"
                ) from exc

            if response.status_code == 429 or response.status_code >= 500:
                attempt += 1
                if attempt >= MAX_ATTEMPTS:
                    raise TransportError(
                        f"Giving up after {MAX_ATTEMPTS} attempts: {_describe(response)}",
                        status_code=response.status_code,
                    )
                self._sleep(_backoff_seconds(response, attempt))
                continue

            if response.status_code == 401 and authed and not reauthed:
                # Session token expired mid-run (~4h sliding): sign in again, once.
                reauthed = True
                self._signin()
                continue

            if response.status_code >= 400:
                message = _describe(response)
                if not authed and "signin" in url:
                    message += (
                        " — sign-in failed: check the PAT name, the TCA_PAT_SECRET "
                        "environment variable, and the site name. PATs also expire "
                        "if unused for 15 days."
                    )
                raise TransportError(message, status_code=response.status_code)

            if response.status_code == 204 or not response.content:
                return {}  # e.g. signout replies 204 No Content
            try:
                return dict(response.json())
            except ValueError as exc:
                raise TransportError(
                    f"{method} {url} returned HTTP {response.status_code} with a "
                    f"non-JSON body: {response.text[:200]!r}"
                ) from exc

    # -- public request surface ----------------------------------------------------

    def get_site(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a site-scoped path, e.g. get_site('/users')."""
        if self.site_luid is None:
            raise TransportError("Not signed in — call connect() first.")
        return self._request_json(
            "GET", f"/api/{self.api_version}/sites/{self.site_luid}{path}", params=params
        )

    def post_api(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        """POST to a non-site-scoped API path (e.g. VizQL Data Service), authed."""
        return self._request_json("POST", path, json=json)

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        page_size: int = PAGE_SIZE,
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        """Yield (page_number, payload) for every page of a site-scoped listing."""
        page = 1
        while True:
            payload = self.get_site(
                path, params={**(params or {}), "pageSize": page_size, "pageNumber": page}
            )
            yield page, payload
            pagination = payload.get("pagination")
            if not pagination:
                return
            total = int(pagination.get("totalAvailable", 0))
            size = int(pagination.get("pageSize", page_size))
            number = int(pagination.get("pageNumber", page))
            if number * size >= total:
                return
            page = number + 1


def _backoff_seconds(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(float(retry_after), 0.0)
        except ValueError:
            pass
    return min(BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), BACKOFF_CAP_SECONDS)


def _describe(response: httpx.Response) -> str:
    """Human-readable error from a Tableau REST error body. Never includes secrets."""
    try:
        error = response.json().get("error", {})
        code = error.get("code", "")
        summary = error.get("summary", "")
        detail = error.get("detail", "")
        return f"HTTP {response.status_code} (Tableau {code}): {summary} — {detail}"
    except Exception:
        return f"HTTP {response.status_code}: {response.text[:200]}"
