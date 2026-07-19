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
    "state.user_activity": (
        "run_id, user_id, user_pseudo, site_role, license_type, user_created_at, "
        "last_login_at, days_since_last_login"
    ),
    "state.content_usage": (
        "run_id, item_id, item_luid, item_type, name, parent_project_name, "
        "top_project_name, project_level, created_at, updated_at, first_published_at, "
        "last_published_at, last_accessed_at, size_bytes, is_data_extract, "
        "has_refresh_scheduled, is_certified, database_type, view_workbook_id, "
        "view_type, controlled_permissions_enabled, controlling_project_luid, tags"
    ),
    "state.tokens": (
        "run_id, guid, token_identifier, token_type, pat_name, issued_at, expires_at, "
        "last_used_at, last_updated_at, database_type, owner_pseudo"
    ),
    "state.vds_group_members": (
        "run_id, group_luid, group_name, min_site_role, licensed_on_sign_in, user_pseudo"
    ),
    "state.user_capabilities": (
        "run_id, item_luid, item_type, item_name, parent_project_name, top_project_name, "
        "controlling_project_name, capability, permission_value, permission_description, "
        "has_permission, user_pseudo, user_site_role"
    ),
    "state.subscription_health": (
        "run_id, subscription_luid, subscription_id, status, data_conditions, has_image, "
        "has_pdf, extract_refresh_triggered, item_luid, item_type, item_name, "
        "schedule_luid, schedule_name, schedule_type, created_at, last_sent_at, "
        "task_luid, task_type, consecutive_failures, queued_seconds, run_seconds"
    ),
    "state.viz_loads": (
        "run_id, request_id, request_time, duration_seconds, status_code, status_type, "
        "item_luid, item_type, item_name, repository_url, project_name, workbook_name"
    ),
    "state.datasource_fields": (
        "run_id, workbook_luid, datasource_node_id, datasource_luid, datasource_name, "
        "is_embedded, field_node_id, name, field_type, role, data_type, is_hidden, formula"
    ),
    "state.upstream_tables": (
        "run_id, datasource_node_id, table_node_id, name, table_schema, full_name, "
        "is_embedded, database_name, database_type"
    ),
    "state.sheet_fields": (
        "run_id, workbook_luid, sheet_node_id, sheet_name, field_node_id, field_name, kind"
    ),
    "state.workbook_datasources": (
        "run_id, workbook_luid, datasource_node_id, datasource_luid, datasource_name, is_embedded"
    ),
}

# VDS pages are flat rows: caption -> (state column, converter). Order matches
# the STATE_TABLES column list above (run_id excluded).
_VDS_STATE: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "vds:ts_users": (
        "state.user_activity",
        (
            ("User ID", "int"),
            ("User LUID", "raw"),  # already the pseudonym
            ("User Site Role", "raw"),
            ("User License Type", "raw"),
            ("User Creation Date", "ts"),
            ("Last Login Date", "ts"),
            ("Days Since Last Login", "int"),
        ),
    ),
    "vds:site_content": (
        "state.content_usage",
        (
            ("Item ID", "int"),
            ("Item LUID", "raw"),
            ("Item Type", "raw"),
            ("Item Name", "raw"),
            ("Item Parent Project Name", "raw"),
            ("Top Parent Project Name", "raw"),
            ("Project Level", "int"),
            ("Created At", "ts"),
            ("Updated At", "ts"),
            ("First Published At", "ts"),
            ("Last Published At", "ts"),
            ("Last Accessed At", "ts"),
            ("Size (bytes)", "int"),
            ("Is Data Extract", "bool"),
            ("Has Refresh Scheduled", "bool"),
            ("Data Source Is Certified", "bool"),
            ("Data Source Database Type", "raw"),
            ("View Workbook ID", "int"),
            ("View Type", "raw"),
            ("Controlled Permissions Enabled", "bool"),
            ("Controlling Permissions Project LUID", "raw"),
            ("Tags", "raw"),
        ),
    ),
    "vds:tokens": (
        "state.tokens",
        (
            ("GUID", "raw"),
            ("Token Identifier", "raw"),
            ("Token Type", "raw"),
            ("PAT Name", "raw"),
            ("Issued At", "ts"),
            ("Expires At", "ts"),
            ("Last Used At", "ts"),
            ("Last Updated", "ts"),
            ("Database Type", "raw"),
            ("Owner Email", "raw"),  # already the pseudonym / [redacted]
        ),
    ),
    "vds:groups": (
        "state.vds_group_members",
        (
            ("Group LUID", "raw"),
            ("Group Name", "raw"),
            ("Group Minimum Site Role", "raw"),
            ("Group Is Licensed On Site", "bool"),
            ("User LUID", "raw"),  # already the pseudonym
        ),
    ),
    "vds:permissions": (
        "state.user_capabilities",
        (
            ("Item LUID", "raw"),
            ("Item Type", "raw"),
            ("Item Name", "raw"),
            ("Item Parent Project Name", "raw"),
            ("Top Parent Project Name", "raw"),
            ("Controlling Permissions Project Name", "raw"),
            ("Capability Type", "raw"),
            ("Permission Value", "int"),
            ("Permissions Description", "raw"),
            ("Has Permission?", "bool"),
            ("User LUID", "raw"),  # already the pseudonym
            ("User Site Role", "raw"),
        ),
    ),
    "vds:subscriptions": (
        "state.subscription_health",
        (
            ("Subscription LUID", "raw"),
            ("Subscription ID", "int"),
            ("Subscription Status", "raw"),
            ("Data Conditions", "raw"),
            ("Has Image Attached", "bool"),
            ("Has PDF Attached", "bool"),
            ("Is Extract Refresh Triggered", "bool"),
            ("Item LUID", "raw"),
            ("Item Type", "raw"),
            ("Item Name", "raw"),
            ("Schedule LUID", "raw"),
            ("Schedule Name", "raw"),
            ("Schedule Type", "raw"),
            ("Created At", "ts"),
            ("Last Sent", "ts"),
            ("Task LUID", "raw"),
            ("Task Type", "raw"),
            ("Consecutive Failure Count", "int"),
            ("Historical Queue Time", "float"),
            ("Historical Run Time", "float"),
        ),
    ),
    "vds:viz_load_times": (
        "state.viz_loads",
        (
            ("Request ID", "raw"),
            ("Request Time", "ts"),
            ("Duration", "float"),
            ("Status Code", "raw"),
            ("Status Code Type", "raw"),
            ("Item Luid", "raw"),
            ("Item Type", "raw"),
            ("Item Name", "raw"),
            ("Item Repository URL", "raw"),
            ("Project Name", "raw"),
            ("Workbook Name", "raw"),
        ),
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
    elif endpoint in _VDS_STATE:
        table, columns = _VDS_STATE[endpoint]
        for row in payload.get("data", []):
            if not isinstance(row, dict) or not any(v is not None for v in row.values()):
                continue  # all-null placeholder row of an empty extract
            rows[table].append(
                [run_id] + [_convert(kind, row.get(caption)) for caption, kind in columns]
            )
    elif endpoint == "graphql:datasources":
        _metadata_datasources(payload, run_id, rows)
    elif endpoint == "graphql:workbooks":
        _metadata_workbooks(payload, run_id, rows)
    # unknown endpoints (future additions) are simply not typed yet


_PERMISSION_ENDPOINTS: dict[str, tuple[str, bool, str | None]] = {
    rest.PROJECT_PERMISSIONS: ("project", False, None),
    rest.PROJECT_DEFAULT_PERMISSIONS_WORKBOOKS: ("project", True, "workbook"),
    rest.PROJECT_DEFAULT_PERMISSIONS_DATASOURCES: ("project", True, "datasource"),
    rest.WORKBOOK_PERMISSIONS: ("workbook", False, None),
    rest.DATASOURCE_PERMISSIONS: ("datasource", False, None),
}


def _metadata_datasources(payload: dict[str, Any], run_id: int, rows: Rows) -> None:
    connection = payload.get("data", {}).get("publishedDatasourcesConnection", {})
    for node in connection.get("nodes") or []:
        _datasource_rows(
            run_id,
            rows,
            fields=node.get("fields") or [],
            upstream_tables=node.get("upstreamTables") or [],
            node_id=node.get("id"),
            luid=node.get("luid"),
            name=node.get("name"),
            workbook_luid=None,
            is_embedded=False,
        )


def _metadata_workbooks(payload: dict[str, Any], run_id: int, rows: Rows) -> None:
    connection = payload.get("data", {}).get("workbooksConnection", {})
    for node in connection.get("nodes") or []:
        workbook_luid = node.get("luid")
        for ds in node.get("upstreamDatasources") or []:
            rows["state.workbook_datasources"].append(
                [run_id, workbook_luid, ds.get("id"), ds.get("luid"), ds.get("name"), False]
            )
        for embedded in node.get("embeddedDatasources") or []:
            rows["state.workbook_datasources"].append(
                [run_id, workbook_luid, embedded.get("id"), None, embedded.get("name"), True]
            )
            _datasource_rows(
                run_id,
                rows,
                fields=embedded.get("fields") or [],
                upstream_tables=embedded.get("upstreamTables") or [],
                node_id=embedded.get("id"),
                luid=None,
                name=embedded.get("name"),
                workbook_luid=workbook_luid,
                is_embedded=True,
            )
        for sheet in node.get("sheets") or []:
            for kind, block in (
                ("worksheet", "worksheetFields"),
                ("datasource", "datasourceFields"),
            ):
                for field in sheet.get(block) or []:
                    rows["state.sheet_fields"].append(
                        [
                            run_id,
                            workbook_luid,
                            sheet.get("id"),
                            sheet.get("name"),
                            field.get("id"),
                            field.get("name"),
                            kind,
                        ]
                    )


def _datasource_rows(
    run_id: int,
    rows: Rows,
    fields: list[dict[str, Any]],
    upstream_tables: list[dict[str, Any]],
    node_id: str | None,
    luid: str | None,
    name: str | None,
    workbook_luid: str | None,
    is_embedded: bool,
) -> None:
    for field in fields:
        rows["state.datasource_fields"].append(
            [
                run_id,
                workbook_luid,
                node_id,
                luid,
                name,
                is_embedded,
                field.get("id"),
                field.get("name"),
                field.get("__typename"),
                field.get("role"),
                field.get("dataType"),
                _bool(field.get("isHidden")),
                field.get("formula"),
            ]
        )
    for table in upstream_tables:
        database = table.get("database") or {}
        rows["state.upstream_tables"].append(
            [
                run_id,
                node_id,
                table.get("id"),
                table.get("name"),
                table.get("schema"),
                table.get("fullName"),
                _bool(table.get("isEmbedded")),
                database.get("name"),
                database.get("connectionType"),
            ]
        )


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


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    if isinstance(value, (int, float)):  # VDS may surface booleans as 0/1
        return bool(value)
    return None


_CONVERTERS = {"raw": lambda v: v, "int": _int, "float": _float, "bool": _bool, "ts": _ts}


def _convert(kind: str, value: Any) -> Any:
    return _CONVERTERS[kind](value)
