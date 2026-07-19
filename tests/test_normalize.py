"""normalize: raw pages -> typed state tables, rebuilt idempotently per run."""

from pathlib import Path

import pytest

from tca.normalize import normalize_run
from tca.storage.writer import PackageStore


@pytest.fixture
def store(tmp_path: Path):
    with PackageStore(tmp_path / "pkg.duckdb") as s:
        yield s


def seed_raw(store: PackageStore, run_id: int) -> None:
    store.write_response(
        run_id,
        "/users",
        {
            "users": {
                "user": [
                    {
                        "id": "U-0001",
                        "name": "U-0001",
                        "siteRole": "Creator",
                        "authSetting": "TableauIDWithMFA",
                        "lastLogin": "2026-05-30T09:12:00Z",
                    },
                    {"id": "U-0002", "siteRole": "Viewer"},
                ]
            }
        },
    )
    store.write_response(
        run_id,
        "/groups",
        {
            "groups": {
                "group": [
                    {
                        "id": "g-1",
                        "name": "Finance",
                        "domain": {"name": "local"},
                        "import": {"minimumSiteRole": "Viewer"},
                    }
                ]
            }
        },
    )
    store.write_response(
        run_id,
        "/groups/{luid}/users",
        {"users": {"user": [{"id": "U-0001"}, {"id": "U-0002"}]}},
        entity_luid="g-1",
    )
    store.write_response(
        run_id,
        "/projects",
        {
            "projects": {
                "project": [
                    {
                        "id": "p-1",
                        "name": "Finance",
                        "contentPermissions": "LockedToProject",
                        "owner": {"id": "U-0001"},
                    }
                ]
            }
        },
    )
    store.write_response(
        run_id,
        "/workbooks",
        {
            "workbooks": {
                "workbook": [
                    {
                        "id": "wb-1",
                        "name": "Sales",
                        "project": {"id": "p-1"},
                        "owner": {"id": "U-0001"},
                        "createdAt": "2025-01-01T00:00:00Z",
                        "updatedAt": "2026-06-01T12:00:00Z",
                        "size": "12",
                        "tags": {"tag": [{"label": "kpi"}, {"label": "certified"}]},
                    }
                ]
            }
        },
    )
    store.write_response(
        run_id,
        "/datasources",
        {
            "datasources": {
                "datasource": [
                    {"id": "ds-1", "name": "DB", "isCertified": True, "hasExtracts": False}
                ]
            }
        },
    )
    store.write_response(
        run_id,
        "/views",
        {
            "views": {
                "view": [
                    {
                        "id": "v-1",
                        "name": "Overview",
                        "workbook": {"id": "wb-1"},
                        "contentUrl": "Sales/sheets/Overview",
                        "usage": {"totalViewCount": "412"},
                    }
                ]
            }
        },
    )
    store.write_response(
        run_id,
        "/workbooks/{luid}/connections",
        {
            "connections": {
                "connection": [
                    {
                        "id": "c-1",
                        "type": "postgres",
                        "serverAddress": "db.internal",
                        "userName": "[redacted]",
                        "embedPassword": True,
                    },
                    {"id": "c-2", "type": "hyper", "userName": "", "embedPassword": False},
                ]
            }
        },
        entity_luid="wb-1",
    )
    store.write_response(
        run_id,
        "/projects/{luid}/default-permissions/workbooks",
        {
            "permissions": {
                "granteeCapabilities": [
                    {
                        "group": {"id": "g-1"},
                        "capabilities": {
                            "capability": [
                                {"name": "Read", "mode": "Allow"},
                                {"name": "Write", "mode": "Deny"},
                            ]
                        },
                    },
                    {
                        "user": {"id": "U-0001"},
                        "capabilities": {"capability": {"name": "Read", "mode": "Allow"}},
                    },
                ]
            }
        },
        entity_luid="p-1",
    )
    # a VDS page: typed into state.user_activity since v0.6
    store.write_response(run_id, "vds:ts_users", {"data": [{"User LUID": "U-0001"}]})


def test_normalize_builds_state_tables(store: PackageStore) -> None:
    run_id = store.begin_run(modules=["rest_core", "content", "permissions"])
    seed_raw(store, run_id)
    counts = normalize_run(store, run_id)

    assert counts == {
        "state.users": 2,
        "state.groups": 1,
        "state.group_members": 2,
        "state.projects": 1,
        "state.content_items": 2,
        "state.views": 1,
        "state.connections": 2,
        "state.permission_rules": 3,
        "state.user_activity": 1,
    }

    user = store.con.execute(
        "SELECT site_role, auth_setting, last_login_at FROM state.users "
        "WHERE run_id = ? AND user_pseudo = 'U-0001'",
        [run_id],
    ).fetchone()
    assert user[0] == "Creator"
    assert str(user[2]).startswith("2026-05-30 09:12")  # Z-suffixed ISO parsed to naive UTC

    wb = store.con.execute(
        "SELECT item_type, project_luid, owner_pseudo, size_raw, tags "
        "FROM state.content_items WHERE item_luid = 'wb-1'"
    ).fetchone()
    assert wb == ("workbook", "p-1", "U-0001", 12, ["kpi", "certified"])

    conn = store.con.execute(
        "SELECT has_embedded_credentials, cred_user_present FROM state.connections "
        "WHERE connection_luid = 'c-1'"
    ).fetchone()
    assert conn == (True, True)
    conn2 = store.con.execute(
        "SELECT cred_user_present FROM state.connections WHERE connection_luid = 'c-2'"
    ).fetchone()
    assert conn2 == (False,)

    rules = store.con.execute(
        "SELECT grantee_type, grantee_id, capability, mode, is_default_template, template_for "
        "FROM state.permission_rules ORDER BY rule_id"
    ).fetchall()
    assert rules == [
        ("group", "g-1", "Read", "Allow", True, "workbook"),
        ("group", "g-1", "Write", "Deny", True, "workbook"),
        ("user", "U-0001", "Read", "Allow", True, "workbook"),
    ]


def test_normalize_types_vds_pages(store: PackageStore) -> None:
    run_id = store.begin_run(modules=["activity"])
    store.write_response(
        run_id,
        "vds:ts_users",
        {
            "data": [
                {
                    "User ID": 42,
                    "User LUID": "U-0001",
                    "User Site Role": "Creator",
                    "User License Type": "Creator",
                    "User Creation Date": "2025-01-01T00:00:00",
                    "Last Login Date": "2026-07-01T10:00:00",
                    "Days Since Last Login": 17,
                }
            ]
        },
        entity_luid="ai-users",
    )
    store.write_response(
        run_id,
        "vds:site_content",
        {
            "data": [
                # one real row + the all-null placeholder of an empty extract
                {
                    "Item LUID": "wb-1",
                    "Item Type": "Workbook",
                    "Item Name": "Sales",
                    "Last Accessed At": "2026-06-30T08:00:00",
                    "Size (bytes)": 1024,
                    "Is Data Extract": "true",
                },
                {"Item LUID": None, "Item Name": None},
            ]
        },
        entity_luid="ai-content",
    )
    store.write_response(
        run_id,
        "vds:permissions",
        {
            "data": [
                {
                    "Item LUID": "wb-1",
                    "Item Type": "Workbook",
                    "Capability Type": "Read",
                    "Has Permission?": True,
                    "User LUID": "U-0001",
                    "User Site Role": "Viewer",
                }
            ]
        },
        entity_luid="ai-perms",
    )
    counts = normalize_run(store, run_id)
    assert counts == {
        "state.user_activity": 1,
        "state.content_usage": 1,  # placeholder row skipped
        "state.user_capabilities": 1,
    }

    activity = store.con.execute(
        "SELECT user_id, user_pseudo, days_since_last_login, last_login_at FROM state.user_activity"
    ).fetchone()
    assert activity[:3] == (42, "U-0001", 17)
    assert str(activity[3]).startswith("2026-07-01 10:00")

    usage = store.con.execute(
        "SELECT item_luid, size_bytes, is_data_extract, last_accessed_at FROM state.content_usage"
    ).fetchone()
    assert usage[:3] == ("wb-1", 1024, True)

    capability = store.con.execute(
        "SELECT item_luid, capability, has_permission, user_pseudo FROM state.user_capabilities"
    ).fetchone()
    assert capability == ("wb-1", "Read", True, "U-0001")


def test_normalize_types_metadata_pages(store: PackageStore) -> None:
    run_id = store.begin_run(modules=["metadata"])
    store.write_response(
        run_id,
        "graphql:datasources",
        {
            "data": {
                "publishedDatasourcesConnection": {
                    "nodes": [
                        {
                            "id": "ds-node-1",
                            "luid": "ds-1",
                            "name": "Sales Model",
                            "fields": [
                                {
                                    "id": "f-1",
                                    "name": "Net Revenue",
                                    "__typename": "CalculatedField",
                                    "role": "MEASURE",
                                    "dataType": "REAL",
                                    "isHidden": False,
                                    "formula": "[Gross]-[Costs]",
                                }
                            ],
                            "upstreamTables": [
                                {
                                    "id": "t-1",
                                    "name": "orders",
                                    "schema": "public",
                                    "fullName": "[public].[orders]",
                                    "isEmbedded": False,
                                    "database": {"name": "dwh", "connectionType": "postgres"},
                                }
                            ],
                        }
                    ]
                }
            }
        },
    )
    store.write_response(
        run_id,
        "graphql:workbooks",
        {
            "data": {
                "workbooksConnection": {
                    "nodes": [
                        {
                            "id": "wb-node-1",
                            "luid": "wb-1",
                            "name": "Sales",
                            "upstreamDatasources": [
                                {"id": "ds-node-1", "luid": "ds-1", "name": "Sales Model"}
                            ],
                            "embeddedDatasources": [
                                {
                                    "id": "emb-1",
                                    "name": "local extract",
                                    "fields": [
                                        {
                                            "id": "f-2",
                                            "name": "Region",
                                            "__typename": "ColumnField",
                                            "role": "DIMENSION",
                                            "dataType": "STRING",
                                        }
                                    ],
                                    "upstreamTables": [],
                                }
                            ],
                            "sheets": [
                                {
                                    "id": "sh-1",
                                    "name": "Overview",
                                    "worksheetFields": [{"id": "wf-1", "name": "Region"}],
                                    "datasourceFields": [{"id": "f-1", "name": "Net Revenue"}],
                                }
                            ],
                        }
                    ]
                }
            }
        },
    )
    counts = normalize_run(store, run_id)
    assert counts == {
        "state.datasource_fields": 2,
        "state.upstream_tables": 1,
        "state.sheet_fields": 2,
        "state.workbook_datasources": 2,
    }

    formula = store.con.execute(
        "SELECT datasource_luid, is_embedded, formula FROM state.datasource_fields "
        "WHERE field_node_id = 'f-1'"
    ).fetchone()
    assert formula == ("ds-1", False, "[Gross]-[Costs]")
    embedded = store.con.execute(
        "SELECT workbook_luid, is_embedded, formula FROM state.datasource_fields "
        "WHERE field_node_id = 'f-2'"
    ).fetchone()
    assert embedded == ("wb-1", True, None)

    table = store.con.execute(
        "SELECT datasource_node_id, table_schema, database_type FROM state.upstream_tables"
    ).fetchone()
    assert table == ("ds-node-1", "public", "postgres")

    kinds = store.con.execute(
        "SELECT field_name, kind FROM state.sheet_fields ORDER BY kind DESC"
    ).fetchall()
    assert kinds == [("Region", "worksheet"), ("Net Revenue", "datasource")]

    links = store.con.execute(
        "SELECT datasource_name, is_embedded FROM state.workbook_datasources ORDER BY is_embedded"
    ).fetchall()
    assert links == [("Sales Model", False), ("local extract", True)]


def test_normalize_is_idempotent(store: PackageStore) -> None:
    run_id = store.begin_run(modules=["rest_core"])
    seed_raw(store, run_id)
    normalize_run(store, run_id)
    normalize_run(store, run_id)  # rebuild: no duplicates
    count = store.con.execute(
        "SELECT count(*) FROM state.users WHERE run_id = ?", [run_id]
    ).fetchone()[0]
    assert count == 2


def test_current_views_follow_latest_run_that_collected_the_data(store: PackageStore) -> None:
    """The partial-run wart (fixed in 007): a run collecting a SUBSET of the
    endpoints must refresh only the views of what it collected — everything
    else keeps pointing at the last run that actually collected it."""
    run1 = store.begin_run(modules=["rest_core", "content", "permissions"])
    seed_raw(store, run1)
    normalize_run(store, run1)
    store.finish_run(run1, "ok")

    # a users-only partial run
    run2 = store.begin_run(modules=["rest_core"])
    store.write_response(run2, "/users", {"users": {"user": [{"id": "U-0003"}]}})
    normalize_run(store, run2)
    store.finish_run(run2, "ok")

    current = store.con.execute("SELECT user_pseudo FROM state.v_users_current").fetchall()
    assert current == [("U-0003",)]  # users follow run2

    # groups, content and permission rules were NOT collected by run2:
    # their views still show run1 instead of going empty
    groups = store.con.execute("SELECT run_id, name FROM state.v_groups_current").fetchall()
    assert groups == [(run1, "Finance")]
    content = store.con.execute("SELECT DISTINCT run_id FROM state.v_content_current").fetchall()
    assert content == [(run1,)]
    rules = store.con.execute("SELECT count(*) FROM state.v_permission_rules_current").fetchone()
    assert rules == (3,)

    # an empty listing still counts as collected: no stale ghosts
    run3 = store.begin_run(modules=["rest_core"])
    store.write_response(run3, "/users", {"users": {}})
    normalize_run(store, run3)
    store.finish_run(run3, "ok")
    assert store.con.execute("SELECT count(*) FROM state.v_users_current").fetchone() == (0,)
