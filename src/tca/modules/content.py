"""The content-inventory module: projects, workbooks, views, datasources,
and per-item connections.

Cheap listings first, per-item connection calls last (the expensive loop —
which is exactly where resume pays off: already-landed items are skipped
without an API call). Owners inside content objects are pseudonymised by the
scrubber; connection credential usernames are redacted (see the manifest).
"""

from __future__ import annotations

from typing import Any

from tca.modules.base import ModuleStats, RunContext, land_item, register
from tca.sources.rest import (
    DATASOURCE_CONNECTIONS,
    DATASOURCES,
    PROJECTS,
    VIEWS,
    WORKBOOK_CONNECTIONS,
    WORKBOOKS,
)


class ContentModule:
    name = "content"
    requires: tuple[str, ...] = ()

    def run(self, ctx: RunContext) -> ModuleStats:
        stats = ModuleStats()

        for page, payload in ctx.rest.projects():
            stats.count(PROJECTS, ctx.land(PROJECTS, payload, page=page))

        workbook_luids: list[str] = []
        for page, payload in ctx.rest.workbooks():
            stats.count(WORKBOOKS, ctx.land(WORKBOOKS, payload, page=page))
            workbook_luids.extend(_item_ids(payload, "workbooks", "workbook"))

        for page, payload in ctx.rest.views():
            stats.count(VIEWS, ctx.land(VIEWS, payload, page=page))

        datasource_luids: list[str] = []
        for page, payload in ctx.rest.datasources():
            stats.count(DATASOURCES, ctx.land(DATASOURCES, payload, page=page))
            datasource_luids.extend(_item_ids(payload, "datasources", "datasource"))

        # per-item calls last: the expensive loop, and the resume sweet spot
        for workbook_luid in ctx.track(workbook_luids, "workbook connections"):
            land_item(
                ctx, stats, WORKBOOK_CONNECTIONS, workbook_luid, ctx.rest.workbook_connections
            )

        for datasource_luid in ctx.track(datasource_luids, "datasource connections"):
            land_item(
                ctx, stats, DATASOURCE_CONNECTIONS, datasource_luid, ctx.rest.datasource_connections
            )

        return stats


def _item_ids(payload: dict[str, Any], block: str, singular: str) -> list[str]:
    items = payload.get(block, {}).get(singular, [])
    if isinstance(items, dict):  # single-object shape
        items = [items]
    return [i["id"] for i in items if isinstance(i, dict) and "id" in i]


register(ContentModule())
