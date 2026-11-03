"""The permissions module: explicit rules on projects and content, plus the
project default-permission templates children inherit.

This is the most expensive loop in the collector (one call per project ×3,
plus one per workbook outside Personal Space and one per datasource) — it runs
last by convention and
leans on resume: already-landed items are skipped without an API call.

The module is self-sufficient: it re-fetches the cheap listings to learn the
item LUIDs. If the `content` module already landed those pages in this run,
ctx.land() skips the duplicate write and the payload is only parsed for IDs.
"""

from __future__ import annotations

from typing import Any

from tca.modules.base import ModuleStats, RunContext, land_item, register
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
        personal_luids: set[str] = set()
        for page, payload in ctx.rest.workbooks():
            stats.count(WORKBOOKS, ctx.land(WORKBOOKS, payload, page=page))
            workbook_luids.extend(_item_ids(payload, "workbooks", "workbook"))
            personal_luids.update(_personal_space_ids(payload))

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
        for project_luid in ctx.track(project_luids, "project permissions"):
            for endpoint, fetch in per_project:
                land_item(ctx, stats, endpoint, project_luid, fetch)

        # Personal Space workbooks are never queried: that content cannot be
        # shared, so it has no explicit rules, and Tableau Cloud refuses the call
        # (403, and 500 since 2026-11). Each refusal used to cost the full retry
        # backoff (~15 s), and three in a row aborted the run.
        queryable = [luid for luid in workbook_luids if luid not in personal_luids]
        stats.personal_space = len(workbook_luids) - len(queryable)
        for workbook_luid in ctx.track(queryable, "workbook permissions"):
            land_item(
                ctx, stats, WORKBOOK_PERMISSIONS, workbook_luid, ctx.rest.workbook_permissions
            )

        for datasource_luid in ctx.track(datasource_luids, "datasource permissions"):
            land_item(
                ctx, stats, DATASOURCE_PERMISSIONS, datasource_luid, ctx.rest.datasource_permissions
            )

        return stats


def _item_ids(payload: dict[str, Any], block: str, singular: str) -> list[str]:
    items = payload.get(block, {}).get(singular, [])
    if isinstance(items, dict):  # single-object shape
        items = [items]
    return [i["id"] for i in items if isinstance(i, dict) and "id" in i]


def _personal_space_ids(payload: dict[str, Any]) -> list[str]:
    items = payload.get("workbooks", {}).get("workbook", [])
    if isinstance(items, dict):  # single-object shape
        items = [items]
    return [
        i["id"]
        for i in items
        if isinstance(i, dict)
        and "id" in i
        and (i.get("location") or {}).get("type") == "PersonalSpace"
    ]


register(PermissionsModule())
