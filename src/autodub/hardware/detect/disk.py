"""Free space and drive type (SSD/HDD) for the models and output folders."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import psutil

from autodub.core import proc
from autodub.hardware.models import DiskInfo

log = logging.getLogger(__name__)
_MB = 1024 * 1024


def existing_parent(path: Path) -> Path:
    p = path.resolve()
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def _kind_windows(path: Path, runner: proc.Runner) -> str:
    drive = os.path.splitdrive(str(path))[0].rstrip(":")
    if len(drive) != 1:
        return "unknown"  # UNC/network path
    res = proc.powershell(
        f"(Get-Partition -DriveLetter {drive} | Get-Disk | Get-PhysicalDisk).MediaType",
        runner=runner,
    )
    if res is None or not res.ok:
        return "unknown"
    out = res.stdout.strip().lower()
    if "ssd" in out:
        return "ssd"
    if "hdd" in out:
        return "hdd"
    return "unknown"


def _block_device_for(path: Path) -> str | None:
    best = None
    for part in psutil.disk_partitions(all=False):
        if str(path).startswith(part.mountpoint) and (
            best is None or len(part.mountpoint) > len(best.mountpoint)
        ):
            best = part
    if best is None or not best.device.startswith("/dev/"):
        return None
    return best.device.removeprefix("/dev/")


def kind_linux(device: str | None, sys_block: Path = Path("/sys/block")) -> str:
    if not device:
        return "unknown"
    # /dev/nvme0n1p2 -> nvme0n1, /dev/sda1 -> sda
    for cand in sorted(sys_block.iterdir(), key=lambda p: -len(p.name)) if sys_block.exists() else []:
        if device.startswith(cand.name):
            try:
                rot = (cand / "queue" / "rotational").read_text().strip()
            except OSError:
                return "unknown"
            return "hdd" if rot == "1" else "ssd"
    return "unknown"


def detect_disk(role: str, path: Path, runner: proc.Runner = proc.run) -> DiskInfo | None:
    target = existing_parent(path)
    try:
        usage = psutil.disk_usage(str(target))
    except OSError as exc:
        log.warning("disk usage for %s failed: %s", target, exc)
        return None
    if sys.platform == "win32":
        kind = _kind_windows(target, runner)
    elif sys.platform.startswith("linux"):
        kind = kind_linux(_block_device_for(target))
    else:
        kind = "unknown"
    return DiskInfo(
        role=role,
        path=str(path),
        free_mb=usage.free // _MB,
        total_mb=usage.total // _MB,
        kind=kind,
    )
