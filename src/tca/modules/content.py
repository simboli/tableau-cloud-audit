"""The content-inventory module: projects, workbooks, views, datasources,
and per-item connections.

Cheap listings first, per-item connection calls last (the expensive loop).
Owners inside content objects are pseudonymised by the scrubber; connection
credential usernames are redacted (see the manifest for both rules).
"""

from __future__ import annotations

from typing import Any

from tca.modules.base import ModuleStats, RunContext, register
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
            ctx.store.write_response(
                ctx.run_id, PROJECTS, ctx.scrubber.scrub(PROJECTS, payload), page=page
            )
            stats.add_page(PROJECTS)
            ctx.on_page(PROJECTS, page)

        workbook_luids: list[str] = []
        for page, payload in ctx.rest.workbooks():
            ctx.store.write_response(
                ctx.run_id, WORKBOOKS, ctx.scrubber.scrub(WORKBOOKS, payload), page=page
            )
            stats.add_page(WORKBOOKS)
            ctx.on_page(WORKBOOKS, page)
            workbook_luids.extend(_item_ids(payload, "workbooks", "workbook"))

        for page, payload in ctx.rest.views():
            ctx.store.write_response(
                ctx.run_id, VIEWS, ctx.scrubber.scrub(VIEWS, payload), page=page
            )
            stats.add_page(VIEWS)
            ctx.on_page(VIEWS, page)

        datasource_luids: list[str] = []
        for page, payload in ctx.rest.datasources():
            ctx.store.write_response(
                ctx.run_id, DATASOURCES, ctx.scrubber.scrub(DATASOURCES, payload), page=page
            )
            stats.add_page(DATASOURCES)
            ctx.on_page(DATASOURCES, page)
            datasource_luids.extend(_item_ids(payload, "datasources", "datasource"))

        # per-item calls last: the expensive loop
        for workbook_luid in workbook_luids:
            payload = ctx.rest.workbook_connections(workbook_luid)
            ctx.store.write_response(
                ctx.run_id,
                WORKBOOK_CONNECTIONS,
                ctx.scrubber.scrub(WORKBOOK_CONNECTIONS, payload),
                entity_luid=workbook_luid,
            )
            stats.add_page(WORKBOOK_CONNECTIONS)
            ctx.on_page(WORKBOOK_CONNECTIONS, 1)

        for datasource_luid in datasource_luids:
            payload = ctx.rest.datasource_connections(datasource_luid)
            ctx.store.write_response(
                ctx.run_id,
                DATASOURCE_CONNECTIONS,
                ctx.scrubber.scrub(DATASOURCE_CONNECTIONS, payload),
                entity_luid=datasource_luid,
            )
            stats.add_page(DATASOURCE_CONNECTIONS)
            ctx.on_page(DATASOURCE_CONNECTIONS, 1)

        return stats


def _item_ids(payload: dict[str, Any], block: str, singular: str) -> list[str]:
    items = payload.get(block, {}).get(singular, [])
    if isinstance(items, dict):  # single-object shape
        items = [items]
    return [i["id"] for i in items if isinstance(i, dict) and "id" in i]


register(ContentModule())
