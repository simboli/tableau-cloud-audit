"""Mechanical normalization: raw pages -> typed state tables.

Runs at the end of every collect. For each run, state rows are DERIVED from
the raw pages of that run and fully rebuilt (delete + insert): raw stays the
source of truth, re-running is always safe, and a resumed run normalizes
exactly what it landed.

Strictly mechanical by design — field renaming, timestamp parsing, list
flattening. Anything that *evaluates* the site (tiers, scores, effective
permissions) is analysis and does not belong in this repo.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from tca.sources import rest
from tca.storage.writer import PackageStore

STATE_TABLES: dict[str, str] = {
    # table -> column list (INSERT order)
    "state.users": "run_id, user_pseudo, site_role, auth_setting, last_login_at",
    "state.groups": "run_id, group_luid, name, domain, min_site_role",
    "state.group_members": "run_id, group_luid, user_pseudo",
    "state.projects": (
        "run_id, project_luid, name, parent_luid, content_permissions, owner_pseudo"
    ),
    "state.content_items": (
        "run_id, item_luid, item_type, name, project_luid, owner_pseudo, created_at, "
        "updated_at, size_raw, is_certified, has_extracts, tags"
    ),
    "state.views": ("run_id, view_luid, workbook_luid, name, content_url, total_views_alltime"),
    "state.connections": (
        "run_id, item_luid, connection_luid, conn_type, server_address, "
        "has_embedded_credentials, cred_user_present"
    ),
    "state.permission_rules": (
        "run_id, rule_id, target_luid, target_type, is_default_template, template_for, "
        "grantee_type, grantee_id, capability, mode"
    ),
}

Rows = dict[str, list[list[Any]]]


def normalize_run(store: PackageStore, run_id: int) -> dict[str, int]:
    """Rebuild the state tables for one run from its raw pages."""
    pages = store.con.execute(
        "SELECT endpoint, entity_luid, payload FROM raw.api_responses WHERE run_id = ?",
        [run_id],
    ).fetchall()

    rows: Rows = {table: [] for table in STATE_TABLES}
    rule_counter = _Counter()
    for endpoint, entity_luid, payload_json in pages:
        payload = json.loads(payload_json)
        _dispatch(endpoint, entity_luid, payload, run_id, rows, rule_counter)

    with store.transaction():
        for table, columns in STATE_TABLES.items():
            store.con.execute(f"DELETE FROM {table} WHERE run_id = ?", [run_id])
            if rows[table]:
                placeholders = ", ".join("?" for _ in columns.split(","))
                store.con.executemany(
                    f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", rows[table]
                )
    return {table: len(table_rows) for table, table_rows in rows.items() if table_rows}


class _Counter:
    def __init__(self) -> None:
        self.value = 0

    def next(self) -> int:
        self.value += 1
        return self.value


def _dispatch(
    endpoint: str,
    entity_luid: str | None,
    payload: dict[str, Any],
    run_id: int,
    rows: Rows,
    rule_counter: _Counter,
) -> None:
    if endpoint == rest.USERS:
        for user in _items(payload, "users", "user"):
            rows["state.users"].append(
                [
                    run_id,
                    user.get("id"),
                    user.get("siteRole"),
                    user.get("authSetting"),
                    _ts(user.get("lastLogin")),
                ]
            )
    elif endpoint == rest.GROUPS:
        for group in _items(payload, "groups", "group"):
            rows["state.groups"].append(
                [
                    run_id,
                    group.get("id"),
                    group.get("name"),
                    group.get("domain", {}).get("name"),
                    group.get("import", {}).get("minimumSiteRole"),
                ]
            )
    elif endpoint == rest.GROUP_USERS:
        for user in _items(payload, "users", "user"):
            rows["state.group_members"].append([run_id, entity_luid, user.get("id")])
    elif endpoint == rest.PROJECTS:
        for project in _items(payload, "projects", "project"):
            rows["state.projects"].append(
                [
                    run_id,
                    project.get("id"),
                    project.get("name"),
                    project.get("parentProjectId"),
                    project.get("contentPermissions"),
                    project.get("owner", {}).get("id"),
                ]
            )
    elif endpoint == rest.WORKBOOKS:
        for wb in _items(payload, "workbooks", "workbook"):
            rows["state.content_items"].append(_content_row(run_id, wb, "workbook"))
    elif endpoint == rest.DATASOURCES:
        for ds in _items(payload, "datasources", "datasource"):
            rows["state.content_items"].append(_content_row(run_id, ds, "datasource"))
    elif endpoint == rest.VIEWS:
        for view in _items(payload, "views", "view"):
            usage = view.get("usage", {})
            rows["state.views"].append(
                [
                    run_id,
                    view.get("id"),
                    view.get("workbook", {}).get("id"),
                    view.get("name"),
                    view.get("contentUrl"),
                    _int(usage.get("totalViewCount")),
                ]
            )
    elif endpoint in (rest.WORKBOOK_CONNECTIONS, rest.DATASOURCE_CONNECTIONS):
        for conn in _items(payload, "connections", "connection"):
            user_name = conn.get("userName")
            rows["state.connections"].append(
                [
                    run_id,
                    entity_luid,
                    conn.get("id"),
                    conn.get("type"),
                    conn.get("serverAddress"),
                    _bool(conn.get("embedPassword")),
                    bool(isinstance(user_name, str) and user_name),
                ]
            )
    elif endpoint in _PERMISSION_ENDPOINTS:
        target_type, is_default, template_for = _PERMISSION_ENDPOINTS[endpoint]
        for grantee in payload.get("permissions", {}).get("granteeCapabilities", []):
            grantee_type = "user" if "user" in grantee else "group"
            grantee_id = grantee.get(grantee_type, {}).get("id")
            capabilities = grantee.get("capabilities", {}).get("capability", [])
            if isinstance(capabilities, dict):
                capabilities = [capabilities]
            for capability in capabilities:
                rows["state.permission_rules"].append(
                    [
                        run_id,
                        rule_counter.next(),
                        entity_luid,
                        target_type,
                        is_default,
                        template_for,
                        grantee_type,
                        grantee_id,
                        capability.get("name"),
                        capability.get("mode"),
                    ]
                )
    # unknown endpoints (VDS sources, future additions) are simply not typed yet


_PERMISSION_ENDPOINTS: dict[str, tuple[str, bool, str | None]] = {
    rest.PROJECT_PERMISSIONS: ("project", False, None),
    rest.PROJECT_DEFAULT_PERMISSIONS_WORKBOOKS: ("project", True, "workbook"),
    rest.PROJECT_DEFAULT_PERMISSIONS_DATASOURCES: ("project", True, "datasource"),
    rest.WORKBOOK_PERMISSIONS: ("workbook", False, None),
    rest.DATASOURCE_PERMISSIONS: ("datasource", False, None),
}


def _content_row(run_id: int, item: dict[str, Any], item_type: str) -> list[Any]:
    tags = item.get("tags", {}).get("tag", [])
    if isinstance(tags, dict):
        tags = [tags]
    return [
        run_id,
        item.get("id"),
        item_type,
        item.get("name"),
        item.get("project", {}).get("id"),
        item.get("owner", {}).get("id"),
        _ts(item.get("createdAt")),
        _ts(item.get("updatedAt")),
        _int(item.get("size")),
        _bool(item.get("isCertified")),
        _bool(item.get("hasExtracts")),
        [t.get("label") for t in tags if isinstance(t, dict) and t.get("label")],
    ]


def _items(payload: dict[str, Any], block: str, singular: str) -> list[dict[str, Any]]:
    items = payload.get(block, {}).get(singular, [])
    if isinstance(items, dict):  # single-object shape
        items = [items]
    return [i for i in items if isinstance(i, dict)]


def _ts(value: Any) -> datetime | None:
    """ISO-8601 (with or without Z) -> naive UTC, matching the file convention."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return None
