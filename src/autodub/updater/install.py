"""Versioned installation layout, download/verification and the switch with rollback.

    <root>/
      versions/0.1.0/        one directory per installed app version (never modified)
      versions/0.1.1/
      current                text file: the version the launcher starts
      previous               text file: the version to roll back to
      bad.json               versions that failed to start (never offered again)
      envs/, models/, data/  heavy layers and user data: untouched by app updates

Running files are never overwritten, so the Windows "file in use" problem cannot happen:
a new version is unpacked next to the old one, the pointer is switched, and the old
directory stays as the backup until the new version reports a healthy start.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
HEALTH_MARKER = ".started_ok"


class UpdateError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_checksums(text: str) -> dict[str, str]:
    """``sha256sum`` format: '<hex>  <name>' (also accepts '*name')."""
    out = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            out[parts[-1].lstrip("*")] = parts[0].lower()
    return out


def download(
    url: str,
    dst: Path,
    *,
    progress: Callable[[float], None] | None = None,
    retries: int = 4,
    opener: Callable[..., Any] | None = None,
) -> Path:
    """Resumable download (HTTP Range) with retries and backoff."""
    part = dst.with_suffix(dst.suffix + ".part")
    open_url = opener or urllib.request.urlopen
    for attempt in range(retries + 1):
        have = part.stat().st_size if part.exists() else 0
        req = urllib.request.Request(
            url, headers={"User-Agent": "Autodub-Updater", **({"Range": f"bytes={have}-"} if have else {})}
        )
        try:
            with open_url(req, timeout=30) as resp:
                status = getattr(resp, "status", 200)
                total = int(resp.headers.get("Content-Length") or 0) + (have if status == 206 else 0)
                mode = "ab" if status == 206 else "wb"
                done = have if status == 206 else 0
                with part.open(mode) as f:
                    while chunk := resp.read(1 << 16):
                        f.write(chunk)
                        done += len(chunk)
                        if progress and total:
                            progress(done / total)
            part.replace(dst)
            return dst
        except OSError as exc:
            if attempt == retries:
                raise UpdateError(f"download failed: {exc}") from exc
            time.sleep(min(2 ** (attempt + 1), 16))
    raise UpdateError("unreachable")


class Installation:
    def __init__(self, root: Path):
        self.root = root
        self.versions = root / "versions"

    def _read(self, name: str) -> str | None:
        p = self.root / name
        return p.read_text("utf-8").strip() if p.exists() else None

    def _write(self, name: str, value: str) -> None:
        tmp = self.root / f"{name}.tmp"
        tmp.write_text(value, "utf-8")
        tmp.replace(self.root / name)

    @property
    def current(self) -> str | None:
        return self._read("current")

    @property
    def previous(self) -> str | None:
        return self._read("previous")

    def version_dir(self, version: str) -> Path:
        return self.versions / version

    def bad(self) -> set[str]:
        p = self.root / "bad.json"
        return set(json.loads(p.read_text("utf-8"))) if p.exists() else set()

    def mark_bad(self, version: str) -> None:
        bad = self.bad() | {version}
        (self.root / "bad.json").write_text(json.dumps(sorted(bad)), "utf-8")

    def stage(self, archive: Path, version: str, expected_sha256: str) -> Path:
        """Verify and unpack a release archive into versions/<version>."""
        actual = sha256(archive)
        if actual != expected_sha256.lower():
            archive.unlink(missing_ok=True)
            raise UpdateError(f"checksum mismatch for {archive.name}")
        target = self.version_dir(version)
        staging = target.with_name(version + ".staging")
        shutil.rmtree(staging, ignore_errors=True)
        with zipfile.ZipFile(archive) as z:
            for member in z.namelist():  # zip-slip guard
                if (staging / member).resolve().is_relative_to(staging.resolve()) is False:
                    raise UpdateError(f"unsafe path in archive: {member}")
            z.extractall(staging)
        shutil.rmtree(target, ignore_errors=True)
        staging.replace(target)
        return target

    def switch(self, version: str) -> None:
        if not self.version_dir(version).exists():
            raise UpdateError(f"version {version} is not installed")
        cur = self.current
        if cur and cur != version:
            self._write("previous", cur)
        (self.version_dir(version) / HEALTH_MARKER).unlink(missing_ok=True)
        self._write("current", version)

    def mark_healthy(self, version: str) -> None:
        (self.version_dir(version) / HEALTH_MARKER).write_text(str(time.time()), "utf-8")

    def healthy(self, version: str) -> bool:
        return (self.version_dir(version) / HEALTH_MARKER).exists()

    def rollback(self) -> str | None:
        """Return to the previous version and remember the current one as bad."""
        cur, prev = self.current, self.previous
        if not prev or not self.version_dir(prev).exists():
            return None
        if cur:
            self.mark_bad(cur)
        self._write("current", prev)
        (self.root / "previous").unlink(missing_ok=True)
        log.warning("rolled back from %s to %s", cur, prev)
        return prev

    def cleanup(self, keep: int = 2) -> None:
        """Remove old versions, always keeping current and previous."""
        protected = {self.current, self.previous}
        dirs = sorted(
            (d for d in self.versions.iterdir() if d.is_dir() and not d.name.endswith(".staging")),
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        for d in dirs[keep:]:
            if d.name not in protected:
                shutil.rmtree(d, ignore_errors=True)
