"""Pseudonymizer tests: manifest-driven scrubbing, pseudonym stability, safety net."""

from pathlib import Path

import pytest

from tca.pseudo.scrubber import Scrubber, ScrubError
from tca.storage.writer import PackageStore


@pytest.fixture
def store(tmp_path: Path):
    with PackageStore(tmp_path / "test.duckdb") as s:
        yield s


@pytest.fixture
def scrubber(store: PackageStore) -> Scrubber:
    run_id = store.begin_run(modules=["rest_core"])
    return Scrubber(store, run_id)


USERS_PAGE = {
    "pagination": {"pageNumber": "1", "pageSize": "100", "totalAvailable": "2"},
    "users": {
        "user": [
            {
                "id": "aa11bb22-0000-1111-2222-333344445555",
                "name": "mario.rossi@acme.it",
                "fullName": "Mario Rossi",
                "email": "mario.rossi@acme.it",
                "siteRole": "Creator",
                "lastLogin": "2026-05-30T09:12:00Z",
            },
            {
                "id": "cc33dd44-0000-1111-2222-333344445555",
                "name": "lbianchi",
                "fullName": "Lucia Bianchi",
                "email": "lucia.bianchi@acme.it",
                "externalAuthUserId": "lbianchi@corp",
                "siteRole": "Viewer",
            },
        ]
    },
}


def test_users_page_is_fully_pseudonymised(scrubber: Scrubber) -> None:
    out = scrubber.scrub("/users", USERS_PAGE)
    u1, u2 = out["users"]["user"]
    assert u1["id"] == u1["name"] == u1["email"] == u1["fullName"] == "U-0001"
    assert u2["id"] == "U-0002"
    assert u2["externalAuthUserId"] == "U-0002"
    # non-PII survives untouched
    assert u1["siteRole"] == "Creator"
    assert u1["lastLogin"] == "2026-05-30T09:12:00Z"
    assert out["pagination"]["totalAvailable"] == "2"


def test_original_payload_is_not_mutated(scrubber: Scrubber) -> None:
    scrubber.scrub("/users", USERS_PAGE)
    assert USERS_PAGE["users"]["user"][0]["email"] == "mario.rossi@acme.it"


def test_pseudonyms_stable_across_endpoints_and_runs(
    store: PackageStore, scrubber: Scrubber
) -> None:
    out1 = scrubber.scrub("/users", USERS_PAGE)
    membership = {"users": {"user": [{"id": "aa11bb22-0000-1111-2222-333344445555"}]}}
    out2 = scrubber.scrub("/groups/{luid}/users", membership)
    assert out2["users"]["user"][0]["id"] == out1["users"]["user"][0]["id"] == "U-0001"

    # new run, same person, same pseudonym
    run2 = store.begin_run(modules=["rest_core"])
    out3 = Scrubber(store, run2).scrub("/users", USERS_PAGE)
    assert out3["users"]["user"][0]["id"] == "U-0001"


def test_vault_captures_identity(store: PackageStore, scrubber: Scrubber) -> None:
    scrubber.scrub("/users", USERS_PAGE)
    row = store.resolve("U-0001")
    assert row["email"] == "mario.rossi@acme.it"
    assert row["full_name"] == "Mario Rossi"
    assert row["user_luid"] == "aa11bb22-0000-1111-2222-333344445555"


def test_groups_pass_through_untouched(scrubber: Scrubber) -> None:
    groups = {"groups": {"group": [{"id": "g-1", "name": "Finance & Controlling"}]}}
    assert scrubber.scrub("/groups", groups) == groups


def test_unregistered_endpoint_is_refused(scrubber: Scrubber) -> None:
    with pytest.raises(ScrubError, match="not registered"):
        scrubber.scrub("/flows", {"flows": {}})


def test_user_object_without_id_is_refused(scrubber: Scrubber) -> None:
    with pytest.raises(ScrubError, match="no 'id'"):
        scrubber.scrub("/users", {"users": {"user": [{"name": "ghost"}]}})


def test_safety_net_blocks_surviving_email(scrubber: Scrubber) -> None:
    # a group whose name embeds an e-mail: no manifest path covers it -> block
    sneaky = {"groups": {"group": [{"id": "g-1", "name": "owners: mario.rossi@acme.it"}]}}
    with pytest.raises(ScrubError, match="e-mail-shaped"):
        scrubber.scrub("/groups", sneaky)


def test_safety_net_blocks_known_luid_leak(scrubber: Scrubber) -> None:
    scrubber.scrub("/users", USERS_PAGE)  # vault now knows the LUIDs
    leak = {"groups": {"group": [{"id": "g-1", "owner": "aa11bb22-0000-1111-2222-333344445555"}]}}
    with pytest.raises(ScrubError, match="LUID"):
        scrubber.scrub("/groups", leak)


def test_safety_net_ignores_non_luid_vault_token(store: PackageStore) -> None:
    # Defense in depth: even if a degenerate (non-UUID) token reaches the vault,
    # the substring scan must skip it instead of matching innocuous fragments of
    # legitimate data (here "NA" inside "NASA Analytics").
    run_id = store.begin_run(modules=["rest_core"])
    store.upsert_identity(run_id, user_luid="NA")
    payload = {"groups": {"group": [{"id": "g-1", "name": "NASA Analytics"}]}}
    out = Scrubber(store, run_id).scrub("/groups", payload)  # must not raise
    assert out["groups"]["group"][0]["name"] == "NASA Analytics"


def test_single_object_instead_of_list(scrubber: Scrubber) -> None:
    # Tableau REST sometimes returns a bare object where a list is expected
    page = {"users": {"user": {"id": "ee55ff66-0000-1111-2222-333344445555", "name": "solo"}}}
    out = scrubber.scrub("/users", page)
    assert out["users"]["user"]["id"] == "U-0001"


def test_empty_page_is_fine(scrubber: Scrubber) -> None:
    assert scrubber.scrub("/users", {"users": {}}) == {"users": {}}


def test_nested_owner_is_pseudonymised(scrubber: Scrubber) -> None:
    wb_page = {
        "workbooks": {
            "workbook": [
                {
                    "id": "wb-1",
                    "name": "Sales",
                    "owner": {"id": "aa11bb22-0000-1111-2222-333344445555", "name": "m@acme.it"},
                }
            ]
        }
    }
    out = scrubber.scrub("/workbooks", wb_page)
    owner = out["workbooks"]["workbook"][0]["owner"]
    assert owner["id"] == owner["name"] == "U-0001"
    assert out["workbooks"]["workbook"][0]["name"] == "Sales"  # content name in clear


def test_vds_email_columns_resolve_via_vault(store: PackageStore, scrubber: Scrubber) -> None:
    scrubber.scrub("/users", USERS_PAGE)  # vault now knows mario (U-0001)
    tokens = {
        "data": [
            {"GUID": "t-1", "PAT Name": "ci-token", "Owner Email": "MARIO.ROSSI@acme.it"},
            {"GUID": "t-2", "PAT Name": "old-token", "Owner Email": "ghost@nowhere.io"},
            {"GUID": "t-3", "PAT Name": "svc-token", "Owner Email": ""},
        ]
    }
    out = scrubber.scrub("vds:tokens", tokens)
    t1, t2, t3 = out["data"]
    assert t1["Owner Email"] == "U-0001"  # case-insensitive vault match
    assert t2["Owner Email"] == "[redacted]"  # unknown e-mail never survives
    assert t3["Owner Email"] == ""  # empty stays empty
    assert t1["PAT Name"] == "ci-token"


def test_vds_na_luid_is_not_a_user(store: PackageStore, scrubber: Scrubber) -> None:
    # Admin Insights writes the literal "NA" in User LUID for group grantees.
    # It must not be pseudonymised, must not enter the vault, and must not trip
    # the substring safety net on this or any later payload.
    scrubber.scrub("/users", USERS_PAGE)  # vault now knows the real LUIDs
    permissions = {
        "data": [
            {
                "Item Name": "Sales",
                "User LUID": "aa11bb22-0000-1111-2222-333344445555",
                "User Site Role": "Creator",
            },
            {"Item Name": "Finance", "User LUID": "NA", "User Site Role": "NA"},
        ]
    }
    out = scrubber.scrub("vds:permissions", permissions)
    user_row, group_row = out["data"]
    assert user_row["User LUID"] == "U-0001"  # real grantee pseudonymised
    assert group_row["User LUID"] == "NA"  # placeholder untouched, not PII
    assert "NA" not in store.known_luids()  # vault never poisoned


def test_connection_username_is_redacted(scrubber: Scrubber) -> None:
    payload = {
        "connections": {
            "connection": [
                {"id": "c-1", "userName": "personal.user@acme.it", "serverAddress": "db"},
                {"id": "c-2", "userName": "", "serverAddress": "db2"},
            ]
        }
    }
    out = scrubber.scrub("/workbooks/{luid}/connections", payload)
    c1, c2 = out["connections"]["connection"]
    assert c1["userName"] == "[redacted]"  # presence preserved, identity gone
    assert c2["userName"] == ""  # empty stays empty (absence signal)
    assert c1["serverAddress"] == "db"
