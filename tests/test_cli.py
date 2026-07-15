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
    assert "NOT be encrypted" in result.output  # no TCA_DB_KEY set
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
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 0, result.output
    assert "All checks passed" in result.output
    assert SITE_LUID in result.output


def test_verify_fails_without_secret(workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_config(workdir)
    monkeypatch.delenv(PAT_SECRET_ENV)
    result = runner.invoke(app, ["verify"])
    assert result.exit_code == 1
    assert "TCA_PAT_SECRET" in result.output


def test_collect_end_to_end_then_summary_and_resolve(workdir: Path, httpx_mock: HTTPXMock) -> None:
    write_config(workdir)
    mock_signin(httpx_mock)
    mock_collect_endpoints(httpx_mock)

    result = runner.invoke(app, ["collect"])
    assert result.exit_code == 0, result.output
    assert "run #1" in result.output
    assert "No real identities" in result.output

    result = runner.invoke(app, ["summary"])
    assert result.exit_code == 0, result.output
    assert "acme" in result.output
    assert "collection runs" in result.output

    result = runner.invoke(app, ["resolve", "U-0001"])
    assert result.exit_code == 0, result.output
    assert "m.rossi@acme.it" in result.output

    result = runner.invoke(app, ["resolve", "U-9999"])
    assert result.exit_code == 1
    assert "not in the identity vault" in result.output


def test_collect_unknown_module(workdir: Path) -> None:
    write_config(workdir)
    result = runner.invoke(app, ["collect", "--modules", "nope"])
    assert result.exit_code == 1
    assert "Unknown module" in result.output


def test_summary_without_file(workdir: Path) -> None:
    write_config(workdir)
    result = runner.invoke(app, ["summary"])
    assert result.exit_code == 1
    assert "tca collect" in result.output
