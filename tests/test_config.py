"""Config loading: collector.toml + the optional [retention]/[collect] tables."""

from pathlib import Path

import pytest

from tca.config import Config, ConfigError

BASE = 'site = "acme"\npod = "10ax"\npat_name = "tok"\ndatabase = "acme.duckdb"\n'


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "collector.toml"
    path.write_text(body)
    return path


def test_retention_defaults_to_keep_forever(tmp_path: Path) -> None:
    cfg = Config.load(_write(tmp_path, BASE))
    assert cfg.retention.raw_days == 0


def test_retention_explicit_value(tmp_path: Path) -> None:
    cfg = Config.load(_write(tmp_path, BASE + "\n[retention]\nraw_days = 30\n"))
    assert cfg.retention.raw_days == 30


def test_retention_negative_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config.load(_write(tmp_path, BASE + "\n[retention]\nraw_days = -1\n"))


def test_unknown_retention_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config.load(_write(tmp_path, BASE + "\n[retention]\nkeep_forever = true\n"))


def test_collect_modules_defaults_to_none(tmp_path: Path) -> None:
    cfg = Config.load(_write(tmp_path, BASE))
    assert cfg.collect.modules is None  # absent → CLI falls back to DEFAULT_MODULES


def test_collect_modules_explicit_list(tmp_path: Path) -> None:
    cfg = Config.load(_write(tmp_path, BASE + '\n[collect]\nmodules = ["rest_core", "content"]\n'))
    assert cfg.collect.modules == ["rest_core", "content"]


def test_collect_empty_modules_list_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config.load(_write(tmp_path, BASE + "\n[collect]\nmodules = []\n"))


def test_unknown_collect_key_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        Config.load(_write(tmp_path, BASE + '\n[collect]\nextra = "x"\n'))
