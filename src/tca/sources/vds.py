"""VizQL Data Service: query published datasources as JSON rows.

Used exclusively to read the Admin Insights datasources (TS Events, TS Users,
Site Content, ...) — the only source of time-windowed activity on Tableau
Cloud. Dumb transport: field selection policy lives in the PII manifest
(tca/pseudo/manifest.py), not here.
"""

from __future__ import annotations

from typing import Any

from tca.transport.client import RestClient

QUERY_PATH = "/api/v1/vizql-data-service/query-datasource"
METADATA_PATH = "/api/v1/vizql-data-service/read-metadata"


class VizqlDataService:
    def __init__(self, client: RestClient) -> None:
        self._client = client

    def field_captions(self, datasource_luid: str) -> set[str]:
        """The fields a datasource actually exposes (captions drift across
        Tableau releases — always intersect before querying)."""
        payload = self._client.post_api(
            METADATA_PATH, json={"datasource": {"datasourceLuid": datasource_luid}}
        )
        return {f["fieldCaption"] for f in payload.get("data", []) if "fieldCaption" in f}

    def query(self, datasource_luid: str, field_captions: list[str]) -> dict[str, Any]:
        """Fetch the requested columns; response shape: {"data": [row, ...]}."""
        return self._client.post_api(
            QUERY_PATH,
            json={
                "datasource": {"datasourceLuid": datasource_luid},
                "query": {"fields": [{"fieldCaption": c} for c in field_captions]},
            },
        )
