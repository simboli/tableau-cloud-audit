"""Configuration loading: collector.toml + TCA_* environment variables.

collector.toml holds only non-secrets (site, pod, pat_name, database path).
Secrets are environment variables, read here and nowhere else:

* ``TCA_PAT_SECRET`` — the PAT secret (required to talk to Tableau)
* ``TCA_DB_KEY`` — optional passphrase for package-file encryption
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

PAT_SECRET_ENV = "TCA_PAT_SECRET"
DB_KEY_ENV = "TCA_DB_KEY"
DEFAULT_CONFIG_FILE = "collector.toml"


class ConfigError(RuntimeError):
    """A configuration problem the user can act on."""


class RetentionConfig(BaseModel):
    """Optional [retention] table in collector.toml.

    State is current-only, so it never accumulates; only the raw archive grows
    per run. `raw_days` caps that: at the end of each collect, raw pages of runs
    older than this are pruned (the current run is always kept). 0 = keep forever
    (the default — the file stays a full archive; opt in to pruning).
    """

    model_config = ConfigDict(extra="forbid")

    raw_days: int = 0

    @field_validator("raw_days")
    @classmethod
    def _non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("raw_days must be >= 0 (0 = keep forever)")
        return value


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")  # typos in collector.toml fail loudly

    site: str
    pod: str
    pat_name: str
    database: str
    retention: RetentionConfig = Field(default_factory=RetentionConfig)

    # set by load(); not part of the TOML
    _config_dir: Path = Path(".")

    @classmethod
    def load(cls, path: Path | str = DEFAULT_CONFIG_FILE) -> Config:
        path = Path(path)
        if not path.exists():
            raise ConfigError(
                f"'{path}' not found. Run `tca init` first, or point to a config "
                "file with --config."
            )
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"'{path}' is not valid TOML: {exc}") from exc
        try:
            config = cls(**data)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()
            )
            raise ConfigError(f"'{path}' is invalid: {problems}") from exc
        config._config_dir = path.parent.resolve()
        return config

    @property
    def database_path(self) -> Path:
        db = Path(self.database)
        return db if db.is_absolute() else self._config_dir / db


def pat_secret() -> str:
    value = os.environ.get(PAT_SECRET_ENV, "").strip()
    if not value:
        raise ConfigError(
            f"The {PAT_SECRET_ENV} environment variable is not set. "
            f"Export your PAT secret first:  export {PAT_SECRET_ENV}='...'"
        )
    return value


def db_key() -> str | None:
    value = os.environ.get(DB_KEY_ENV, "").strip()
    return value or None
