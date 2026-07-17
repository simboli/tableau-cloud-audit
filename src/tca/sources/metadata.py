"""Tableau Metadata API: lineage, fields and formulas over GraphQL.

Dumb transport by design. The query texts — the complete request surface —
live in the PII manifest (``tca/pseudo/manifest.py``) next to the other
minimization contracts; this wrapper only posts them and follows cursor
pagination. GraphQL reports failures in an ``errors`` array (sometimes with
HTTP 200 and partial data): any error fails the page loudly — partial
results are never handed to the caller.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from tca.pseudo.manifest import GraphqlQuerySpec
from tca.transport.client import RestClient, TransportError

GRAPHQL_PATH = "/api/metadata/graphql"
# Nodes per page. Deliberately small: every workbook drags its embedded
# datasources, fields and sheets along, and Tableau Cloud enforces query
# complexity limits — shallow-and-frequent beats deep-and-throttled.
PAGE_SIZE = 20


class MetadataApi:
    def __init__(self, client: RestClient) -> None:
        self._client = client

    def totals(self) -> dict[str, int]:
        """Cheapest possible reachability probe (used by `tca verify`)."""
        payload = self._post(
            "query tca_verify { workbooksConnection(first: 1) { totalCount } "
            "publishedDatasourcesConnection(first: 1) { totalCount } }"
        )
        data = payload["data"]
        return {
            "workbooks": int(data["workbooksConnection"]["totalCount"]),
            "datasources": int(data["publishedDatasourcesConnection"]["totalCount"]),
        }

    def paginate(
        self,
        spec: GraphqlQuerySpec,
        after: str | None = None,
        start_page: int = 1,
    ) -> Iterator[tuple[int, dict[str, Any]]]:
        """Yield (page_number, payload) following the connection's cursor.

        ``after``/``start_page`` let a resumed run continue from the cursor of
        the last page already landed in raw, without refetching earlier pages.
        """
        page, cursor = start_page, after
        while True:
            payload = self._post(spec.query, variables={"first": PAGE_SIZE, "after": cursor})
            yield page, payload
            info = payload["data"][spec.connection]["pageInfo"]
            if not info.get("hasNextPage"):
                return
            cursor = str(info["endCursor"])
            page += 1

    def _post(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = self._client.post_api(
            GRAPHQL_PATH, json={"query": query, "variables": variables or {}}
        )
        errors = payload.get("errors")
        if errors:
            messages = "; ".join(str(e.get("message", e)) for e in errors[:3])
            raise TransportError(f"Metadata API returned errors: {messages}")
        if "data" not in payload or payload["data"] is None:
            raise TransportError("Metadata API returned no data and no errors.")
        return payload
