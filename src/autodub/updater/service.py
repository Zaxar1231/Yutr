"""App-side update flow: periodic check, background download, staging, switch on restart."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from autodub import __version__
from autodub.core.config import AppConfig
from autodub.updater.github import Release, UpdateChecker
from autodub.updater.install import Installation, UpdateError, download, parse_checksums
from autodub.updater.semver import Version, from_pep440

log = logging.getLogger(__name__)
_DETACHED = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0  # DETACHED_PROCESS|NEW_GROUP


def install_root() -> Path | None:
    """Set by the launcher when the app runs from an installed, versioned layout."""
    root = os.environ.get("AUTODUB_INSTALL_ROOT")
    return Path(root) if root else None


class UpdateService:
    def __init__(self, config: AppConfig, data_root: Path):
        self.config = config
        self.checker = UpdateChecker(
            config.updates.repository, data_root / "updates.json", user_agent=f"Autodub/{__version__}"
        )
        self.root = install_root()
        self.inst = Installation(self.root) if self.root else None
        self.available: Release | None = None
        self.staged: str | None = None

    @property
    def current(self) -> Version:
        return from_pep440(__version__)

    def can_install(self) -> bool:
        return self.inst is not None

    def mark_started(self) -> None:
        """Called once the UI is up: confirms the new version works, so no rollback happens."""
        if self.inst and self.inst.current:
            self.inst.mark_healthy(self.inst.current)
            self.inst.cleanup(keep=3)

    def check(self, *, force: bool = False) -> Release | None:
        u = self.config.updates
        if not force and not self.checker.due(u.check_interval_hours):
            return self.available
        bad = self.inst.bad() if self.inst else set()
        self.available = self.checker.check(self.current, channel=u.channel, bad=bad)
        return self.available

    def skip(self, release: Release) -> None:
        self.checker.skip(str(release.version))
        self.available = None

    def stage(self, release: Release, progress: Callable[[float], None]) -> str:
        """Download the update archive, verify SHA-256 from checksums.txt, unpack next to the app."""
        if not self.inst or not self.root:
            raise UpdateError("updates can be installed only in the installed version")
        asset = release.asset("-update.zip")
        sums = release.asset("checksums.txt")
        if not asset or not sums:
            raise UpdateError("release has no update archive or checksums")
        dl_dir = self.root / "downloads"
        dl_dir.mkdir(exist_ok=True)
        sums_file = download(sums[1], dl_dir / f"checksums-{release.version}.txt")
        expected = parse_checksums(sums_file.read_text("utf-8")).get(asset[0])
        if not expected:
            raise UpdateError("update archive is missing from checksums.txt")
        archive = download(asset[1], dl_dir / asset[0], progress=progress)
        self.inst.stage(archive, str(release.version), expected)
        archive.unlink(missing_ok=True)
        self.staged = str(release.version)
        return self.staged

    def apply_and_restart(self) -> None:
        """Switch the pointer and hand over to the launcher, which watches the new start."""
        if not (self.inst and self.root and self.staged):
            return
        self.inst.switch(self.staged)
        launcher = self.root / "launcher.pyw"
        pyw = Path(sys.executable).with_name("pythonw.exe")
        python = str(pyw if pyw.exists() else sys.executable)
        subprocess.Popen([python, str(launcher), "--after-update"], creationflags=_DETACHED, close_fds=True)

    def apply_on_next_start(self) -> None:
        if self.inst and self.staged:
            self.inst.switch(self.staged)
