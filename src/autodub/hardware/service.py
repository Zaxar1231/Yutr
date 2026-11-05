"""Glue between detection, profiles, calibration, the config and the UI.

Rescan policy:
* first start (no saved scan);
* hardware or driver fingerprint changed since the last scan (checked at every start with a
  quick scan that skips slow probes);
* the user pressed «Пересканировать».
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autodub.core.config import AppConfig, save_config
from autodub.core.paths import AppPaths
from autodub.hardware import detect
from autodub.hardware.calibrate import Calibration, apply_calibration
from autodub.hardware.detect.network import DEFAULT_ENDPOINTS, measure
from autodub.hardware.models import HardwareReport
from autodub.hardware.profiles import (
    OVERRIDABLE,
    ProfileSelection,
    Tier,
    apply_overrides,
    estimate_minutes,
    select_profile,
)

log = logging.getLogger(__name__)


class HardwareState(BaseModel):
    report: HardwareReport | None = None
    calibration: Calibration | None = None
    learned_peaks: dict[str, dict[str, int]] = Field(default_factory=dict)


def load_state(path: Path) -> HardwareState:
    if not path.exists():
        return HardwareState()
    try:
        return HardwareState.model_validate_json(path.read_text("utf-8"))
    except ValueError as exc:
        log.warning("hardware state unreadable (%s), starting fresh", exc)
        return HardwareState()


def save_state(state: HardwareState, path: Path) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state.model_dump(mode="json"), ensure_ascii=False, indent=1), "utf-8")
    tmp.replace(path)


@dataclass
class HardwareService:
    paths: AppPaths
    config: AppConfig
    scan_fn: Callable[..., HardwareReport] = detect.scan
    fingerprint_fn: Callable[[], str] = detect.quick_fingerprint
    state: HardwareState = field(init=False)

    def __post_init__(self) -> None:
        self.state = load_state(self.state_file)

    @property
    def state_file(self) -> Path:
        return self.paths.root / "hardware.json"

    def output_dir(self) -> Path:
        return Path(self.config.output_dir) if self.config.output_dir else self.paths.default_output()

    # --- scanning -------------------------------------------------------------------------

    def rescan_reason(self) -> str | None:
        """Why a full scan is needed now, or None."""
        report = self.state.report
        if report is None:
            return "first_run"
        try:
            current = self.fingerprint_fn()
        except Exception:
            log.exception("quick fingerprint failed")
            return None
        if current != report.fingerprint():
            return "hardware_changed"
        return None

    def calibration_needed(self) -> bool:
        cal, report = self.state.calibration, self.state.report
        return report is not None and (cal is None or cal.fingerprint != report.fingerprint())

    def scan(self, *, ffmpeg: str = "ffmpeg") -> HardwareReport:
        hw = self.config.hardware
        report = self.scan_fn(
            models_dir=self.paths.models,
            output_dir=self.output_dir(),
            ffmpeg=ffmpeg,
            measure_network=False,
        )
        if hw.network_test:
            report.network = measure(tuple(hw.speedtest_urls) or DEFAULT_ENDPOINTS)
        old = self.state.report
        if old is not None and old.fingerprint() != report.fingerprint():
            # New hardware: learned peaks and calibration no longer apply.
            self.state.learned_peaks.clear()
            self.state.calibration = None
        self.state.report = report
        save_state(self.state, self.state_file)
        return report

    def set_calibration(self, cal: Calibration) -> None:
        self.state.calibration = cal
        save_state(self.state, self.state_file)

    def record_peaks(self, peaks: dict[str, dict[str, int]]) -> None:
        self.state.learned_peaks.update(peaks)
        save_state(self.state, self.state_file)

    # --- profile --------------------------------------------------------------------------

    def _auto(self, report: HardwareReport, *, forced: Tier | None) -> ProfileSelection:
        hw = self.config.hardware

        def base(tier: Tier | None) -> ProfileSelection:
            return select_profile(report, eco=hw.eco_mode, forced_tier=tier)

        sel = base(forced)
        cal = self.state.calibration
        if forced is None and cal is not None and cal.fingerprint == report.fingerprint():
            sel = apply_calibration(sel, cal, base)
        return sel

    def automatic_selection(self) -> ProfileSelection:
        """Profile without the user's manual overrides (what «Вернуть авто» restores)."""
        report = self._require_report()
        return self._auto(report, forced=None)

    def selection(self) -> ProfileSelection:
        report = self._require_report()
        hw = self.config.hardware
        forced = Tier(hw.forced_tier) if hw.forced_tier else None
        sel = self._auto(report, forced=forced)
        overrides = {k: v for k, v in hw.overrides.items() if k in OVERRIDABLE}
        if overrides:
            try:
                sel = apply_overrides(sel, overrides)
            except ValueError as exc:
                log.warning("ignoring invalid overrides %s: %s", overrides, exc)
        return sel

    def reselect(self, tier: Tier) -> ProfileSelection:
        """Selection for a lower tier, used by OOM fallback (keeps user overrides out)."""
        return select_profile(self._require_report(), eco=self.config.hardware.eco_mode, forced_tier=tier)

    def estimate_10min(self) -> tuple[int, int]:
        cal = self.state.calibration
        report = self._require_report()
        speed = cal.speed_factor if cal and cal.fingerprint == report.fingerprint() else 1.0
        return estimate_minutes(self.selection(), video_minutes=10, speed_factor=speed)

    # --- user actions ---------------------------------------------------------------------

    def set_override(self, key: str, value: Any) -> None:
        if key not in OVERRIDABLE:
            raise KeyError(key)
        candidate = self.config.hardware.overrides | {key: value}
        apply_overrides(self.automatic_selection(), candidate)  # raises ValueError if invalid
        self.config.hardware.overrides = candidate
        self._save_config()

    def set_forced_tier(self, tier: Tier | None) -> None:
        self.config.hardware.forced_tier = tier.value if tier else None
        self._save_config()

    def set_eco(self, enabled: bool) -> None:
        self.config.hardware.eco_mode = enabled
        self._save_config()

    def reset_to_auto(self) -> None:
        self.config.hardware.overrides.clear()
        self.config.hardware.forced_tier = None
        self._save_config()

    def _save_config(self) -> None:
        save_config(self.config, self.paths.config_file)

    def _require_report(self) -> HardwareReport:
        if self.state.report is None:
            raise RuntimeError("hardware has not been scanned yet")
        return self.state.report


def bug_report_summary(report: HardwareReport, sel: ProfileSelection, version: str) -> dict[str, Any]:
    """Hardware info for «Сообщить об ошибке». No paths, user names or serial numbers."""
    gpu = report.primary_gpu()
    return {
        "version": version,
        "os": f"{report.os} {report.os_version}",
        "profile": sel.tier.value,
        "accelerator": sel.accelerator.value,
        "gpu": (f"{gpu.name} {gpu.vram_total_mb} MB, driver {gpu.driver_version}, CUDA {gpu.cuda_version}")
        if gpu
        else "none",
        "cpu": f"{report.cpu.name} ({report.cpu.physical_cores}C/{report.cpu.logical_threads}T, "
        f"AVX2={report.cpu.avx2}, AVX-512={report.cpu.avx512})",
        "ram_gb": round(report.memory.total_mb / 1024),
        "encoders": report.encoders.available(),
        "overridden": sel.overridden,
    }
