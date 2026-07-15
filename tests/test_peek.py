"""Clear views: real identities at read time, locally only — never in the export."""

from pathlib import Path

import duckdb
import pytest

from tca.normalize import normalize_run
from tca.storage.writer import PackageStore


@pytest.fixture
def populated(tmp_path: Path):
    """A store with one ok run: users, a group with members, a workbook, a rule."""
    with PackageStore(tmp_path / "pkg.duckdb") as store:
        run_id = store.begin_run(modules=["rest_core"])
        store.upsert_identity(
            run_id, "luid-a", name="mrossi", full_name="Mario Rossi", email="mario@acme.it"
        )  # -> U-0001
        store.write_response(
            run_id,
            "/users",
            {"users": {"user": [{"id": "U-0001", "siteRole": "Creator"}, {"id": "U-9999"}]}},
        )
        store.write_response(
            run_id, "/groups", {"groups": {"group": [{"id": "g-1", "name": "Finance"}]}}
        )
        store.write_response(
            run_id,
            "/groups/{luid}/users",
            {"users": {"user": [{"id": "U-0001"}]}},
            entity_luid="g-1",
        )
        store.write_response(
            run_id,
            "/workbooks",
            {
                "workbooks": {
                    "workbook": [{"id": "wb-1", "name": "Sales", "owner": {"id": "U-0001"}}]
                }
            },
        )
        store.write_response(
            run_id,
            "/workbooks/{luid}/permissions",
            {
                "permissions": {
                    "granteeCapabilities": [
                        {
                            "group": {"id": "g-1"},
                            "capabilities": {"capability": [{"name": "Read", "mode": "Allow"}]},
                        },
                        {
                            "user": {"id": "U-0001"},
                            "capabilities": {"capability": [{"name": "Write", "mode": "Allow"}]},
                        },
                    ]
                }
            },
            entity_luid="wb-1",
        )
        normalize_run(store, run_id)
        store.finish_run(run_id, "ok")
        yield store


def test_clear_users_resolves_identity(populated: PackageStore) -> None:
    rows = populated.con.execute(
        "SELECT user_pseudo, full_name, email, site_role FROM clear.users ORDER BY user_pseudo"
    ).fetchall()
    assert rows[0] == ("U-0001", "Mario Rossi", "mario@acme.it", "Creator")
    # a pseudonym without a vault entry still shows, with NULL identity
    assert rows[1][0] == "U-9999"
    assert rows[1][1] is None


def test_clear_group_members_and_content(populated: PackageStore) -> None:
    member = populated.con.execute("SELECT group_name, email FROM clear.group_members").fetchone()
    assert member == ("Finance", "mario@acme.it")
    owner = populated.con.execute(
        "SELECT item_name, owner_email FROM clear.content_owners"
    ).fetchone()
    assert owner == ("Sales", "mario@acme.it")


def test_clear_rules_resolve_user_and_group_grantees(populated: PackageStore) -> None:
    rules = populated.con.execute(
        "SELECT grantee_type, grantee, capability FROM clear.permission_rules ORDER BY grantee_type"
    ).fetchall()
    assert ("group", "Finance", "Read") in rules
    assert ("user", "mario@acme.it", "Write") in rules


def test_export_cannot_reproduce_clear_data(populated: PackageStore, tmp_path: Path) -> None:
    dest = tmp_path / "export.duckdb"
    populated.export_redacted(dest)
    con = duckdb.connect(str(dest), read_only=True)
    schemas = {r[0] for r in con.execute("SELECT schema_name FROM duckdb_tables()").fetchall()}
    assert "clear" not in schemas  # views are not tables: nothing clear travels
    assert "identity" not in schemas  # and the vault to rebuild them is absent
    exported = str(con.execute("SELECT * FROM state.users").fetchall())
    assert "mario" not in exported and "acme.it" not in exported
    con.close()
