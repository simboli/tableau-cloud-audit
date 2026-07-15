"""The permissions module: explicit rules on projects and content, plus the
project default-permission templates children inherit.

This is the most expensive loop in the collector (one call per project ×3,
plus one per workbook and per datasource) — it runs last by convention and
leans on resume: already-landed items are skipped without an API call.

The module is self-sufficient: it re-fetches the cheap listings to learn the
item LUIDs. If the `content` module already landed those pages in this run,
ctx.land() skips the duplicate write and the payload is only parsed for IDs.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tca.modules.base import ModuleStats, RunContext, register
from tca.sources.rest import (
    DATASOURCE_PERMISSIONS,
    DATASOURCES,
    PROJECT_DEFAULT_PERMISSIONS_DATASOURCES,
    PROJECT_DEFAULT_PERMISSIONS_WORKBOOKS,
    PROJECT_PERMISSIONS,
    PROJECTS,
    WORKBOOK_PERMISSIONS,
    WORKBOOKS,
)
from tca.transport.client import TransportError


class PermissionsModule:
    name = "permissions"
    requires: tuple[str, ...] = ()

    def run(self, ctx: RunContext) -> ModuleStats:
        stats = ModuleStats()

        project_luids: list[str] = []
        for page, payload in ctx.rest.projects():
            stats.count(PROJECTS, ctx.land(PROJECTS, payload, page=page))
            project_luids.extend(_item_ids(payload, "projects", "project"))

        workbook_luids: list[str] = []
        for page, payload in ctx.rest.workbooks():
            stats.count(WORKBOOKS, ctx.land(WORKBOOKS, payload, page=page))
            workbook_luids.extend(_item_ids(payload, "workbooks", "workbook"))

        datasource_luids: list[str] = []
        for page, payload in ctx.rest.datasources():
            stats.count(DATASOURCES, ctx.land(DATASOURCES, payload, page=page))
            datasource_luids.extend(_item_ids(payload, "datasources", "datasource"))

        # project-level rules + the default templates children inherit —
        # this is where "All Users" grants hide (F-10)
        per_project = [
            (PROJECT_PERMISSIONS, ctx.rest.project_permissions),
            (PROJECT_DEFAULT_PERMISSIONS_WORKBOOKS, ctx.rest.project_default_permissions_workbooks),
            (
                PROJECT_DEFAULT_PERMISSIONS_DATASOURCES,
                ctx.rest.project_default_permissions_datasources,
            ),
        ]
        for project_luid in project_luids:
            for endpoint, fetch in per_project:
                _land_item(ctx, stats, endpoint, project_luid, fetch)

        for workbook_luid in workbook_luids:
            _land_item(
                ctx, stats, WORKBOOK_PERMISSIONS, workbook_luid, ctx.rest.workbook_permissions
            )

        for datasource_luid in datasource_luids:
            _land_item(
                ctx, stats, DATASOURCE_PERMISSIONS, datasource_luid, ctx.rest.datasource_permissions
            )

        return stats


def _land_item(
    ctx: RunContext,
    stats: ModuleStats,
    endpoint: str,
    entity_luid: str,
    fetch: Callable[[str], dict[str, Any]],
) -> None:
    """Fetch + land one per-item permissions page.

    403/404 on a single item is a property of the site (e.g. Personal Space
    content refuses permission queries even to admins) — counted and skipped,
    never fatal. Any other error still aborts the run (resumable).
    """
    if ctx.is_done(endpoint, entity_luid):
        stats.count(endpoint, written=False)
        return
    try:
        payload = fetch(entity_luid)
    except TransportError as exc:
        if exc.status_code in (403, 404):
            stats.count_denied(endpoint, entity_luid)
            return
        raise
    stats.count(endpoint, ctx.land(endpoint, payload, entity_luid=entity_luid))


def _item_ids(payload: dict[str, Any], block: str, singular: str) -> list[str]:
    items = payload.get(block, {}).get(singular, [])
    if isinstance(items, dict):  # single-object shape
        items = [items]
    return [i["id"] for i in items if isinstance(i, dict) and "id" in i]


register(PermissionsModule())
