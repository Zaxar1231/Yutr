"""CPU model, cores and SIMD support."""

from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path

import psutil

from autodub.hardware.models import CpuInfo

log = logging.getLogger(__name__)

# winnt.h processor feature constants.
_PF_AVX = 39
_PF_AVX2 = 40
_PF_AVX512F = 41


def parse_proc_cpuinfo(text: str) -> tuple[str | None, set[str]]:
    name: str | None = None
    flags: set[str] = set()
    for line in text.splitlines():
        key, _, value = line.partition(":")
        key = key.strip()
        if key == "model name" and name is None:
            name = value.strip()
        elif key == "flags" and not flags:
            flags = set(value.split())
    return name, flags


def _windows() -> tuple[str | None, bool | None, bool | None]:
    if sys.platform != "win32":
        return None, None, None
    import ctypes
    import winreg

    name = None
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as k:
            name = str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except OSError:
        pass
    present = ctypes.windll.kernel32.IsProcessorFeaturePresent
    if not present(_PF_AVX):
        # Old Windows builds do not know the AVX constants at all: unknown, not "no".
        return name, None, None
    return name, bool(present(_PF_AVX2)), bool(present(_PF_AVX512F))


def detect_cpu(proc_cpuinfo: Path = Path("/proc/cpuinfo")) -> CpuInfo:
    name: str | None = None
    avx2: bool | None = None
    avx512: bool | None = None
    try:
        if sys.platform == "win32":
            name, avx2, avx512 = _windows()
        elif proc_cpuinfo.exists():
            name, flags = parse_proc_cpuinfo(proc_cpuinfo.read_text(errors="replace"))
            if flags:
                avx2 = "avx2" in flags
                avx512 = "avx512f" in flags
    except Exception as exc:
        log.warning("CPU details unavailable: %s", exc)
    threads = psutil.cpu_count(logical=True) or 1
    cores = psutil.cpu_count(logical=False) or threads
    return CpuInfo(
        name=name or platform.processor() or "Неизвестный процессор",
        physical_cores=cores,
        logical_threads=threads,
        avx2=avx2,
        avx512=avx512,
    )
