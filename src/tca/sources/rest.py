"""Thin, typed wrappers over the Tableau REST endpoints the collector uses.

Dumb transport by design: no filtering, no interpretation — each method just
yields raw response pages. The endpoint *template* constants double as the
keys of the PII manifest (``tca/pseudo/manifest.py``); keep them in sync.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from tca.transport.client import RestClient

# Endpoint templates — MUST match the keys in tca/pseudo/manifest.py.
USERS = "/users"
GROUPS = "/groups"
GROUP_USERS = "/groups/{luid}/users"
PROJECTS = "/projects"
WORKBOOKS = "/workbooks"
VIEWS = "/views"
DATASOURCES = "/datasources"
WORKBOOK_CONNECTIONS = "/workbooks/{luid}/connections"
DATASOURCE_CONNECTIONS = "/datasources/{luid}/connections"

Page = tuple[int, dict[str, Any]]


class TableauRest:
    def __init__(self, client: RestClient) -> None:
        self._client = client

    # -- identity core ---------------------------------------------------------

    def users(self) -> Iterator[Page]:
        """All site users, all fields (one page = up to 1000 users)."""
        return self._client.paginate(USERS, params={"fields": "_all_"})

    def groups(self) -> Iterator[Page]:
        return self._client.paginate(GROUPS)

    def group_users(self, group_luid: str) -> Iterator[Page]:
        return self._client.paginate(GROUP_USERS.replace("{luid}", group_luid))

    # -- content inventory -------------------------------------------------------

    def projects(self) -> Iterator[Page]:
        return self._client.paginate(PROJECTS)

    def workbooks(self) -> Iterator[Page]:
        return self._client.paginate(WORKBOOKS, params={"fields": "_all_"})

    def views(self) -> Iterator[Page]:
        """Views with all-time usage counters (windowed usage comes later, from
        Admin Insights — the REST counter is all-time only)."""
        return self._client.paginate(VIEWS, params={"includeUsageStatistics": "true"})

    def datasources(self) -> Iterator[Page]:
        return self._client.paginate(DATASOURCES, params={"fields": "_all_"})

    def workbook_connections(self, workbook_luid: str) -> dict[str, Any]:
        """Not paginated: one small response per workbook."""
        return self._client.get_site(WORKBOOK_CONNECTIONS.replace("{luid}", workbook_luid))

    def datasource_connections(self, datasource_luid: str) -> dict[str, Any]:
        return self._client.get_site(DATASOURCE_CONNECTIONS.replace("{luid}", datasource_luid))
