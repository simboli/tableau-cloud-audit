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
    # a VDS page: must be ignored by normalize, not crash it
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


def test_normalize_is_idempotent(store: PackageStore) -> None:
    run_id = store.begin_run(modules=["rest_core"])
    seed_raw(store, run_id)
    normalize_run(store, run_id)
    normalize_run(store, run_id)  # rebuild: no duplicates
    count = store.con.execute(
        "SELECT count(*) FROM state.users WHERE run_id = ?", [run_id]
    ).fetchone()[0]
    assert count == 2


def test_current_views_follow_latest_ok_run(store: PackageStore) -> None:
    run1 = store.begin_run(modules=["rest_core"])
    seed_raw(store, run1)
    normalize_run(store, run1)
    store.finish_run(run1, "ok")

    run2 = store.begin_run(modules=["rest_core"])
    store.write_response(run2, "/users", {"users": {"user": [{"id": "U-0003"}]}})
    normalize_run(store, run2)
    store.finish_run(run2, "ok")

    current = store.con.execute("SELECT user_pseudo FROM state.v_users_current").fetchall()
    assert current == [("U-0003",)]  # only the latest ok run
