"""CLI tests: init wizard, verify, collect, summary, resolve — against fake API."""

from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock
from typer.testing import CliRunner

from tca.cli import app
from tca.config import PAT_SECRET_ENV

BASE = "https://test-pod.online.tableau.com"
SITE_LUID = "site-luid-1"
API = f"{BASE}/api/3.26/sites/{SITE_LUID}"

runner = CliRunner()


def plain(output: str) -> str:
    """Collapse whitespace: rich wraps lines at terminal width, and the wrap
    point depends on tmp-path lengths — different on CI vs locally. Assert
    against the normalized text, never the raw wrapped output."""
    return " ".join(output.split())


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(PAT_SECRET_ENV, "the-secret")
    monkeypatch.delenv("TCA_DB_KEY", raising=False)
    return tmp_path


def write_config(tmp_path: Path) -> None:
    (tmp_path / "collector.toml").write_text(
        'site = "acme"\npod = "test-pod"\npat_name = "tca-collector"\ndatabase = "acme.duckdb"\n'
    )


def mock_signin(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/3.4/serverinfo", json={"serverInfo": {"restApiVersion": "3.26"}}
    )
    httpx_mock.add_response(
        url=f"{BASE}/api/3.26/auth/signin",
        json={"credentials": {"token": "tok", "site": {"id": SITE_LUID, "contentUrl": "acme"}}},
    )
    httpx_mock.add_response(url=f"{BASE}/api/3.26/auth/signout", json={})


def mock_collect_endpoints(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{API}/users?fields=_all_&pageSize=1000&pageNumber=1",
        json={
            "pagination": {"pageNumber": "1", "pageSize": "1000", "totalAvailable": "1"},
            "users": {
                "user": [
                    {"id": "aaaa-1111", "name": "mrossi", "email": "m.rossi@acme.it"},
                ]
            },
        },
    )
    httpx_mock.add_response(
        url=f"{API}/groups?pageSize=1000&pageNumber=1",
        json={
            "pagination": {"pageNumber": "1", "pageSize": "1000", "totalAvailable": "1"},
            "groups": {"group": [{"id": "g-1", "name": "Finance"}]},
        },
    )
    httpx_mock.add_response(
        url=f"{API}/groups/g-1/users?pageSize=1000&pageNumber=1",
        json={
            "pagination": {"pageNumber": "1", "pageSize": "1000", "totalAvailable": "1"},
            "users": {"user": [{"id": "aaaa-1111"}]},
        },
    )


def test_init_writes_config_and_creates_file(workdir: Path) -> None:
    result = runner.invoke(app, ["init"], input="acme\ntest-pod\n\n\n")
    assert result.exit_code == 0, result.output
    assert (workdir / "collector.toml").exists()
    assert (workdir / "acme.duckdb").exists()
    assert "NOT be encrypted" in plain(result.output)  # no TCA_DB_KEY set
    content = (workdir / "collector.toml").read_text()
    assert 'site = "acme"' in content
    assert "TCA_PAT_SECRET" in content  # the "secrets don't go here" comment


def test_init_refuses_overwrite(workdir: Path) -> None:
    write_config(workdir)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "--force" in result.output


def test_verify_happy_path(workdir: Path, httpx_mock: HTTPXMock) -> None:
    write_config(workdir)
    mock_signin(httpx_mock)
    # no Admin Insights on this fake site -> verify warns but still passes
    httpx_mock.add_response(
        url=f"{API}/datasources?pageSize=1000&pageNumber=1",
        json={"datasources": {"datasource": []}},
    )
    httpx_mock.add_response(
        url=f"{BASE}/api/metadata/graphql",
        json={
            "data": {
                "workbooksConnection": {"totalCount": 24},
                "publishedDatasourcesConnection": {"totalCount": 10},
            }
        },
    )
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 0, result.output
    assert "All checks passed" in plain(result.output)
    assert "Admin Insights datasources not found" in plain(result.output)
    assert "Metadata API reachable" in plain(result.output)
    assert SITE_LUID in result.output


def test_verify_fails_without_secret(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(workdir)
    monkeypatch.delenv(PAT_SECRET_ENV)
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 1
    assert "TCA_PAT_SECRET" in plain(result.output)


def test_collect_end_to_end_then_summary_and_resolve(workdir: Path, httpx_mock: HTTPXMock) -> None:
    write_config(workdir)
    mock_signin(httpx_mock)
    mock_collect_endpoints(httpx_mock)

    # explicit module list: the content module's endpoints are not mocked here
    result = runner.invoke(app, ["collect", "--modules", "rest_core"])
    assert result.exit_code == 0, result.output
    assert "run #1" in plain(result.output)
    assert "No real identities" in plain(result.output)
    # listing pages still print one line each; per-item loops (group membership
    # here) report through the progress bar instead of a line per page
    assert "/users — page 1" in plain(result.output)
    assert "/groups/{luid}/users — page" not in plain(result.output)

    result = runner.invoke(app, ["summary"])
    assert result.exit_code == 0, result.output
    assert "acme" in result.output
    assert "collection runs" in plain(result.output)

    result = runner.invoke(app, ["runs"])
    assert result.exit_code == 0, result.output
    assert "collection runs" in plain(result.output)
    assert "ok" in plain(result.output)

    # machine-readable last outcome for scheduling: only the status, nothing else
    result = runner.invoke(app, ["runs", "--last", "-q"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "ok"

    result = runner.invoke(app, ["resolve", "U-0001"])
    assert result.exit_code == 0, result.output
    assert "m.rossi@acme.it" in result.output

    result = runner.invoke(app, ["resolve", "U-9999"])
    assert result.exit_code == 1
    assert "not in the identity vault" in plain(result.output)


def test_collect_unknown_module(workdir: Path) -> None:
    write_config(workdir)
    result = runner.invoke(app, ["collect", "--modules", "nope"])
    assert result.exit_code == 1
    assert "Unknown module" in plain(result.output)


def test_summary_without_file(workdir: Path) -> None:
    write_config(workdir)
    result = runner.invoke(app, ["summary"])
    assert result.exit_code == 1
    assert "tca collect" in plain(result.output)


def test_runs_without_file(workdir: Path) -> None:
    write_config(workdir)
    result = runner.invoke(app, ["runs"])
    assert result.exit_code == 1
    assert "tca collect" in plain(result.output)


def test_export_after_collect(workdir: Path, httpx_mock: HTTPXMock) -> None:
    write_config(workdir)
    mock_signin(httpx_mock)
    mock_collect_endpoints(httpx_mock)
    runner.invoke(app, ["collect", "--modules", "rest_core"])

    result = runner.invoke(app, ["export"])
    assert result.exit_code == 0, result.output
    assert (workdir / "acme-export.duckdb").exists()
    assert (workdir / "acme-export.duckdb.sha256").exists()
    assert "identity vault NOT exported" in plain(result.output)

    # second run refuses, --force overwrites
    result = runner.invoke(app, ["export"])
    assert result.exit_code == 1
    assert "--force" in result.output
    result = runner.invoke(app, ["export", "--force"])
    assert result.exit_code == 0, result.output
