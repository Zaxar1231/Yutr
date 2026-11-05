"""Filesystem locations.

Installed mode keeps data in the per-user application data directory
(``%LOCALAPPDATA%\\Autodub`` on Windows). Portable mode is enabled by a ``portable.flag``
file next to the application and keeps everything inside the application folder.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir, user_videos_dir

APP_NAME = "Autodub"
PORTABLE_FLAG = "portable.flag"


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    env = os.environ.get("AUTODUB_APP_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class AppPaths:
    root: Path
    portable: bool

    @property
    def config_file(self) -> Path:
        return self.root / "config.toml"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def bin(self) -> Path:
        return self.root / "bin"

    @property
    def envs(self) -> Path:
        return self.root / "envs"

    def default_output(self) -> Path:
        if self.portable:
            return self.root / "output"
        return Path(user_videos_dir()) / "Автодубляж"

    def ensure(self) -> AppPaths:
        for p in (self.root, self.logs, self.models, self.cache, self.bin):
            p.mkdir(parents=True, exist_ok=True)
        return self


def resolve_paths(override: Path | None = None) -> AppPaths:
    if override is not None:
        return AppPaths(root=override, portable=True)
    env = os.environ.get("AUTODUB_DATA_DIR")
    if env:
        return AppPaths(root=Path(env), portable=False)
    app_root = _app_root()
    if (app_root / PORTABLE_FLAG).exists():
        return AppPaths(root=app_root / "data", portable=True)
    return AppPaths(root=Path(user_data_dir(APP_NAME, appauthor=False)), portable=False)
