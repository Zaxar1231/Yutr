"""Battery / mains power."""

from __future__ import annotations

import psutil

from autodub.hardware.models import PowerInfo


def detect_power() -> PowerInfo:
    try:
        bat = psutil.sensors_battery()
    except (NotImplementedError, OSError, RuntimeError):
        bat = None
    if bat is None:
        return PowerInfo(has_battery=False, on_battery=False)
    return PowerInfo(
        has_battery=True,
        on_battery=bat.power_plugged is False,
        percent=float(bat.percent) if bat.percent is not None else None,
    )
