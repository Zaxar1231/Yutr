from __future__ import annotations

from autodub.core.proc import CmdResult
from autodub.hardware.calibrate import apply_calibration, derive, gpu_tflops
from autodub.hardware.profiles import Tier, select_profile

from .conftest import make_report, nvidia


def _derive(sel, **kw):
    base = {"fingerprint": "fp", "cpu": 200.0, "gpu": {}, "asr_rtf": None, "duration_s": 25}
    return derive(sel, **(base | kw))


def test_typical_gpu_keeps_profile() -> None:
    sel = select_profile(make_report([nvidia(8192)]))
    cal = _derive(sel, gpu={"fp16_tflops": 30.0, "fp32_tflops": 15.0})
    assert cal.tier_steps_down == 0 and not cal.settings_patch
    assert cal.speed_factor > 1


def test_slow_gpu_is_downgraded() -> None:
    report = make_report([nvidia(16384)])
    sel = select_profile(report)
    cal = _derive(sel, gpu={"fp16_tflops": 8.0, "fp32_tflops": 6.0})
    assert cal.tier_steps_down == 1
    out = apply_calibration(sel, cal, lambda t: select_profile(report, forced_tier=t))
    assert out.tier == Tier.HIGH
    assert any("медленнее" in r for r in out.reasons)


def test_no_fast_fp16_patches_precision() -> None:
    report = make_report([nvidia(8192)])
    sel = select_profile(report)
    cal = _derive(sel, gpu={"fp16_tflops": 9.0, "fp32_tflops": 8.5})
    out = apply_calibration(sel, cal, lambda t: select_profile(report, forced_tier=t))
    assert out.settings.tts_precision == "fp32"
    assert out.settings.whisper_compute_type == "int8_float32"


def test_slow_cpu_drops_whisper_medium() -> None:
    report = make_report([], cores=8, ram_gb=32)
    sel = select_profile(report)
    assert sel.settings.whisper_model == "medium"
    cal = _derive(sel, asr_rtf=1.4)
    out = apply_calibration(sel, cal, lambda t: select_profile(report, forced_tier=t))
    assert out.settings.whisper_model == "small"


def test_speed_factor_is_clamped() -> None:
    sel = select_profile(make_report([]))
    assert _derive(sel, cpu=1e6).speed_factor == 4.0
    assert _derive(sel, cpu=0.001).speed_factor == 0.1


def test_gpu_bench_failure_is_reported_not_raised() -> None:
    assert "error" in gpu_tflops(None)
    out = gpu_tflops("python", runner=lambda a, timeout=0: CmdResult(1, "", "boom"))
    assert out["error"] == "boom"
    ok = gpu_tflops(
        "python",
        runner=lambda a, timeout=0: CmdResult(
            0, 'log\n{"fp16_tflops": 40.1, "fp32_tflops": 20.0}\n', ""
        ),
    )
    assert ok["fp16_tflops"] == 40.1
