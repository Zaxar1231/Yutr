"""RAM."""

from __future__ import annotations

import psutil

from autodub.hardware.models import MemoryInfo

_MB = 1024 * 1024


def detect_memory() -> MemoryInfo:
    vm = psutil.virtual_memory()
    return MemoryInfo(total_mb=vm.total // _MB, available_mb=vm.available // _MB)
