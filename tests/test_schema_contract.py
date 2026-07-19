"""THE SCHEMA CONTRACT — the analyst-side compatibility guarantee.

The package file schema is ADDITIVE-ONLY: analysis engines built on today's
tables must keep working against every future collector version. Concretely:

* every table and column listed below MUST keep existing, with the same type;
* NEW tables and NEW columns are welcome (add them to the contract);
* renaming or dropping anything listed here is a BREAKING CHANGE and this
  test will fail — that is its job, do not "fix" the test to make a PR green.

If you genuinely believe a listed column must go, open an issue first: the
answer will almost always be "add a new column and deprecate the old one".

The golden snapshot below is intentionally spelled out (not generated at
runtime): reviewers must SEE contract changes in the diff.
"""

from pathlib import Path

import pytest

from tca.storage.writer import PackageStore

# ---------------------------------------------------------------------------
# Golden contract — schema v0.5 (2026-07-19)
# ---------------------------------------------------------------------------

GOLDEN_TABLES: dict[str, dict[str, str]] = {
    "meta.file_info": {
        "site_luid": "VARCHAR",
        "site_name": "VARCHAR",
        "pod": "VARCHAR",
        "file_created_at": "TIMESTAMP",
        "schema_version": "VARCHAR",
        "is_encrypted": "BOOLEAN",
    },
    "meta.schema_migrations": {
        "version": "VARCHAR",
        "filename": "VARCHAR",
        "applied_at": "TIMESTAMP",
        "collector_version": "VARCHAR",
    },
    "meta.collection_runs": {
        "run_id": "INTEGER",
        "started_at": "TIMESTAMP",
        "finished_at": "TIMESTAMP",
        "status": "VARCHAR",
        "collector_version": "VARCHAR",
        "rest_api_version": "VARCHAR",
        "duckdb_version": "VARCHAR",
        "modules_run": "VARCHAR[]",
        "notes": "VARCHAR",
    },
    "meta.event_coverage": {
        "run_id": "INTEGER",
        "source": "VARCHAR",
        "window_start": "TIMESTAMP",
        "window_end": "TIMESTAMP",
    },
    "raw.api_responses": {
        "run_id": "INTEGER",
        "endpoint": "VARCHAR",
        "entity_luid": "VARCHAR",
        "page": "INTEGER",
        "fetched_at": "TIMESTAMP",
        "payload": "JSON",
    },
    "history.events": {
        "event_id": "BIGINT",
        "event_date": "TIMESTAMP",
        "event_name": "VARCHAR",
        "event_type": "VARCHAR",
        "item_id": "BIGINT",
        "item_luid": "VARCHAR",
        "item_type": "VARCHAR",
        "item_name": "VARCHAR",
        "project_name": "VARCHAR",
        "actor_user_id": "BIGINT",
        "actor_site_role": "VARCHAR",
        "actor_license_role": "VARCHAR",
        "item_owner_id": "BIGINT",
        "target_user_id": "BIGINT",
        "first_seen_run": "INTEGER",
    },
    "history.job_runs": {
        "job_id": "BIGINT",
        "job_luid": "VARCHAR",
        "job_type": "VARCHAR",
        "job_result": "VARCHAR",
        "final_job_result": "VARCHAR",
        "was_manual_run": "BOOLEAN",
        "item_id": "BIGINT",
        "item_luid": "VARCHAR",
        "item_type": "VARCHAR",
        "item_name": "VARCHAR",
        "parent_project_name": "VARCHAR",
        "schedule_luid": "VARCHAR",
        "schedule_name": "VARCHAR",
        "created_at": "TIMESTAMP",
        "queued_at": "TIMESTAMP",
        "started_at": "TIMESTAMP",
        "completed_at": "TIMESTAMP",
        "job_duration": "DOUBLE",
        "job_queued_duration": "DOUBLE",
        "job_execution_duration": "DOUBLE",
        "job_overflow_queued_duration": "DOUBLE",
        "was_overflow_queued": "BOOLEAN",
        "extract_file_size": "DOUBLE",
        "subscriber_id": "BIGINT",
        "owner_email": "VARCHAR",
        "first_seen_run": "INTEGER",
    },
    "state.users": {
        "run_id": "INTEGER",
        "user_pseudo": "VARCHAR",
        "site_role": "VARCHAR",
        "auth_setting": "VARCHAR",
        "last_login_at": "TIMESTAMP",
    },
    "state.groups": {
        "run_id": "INTEGER",
        "group_luid": "VARCHAR",
        "name": "VARCHAR",
        "domain": "VARCHAR",
        "min_site_role": "VARCHAR",
    },
    "state.group_members": {
        "run_id": "INTEGER",
        "group_luid": "VARCHAR",
        "user_pseudo": "VARCHAR",
    },
    "state.projects": {
        "run_id": "INTEGER",
        "project_luid": "VARCHAR",
        "name": "VARCHAR",
        "parent_luid": "VARCHAR",
        "content_permissions": "VARCHAR",
        "owner_pseudo": "VARCHAR",
    },
    "state.content_items": {
        "run_id": "INTEGER",
        "item_luid": "VARCHAR",
        "item_type": "VARCHAR",
        "name": "VARCHAR",
        "project_luid": "VARCHAR",
        "owner_pseudo": "VARCHAR",
        "created_at": "TIMESTAMP",
        "updated_at": "TIMESTAMP",
        "size_raw": "BIGINT",
        "is_certified": "BOOLEAN",
        "has_extracts": "BOOLEAN",
        "tags": "VARCHAR[]",
    },
    "state.views": {
        "run_id": "INTEGER",
        "view_luid": "VARCHAR",
        "workbook_luid": "VARCHAR",
        "name": "VARCHAR",
        "content_url": "VARCHAR",
        "total_views_alltime": "BIGINT",
    },
    "state.connections": {
        "run_id": "INTEGER",
        "item_luid": "VARCHAR",
        "connection_luid": "VARCHAR",
        "conn_type": "VARCHAR",
        "server_address": "VARCHAR",
        "has_embedded_credentials": "BOOLEAN",
        "cred_user_present": "BOOLEAN",
    },
    "state.permission_rules": {
        "run_id": "INTEGER",
        "rule_id": "BIGINT",
        "target_luid": "VARCHAR",
        "target_type": "VARCHAR",
        "is_default_template": "BOOLEAN",
        "template_for": "VARCHAR",
        "grantee_type": "VARCHAR",
        "grantee_id": "VARCHAR",
        "capability": "VARCHAR",
        "mode": "VARCHAR",
    },
    "state.user_activity": {
        "run_id": "INTEGER",
        "user_id": "BIGINT",
        "user_pseudo": "VARCHAR",
        "site_role": "VARCHAR",
        "license_type": "VARCHAR",
        "user_created_at": "TIMESTAMP",
        "last_login_at": "TIMESTAMP",
        "days_since_last_login": "BIGINT",
    },
    "state.content_usage": {
        "run_id": "INTEGER",
        "item_id": "BIGINT",
        "item_luid": "VARCHAR",
        "item_type": "VARCHAR",
        "name": "VARCHAR",
        "parent_project_name": "VARCHAR",
        "top_project_name": "VARCHAR",
        "project_level": "BIGINT",
        "created_at": "TIMESTAMP",
        "updated_at": "TIMESTAMP",
        "first_published_at": "TIMESTAMP",
        "last_published_at": "TIMESTAMP",
        "last_accessed_at": "TIMESTAMP",
        "size_bytes": "BIGINT",
        "is_data_extract": "BOOLEAN",
        "has_refresh_scheduled": "BOOLEAN",
        "is_certified": "BOOLEAN",
        "database_type": "VARCHAR",
        "view_workbook_id": "BIGINT",
        "view_type": "VARCHAR",
        "controlled_permissions_enabled": "BOOLEAN",
        "controlling_project_luid": "VARCHAR",
        "tags": "VARCHAR",
    },
    "state.tokens": {
        "run_id": "INTEGER",
        "guid": "VARCHAR",
        "token_identifier": "VARCHAR",
        "token_type": "VARCHAR",
        "pat_name": "VARCHAR",
        "issued_at": "TIMESTAMP",
        "expires_at": "TIMESTAMP",
        "last_used_at": "TIMESTAMP",
        "last_updated_at": "TIMESTAMP",
        "database_type": "VARCHAR",
        "owner_pseudo": "VARCHAR",
    },
    "state.vds_group_members": {
        "run_id": "INTEGER",
        "group_luid": "VARCHAR",
        "group_name": "VARCHAR",
        "min_site_role": "VARCHAR",
        "licensed_on_sign_in": "BOOLEAN",
        "user_pseudo": "VARCHAR",
    },
    "state.user_capabilities": {
        "run_id": "INTEGER",
        "item_luid": "VARCHAR",
        "item_type": "VARCHAR",
        "item_name": "VARCHAR",
        "parent_project_name": "VARCHAR",
        "top_project_name": "VARCHAR",
        "controlling_project_name": "VARCHAR",
        "capability": "VARCHAR",
        "permission_value": "BIGINT",
        "permission_description": "VARCHAR",
        "has_permission": "BOOLEAN",
        "user_pseudo": "VARCHAR",
        "user_site_role": "VARCHAR",
    },
    "state.subscription_health": {
        "run_id": "INTEGER",
        "subscription_luid": "VARCHAR",
        "subscription_id": "BIGINT",
        "status": "VARCHAR",
        "data_conditions": "VARCHAR",
        "has_image": "BOOLEAN",
        "has_pdf": "BOOLEAN",
        "extract_refresh_triggered": "BOOLEAN",
        "item_luid": "VARCHAR",
        "item_type": "VARCHAR",
        "item_name": "VARCHAR",
        "schedule_luid": "VARCHAR",
        "schedule_name": "VARCHAR",
        "schedule_type": "VARCHAR",
        "created_at": "TIMESTAMP",
        "last_sent_at": "TIMESTAMP",
        "task_luid": "VARCHAR",
        "task_type": "VARCHAR",
        "consecutive_failures": "BIGINT",
        "queued_seconds": "DOUBLE",
        "run_seconds": "DOUBLE",
    },
    "state.viz_loads": {
        "run_id": "INTEGER",
        "request_id": "VARCHAR",
        "request_time": "TIMESTAMP",
        "duration_seconds": "DOUBLE",
        "status_code": "VARCHAR",
        "status_type": "VARCHAR",
        "item_luid": "VARCHAR",
        "item_type": "VARCHAR",
        "item_name": "VARCHAR",
        "repository_url": "VARCHAR",
        "project_name": "VARCHAR",
        "workbook_name": "VARCHAR",
    },
    "state.datasource_fields": {
        "run_id": "INTEGER",
        "workbook_luid": "VARCHAR",
        "datasource_node_id": "VARCHAR",
        "datasource_luid": "VARCHAR",
        "datasource_name": "VARCHAR",
        "is_embedded": "BOOLEAN",
        "field_node_id": "VARCHAR",
        "name": "VARCHAR",
        "field_type": "VARCHAR",
        "role": "VARCHAR",
        "data_type": "VARCHAR",
        "is_hidden": "BOOLEAN",
        "formula": "VARCHAR",
    },
    "state.upstream_tables": {
        "run_id": "INTEGER",
        "datasource_node_id": "VARCHAR",
        "table_node_id": "VARCHAR",
        "name": "VARCHAR",
        "table_schema": "VARCHAR",
        "full_name": "VARCHAR",
        "is_embedded": "BOOLEAN",
        "database_name": "VARCHAR",
        "database_type": "VARCHAR",
    },
    "state.sheet_fields": {
        "run_id": "INTEGER",
        "workbook_luid": "VARCHAR",
        "sheet_node_id": "VARCHAR",
        "sheet_name": "VARCHAR",
        "field_node_id": "VARCHAR",
        "field_name": "VARCHAR",
        "kind": "VARCHAR",
    },
    "state.workbook_datasources": {
        "run_id": "INTEGER",
        "workbook_luid": "VARCHAR",
        "datasource_node_id": "VARCHAR",
        "datasource_luid": "VARCHAR",
        "datasource_name": "VARCHAR",
        "is_embedded": "BOOLEAN",
    },
    "identity.map": {
        "pseudonym": "VARCHAR",
        "user_luid": "VARCHAR",
        "name": "VARCHAR",
        "full_name": "VARCHAR",
        "email": "VARCHAR",
        "external_auth_user_id": "VARCHAR",
        "first_seen_run": "INTEGER",
        "last_seen_run": "INTEGER",
    },
}

GOLDEN_VIEWS: set[str] = {
    "meta.v_latest_run",
    "meta.v_event_gaps",
    "state.v_users_current",
    "state.v_groups_current",
    "state.v_content_current",
    "state.v_permission_rules_current",
    "clear.users",
    "clear.group_members",
    "clear.content_owners",
    "clear.permission_rules",
}


@pytest.fixture(scope="module")
def fresh_schema(tmp_path_factory: pytest.TempPathFactory) -> dict:
    path: Path = tmp_path_factory.mktemp("contract") / "fresh.duckdb"
    with PackageStore(path) as store:
        columns = store.con.execute(
            """
            SELECT c.schema_name || '.' || c.table_name, c.column_name, c.data_type
            FROM duckdb_columns() c
            JOIN duckdb_tables() t
              ON t.database_name = c.database_name
             AND t.schema_name = c.schema_name
             AND t.table_name = c.table_name
            WHERE c.database_name = 'pkg'
            """
        ).fetchall()
        views = store.con.execute(
            "SELECT schema_name || '.' || view_name FROM duckdb_views() "
            "WHERE database_name = 'pkg' AND NOT internal"
        ).fetchall()
    tables: dict[str, dict[str, str]] = {}
    for table, column, data_type in columns:
        tables.setdefault(table, {})[column] = data_type
    return {"tables": tables, "views": {v[0] for v in views}}


def test_every_contract_table_and_column_still_exists(fresh_schema: dict) -> None:
    problems: list[str] = []
    for table, golden_columns in GOLDEN_TABLES.items():
        actual = fresh_schema["tables"].get(table)
        if actual is None:
            problems.append(f"table '{table}' disappeared")
            continue
        for column, expected_type in golden_columns.items():
            if column not in actual:
                problems.append(f"column '{table}.{column}' disappeared")
            elif actual[column] != expected_type:
                problems.append(
                    f"column '{table}.{column}' changed type: {expected_type} -> {actual[column]}"
                )
    assert not problems, (
        "SCHEMA CONTRACT VIOLATED (additive-only!). Analysis engines depend on "
        "these definitions:\n  - " + "\n  - ".join(problems)
    )


def test_every_contract_view_still_exists(fresh_schema: dict) -> None:
    missing = GOLDEN_VIEWS - fresh_schema["views"]
    assert not missing, "SCHEMA CONTRACT VIOLATED: views disappeared: " + ", ".join(sorted(missing))


def test_new_tables_are_added_to_the_contract(fresh_schema: dict) -> None:
    """Additions are welcome — but they must become part of the contract, so
    future changes to them are guarded too."""
    unlisted = set(fresh_schema["tables"]) - set(GOLDEN_TABLES)
    assert not unlisted, (
        "New table(s) not yet in the schema contract — add them to GOLDEN_TABLES "
        "in this file: " + ", ".join(sorted(unlisted))
    )
