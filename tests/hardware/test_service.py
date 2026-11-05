from __future__ import annotations

from pathlib import Path

import pytest

from autodub.core.config import AppConfig, load_config
from autodub.core.paths import AppPaths
from autodub.hardware.calibrate import Calibration
from autodub.hardware.profiles import Tier
from autodub.hardware.service import HardwareService, bug_report_summary

from .conftest import make_report, nvidia


def make_service(tmp_path: Path, report, fingerprint: str | None = None) -> HardwareService:
    paths = AppPaths(root=tmp_path, portable=True).ensure()
    return HardwareService(
        paths=paths,
        config=AppConfig(hardware={"network_test": False}),
        scan_fn=lambda **_: report.model_copy(deep=True),
        fingerprint_fn=lambda: fingerprint or report.fingerprint(),
    )


def test_first_run_then_no_rescan(tmp_path: Path) -> None:
    report = make_report([nvidia(8192)])
    svc = make_service(tmp_path, report)
    assert svc.rescan_reason() == "first_run"
    svc.scan()
    assert svc.rescan_reason() is None
    # state survives a restart
    svc2 = make_service(tmp_path, report)
    assert svc2.state.report is not None and svc2.rescan_reason() is None


def test_driver_change_triggers_rescan_and_invalidates_calibration(tmp_path: Path) -> None:
    report = make_report([nvidia(8192)])
    svc = make_service(tmp_path, report)
    svc.scan()
    svc.set_calibration(
        Calibration(
            fingerprint=report.fingerprint(), measured_at="x", duration_s=1, speed_factor=2.0
        )
    )
    assert not svc.calibration_needed()
    new = make_report([nvidia(8192, driver="595.00")])
    svc2 = make_service(tmp_path, new, fingerprint=new.fingerprint())
    assert svc2.rescan_reason() == "hardware_changed"
    svc2.scan()
    assert svc2.state.calibration is None and svc2.calibration_needed()


def test_overrides_and_reset_to_auto(tmp_path: Path) -> None:
    svc = make_service(tmp_path, make_report([nvidia(12282)]))
    svc.scan()
    auto = svc.selection()
    svc.set_override("whisper_batch_size", 4)
    svc.set_forced_tier(Tier.MEDIUM)
    sel = svc.selection()
    assert sel.tier == Tier.MEDIUM and sel.settings.whisper_batch_size == 4
    assert "whisper_batch_size" in sel.overridden
    # persisted to config.toml
    cfg = load_config(svc.paths.config_file)
    assert cfg.hardware.overrides == {"whisper_batch_size": 4}
    svc.reset_to_auto()
    assert svc.selection() == auto
    with pytest.raises(KeyError):
        svc.set_override("nope", 1)
    with pytest.raises(ValueError):
        svc.set_override("whisper_batch_size", 0)


def test_calibration_applies_to_selection_and_estimate(tmp_path: Path) -> None:
    report = make_report([nvidia(8192)])
    svc = make_service(tmp_path, report)
    svc.scan()
    slow = svc.estimate_10min()
    svc.set_calibration(
        Calibration(
            fingerprint=report.fingerprint(),
            measured_at="x",
            duration_s=1,
            speed_factor=2.0,
            settings_patch={"tts_precision": "fp32"},
        )
    )
    assert svc.estimate_10min()[1] < slow[1]
    assert svc.selection().settings.tts_precision == "fp32"


def test_bug_report_has_no_personal_paths(tmp_path: Path) -> None:
    svc = make_service(tmp_path, make_report([nvidia(8192)]))
    report = svc.scan()
    summary = bug_report_summary(report, svc.selection(), "0.1.0")
    text = str(summary)
    assert str(tmp_path) not in text and "RTX" in text
