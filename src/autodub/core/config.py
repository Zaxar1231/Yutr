"""User configuration in TOML with versioned migrations.

Only user choices live here. Machine state (last hardware scan, calibration) is stored
separately in ``hardware.json`` so that the config stays short and hand-editable.
Secrets are never stored here: they go to the OS credential store via keyring.
"""

from __future__ import annotations

import logging
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1


class HardwareConfig(BaseModel):
    forced_tier: str | None = None  # None = automatic
    eco_mode: bool = False
    overrides: dict[str, Any] = Field(default_factory=dict)
    network_test: bool = True
    speedtest_urls: list[str] = Field(default_factory=list)  # empty = built-in list


class DefaultsConfig(BaseModel):
    """Job settings chosen in «Настройки»; the main screen uses them for new jobs."""

    quality: str = "best"
    mode: str = "dub"
    original_volume: float = 0.2
    translator: str = "claude"
    claude_model: str = "claude-opus-5-5"
    tts_engine: str | None = None  # None = from the performance profile
    diarize: bool = False
    cookies_from_browser: str = ""


class UpdatesConfig(BaseModel):
    repository: str = "Zaxar1231/Yutr"
    channel: str = "stable"
    check_interval_hours: int = 12
    auto_update: bool = False


class AppConfig(BaseModel):
    schema_version: int = SCHEMA_VERSION
    output_dir: str | None = None
    theme: str = "system"
    first_run_done: bool = False
    licenses_accepted: list[str] = Field(default_factory=list)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    hardware: HardwareConfig = Field(default_factory=HardwareConfig)
    updates: UpdatesConfig = Field(default_factory=UpdatesConfig)


Migration = Callable[[dict[str, Any]], dict[str, Any]]
# MIGRATIONS[n] upgrades a raw config dict from schema n to n + 1.
MIGRATIONS: dict[int, Migration] = {}


def migrate(raw: dict[str, Any]) -> dict[str, Any]:
    version = int(raw.get("schema_version", 1))
    while version < SCHEMA_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise ValueError(f"no migration from config schema {version}")
        raw = step(raw)
        version += 1
        raw["schema_version"] = version
    return raw


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        return AppConfig()
    try:
        raw = tomllib.loads(path.read_text("utf-8"))
        return AppConfig.model_validate(migrate(raw))
    except (tomllib.TOMLDecodeError, ValueError) as exc:
        backup = path.with_suffix(".broken.toml")
        path.replace(backup)
        log.error("config %s is invalid (%s); saved as %s, using defaults", path, exc, backup)
        return AppConfig()


def save_config(cfg: AppConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(tomli_w.dumps(cfg.model_dump(mode="json", exclude_none=True)), "utf-8")
    tmp.replace(path)  # atomic on the same volume
