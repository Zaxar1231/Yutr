"""First-run calibration (~30 s) that refines the profile picked from specs alone.

Specs do not tell everything: a laptop GPU with a low power limit, an old card without
tensor cores, or a throttling CPU can be several times slower than "typical" for its VRAM.
The benchmark measures real throughput and adjusts the profile:

* GPU throughput (fp16/fp32 matmul) runs inside the torch engine environment, in a separate
  process, so a driver problem cannot take the app down.
* CPU throughput (fp32 matmul via numpy).
* Optionally a real Whisper pass over the bundled test speech clip (provided by the pipeline).

The result is stored in the config together with the hardware fingerprint and reused until
the hardware or drivers change.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autodub.core import proc
from autodub.hardware.profiles import ProfileSelection, ProfileSettings, Tier, load_profile_data

log = logging.getLogger(__name__)

# Throughput typical for the bottom of each tier. Used to compute the speed factor.
# Values are deliberately conservative; they only steer estimates and big downgrades.
REFERENCE_GPU_FP16_TFLOPS = {
    Tier.ULTRA: 40.0,
    Tier.HIGH: 25.0,
    Tier.MEDIUM: 15.0,
    Tier.LOW: 8.0,
}
REFERENCE_CPU_GFLOPS = 150.0
# Below this share of the reference, the GPU is treated as one tier lower.
DOWNGRADE_BELOW = 0.35
# A CPU must transcribe at least this fast (real-time factor) to keep Whisper medium.
CPU_MEDIUM_MAX_RTF = 0.9

GPU_BENCH_SCRIPT = r"""
import json, sys, time
try:
    import torch
except Exception as e:
    print(json.dumps({"error": f"torch: {e}"})); sys.exit(0)
if not torch.cuda.is_available():
    print(json.dumps({"error": "no cuda"})); sys.exit(0)
dev = torch.device("cuda")
res = {"device": torch.cuda.get_device_name(0)}
budget = float(sys.argv[1]) if len(sys.argv) > 1 else 8.0
n = 4096
for dtype, key in ((torch.float16, "fp16_tflops"), (torch.float32, "fp32_tflops")):
    a = torch.randn(n, n, device=dev, dtype=dtype); b = torch.randn(n, n, device=dev, dtype=dtype)
    for _ in range(3): a @ b
    torch.cuda.synchronize(); t0 = time.perf_counter(); it = 0
    while time.perf_counter() - t0 < budget / 2:
        a @ b; it += 1
        if it % 10 == 0: torch.cuda.synchronize()
    torch.cuda.synchronize(); dt = time.perf_counter() - t0
    res[key] = round(2 * n**3 * it / dt / 1e12, 2)
    del a, b; torch.cuda.empty_cache()
print(json.dumps(res))
"""


class Calibration(BaseModel):
    fingerprint: str
    measured_at: str
    duration_s: float
    cpu_gflops: float | None = None
    gpu_fp16_tflops: float | None = None
    gpu_fp32_tflops: float | None = None
    asr_rtf: float | None = None
    speed_factor: float = 1.0  # >1 faster than typical for the tier
    tier_steps_down: int = 0
    settings_patch: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


def cpu_gflops(seconds: float = 3.0) -> float | None:
    try:
        import numpy as np
    except ImportError:
        return None
    n = 1024
    a = np.random.rand(n, n).astype(np.float32)
    b = np.random.rand(n, n).astype(np.float32)
    a @ b
    it, t0 = 0, time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        a @ b
        it += 1
    return round(2 * n**3 * it / (time.perf_counter() - t0) / 1e9, 1)


def gpu_tflops(
    engine_python: str | None, seconds: float = 8.0, runner: proc.Runner = proc.run
) -> dict[str, Any]:
    if not engine_python:
        return {"error": "torch environment not installed yet"}
    res = runner([engine_python, "-c", GPU_BENCH_SCRIPT, str(seconds)], timeout=seconds + 60)
    if res is None or not res.ok:
        return {"error": (res.stderr[-500:] if res else "engine python not found")}
    try:
        return dict(json.loads(res.stdout.strip().splitlines()[-1]))
    except (ValueError, IndexError):
        return {"error": f"bad output: {res.stdout[-200:]}"}


def derive(
    selection: ProfileSelection,
    *,
    fingerprint: str,
    cpu: float | None,
    gpu: dict[str, Any],
    asr_rtf: float | None,
    duration_s: float,
) -> Calibration:
    """Turn raw measurements into profile adjustments. Pure; unit-tested."""
    cal = Calibration(
        fingerprint=fingerprint,
        measured_at=datetime.now(UTC).isoformat(timespec="seconds"),
        duration_s=round(duration_s, 1),
        cpu_gflops=cpu,
        gpu_fp16_tflops=gpu.get("fp16_tflops"),
        gpu_fp32_tflops=gpu.get("fp32_tflops"),
        asr_rtf=asr_rtf,
    )
    tier = selection.tier
    factors: list[float] = []

    if tier != Tier.CPU and cal.gpu_fp16_tflops:
        ratio = cal.gpu_fp16_tflops / REFERENCE_GPU_FP16_TFLOPS[tier]
        factors.append(ratio)
        if ratio < DOWNGRADE_BELOW and tier != Tier.LOW:
            cal.tier_steps_down = 1
            cal.notes.append(
                "Видеокарта оказалась заметно медленнее типичной для своего объёма памяти "
                f"({cal.gpu_fp16_tflops} TFLOPS), поэтому профиль понижен на одну ступень."
            )
        if cal.gpu_fp32_tflops and cal.gpu_fp16_tflops < cal.gpu_fp32_tflops * 1.5:
            cal.settings_patch.update({"tts_precision": "fp32"})
            if selection.settings.whisper_compute_type in ("float16", "int8_float16"):
                cal.settings_patch["whisper_compute_type"] = "int8_float32"
            cal.notes.append("Половинная точность (fp16) на этой видеокарте не быстрее обычной — отключена.")
    elif tier != Tier.CPU and "error" in gpu:
        cal.notes.append("Тест видеокарты не прошёл, профиль оставлен по характеристикам.")

    if cpu:
        cpu_ratio = cpu / REFERENCE_CPU_GFLOPS
        if tier == Tier.CPU:
            factors.append(cpu_ratio)

    if asr_rtf is not None:
        expected = float(load_profile_data()["tiers"][tier.value]["rtf"]["asr"])
        factors.append(expected / max(asr_rtf, 1e-3))
        if tier == Tier.CPU and selection.settings.whisper_model == "medium" and asr_rtf > CPU_MEDIUM_MAX_RTF:
            cal.settings_patch["whisper_model"] = "small"
            cal.notes.append(
                "Процессор распознаёт речь модели medium медленнее реального времени — "
                "выбрана более лёгкая модель small."
            )

    if factors:
        # Geometric mean, clamped: estimates should move, not explode.
        prod = 1.0
        for f in factors:
            prod *= f
        cal.speed_factor = round(min(4.0, max(0.1, prod ** (1 / len(factors)))), 2)
    return cal


def apply_calibration(
    selection: ProfileSelection,
    cal: Calibration,
    reselect: Callable[[Tier], ProfileSelection],
) -> ProfileSelection:
    sel = selection
    if cal.tier_steps_down and sel.tier not in (Tier.LOW, Tier.CPU):
        sel = reselect(sel.tier.lower(cal.tier_steps_down))
    if cal.settings_patch:
        valid = {k: v for k, v in cal.settings_patch.items() if k in ProfileSettings.model_fields}
        if valid.get("whisper_compute_type") and sel.settings.whisper_device == "cpu":
            valid.pop("whisper_compute_type")
        settings = ProfileSettings.model_validate(sel.settings.model_dump() | valid)
        sel = sel.model_copy(update={"settings": settings})
    return sel.model_copy(update={"reasons": [*sel.reasons, *cal.notes]})


def run_calibration(
    selection: ProfileSelection,
    *,
    fingerprint: str,
    engine_python: str | None = None,
    asr_probe: Callable[[ProfileSettings], float | None] | None = None,
    budget_s: float = 30.0,
    progress: Callable[[str, float], None] = lambda _m, _p: None,
) -> Calibration:
    t0 = time.perf_counter()
    progress("Тест процессора", 0.05)
    cpu = cpu_gflops(seconds=min(3.0, budget_s * 0.1))
    gpu: dict[str, Any] = {}
    if selection.settings.tts_device == "cuda":
        progress("Тест видеокарты", 0.2)
        gpu = gpu_tflops(engine_python, seconds=min(8.0, budget_s * 0.3))
    asr = None
    remaining = budget_s - (time.perf_counter() - t0)
    if asr_probe is not None and remaining > 5:
        progress("Тестовое распознавание речи", 0.5)
        try:
            asr = asr_probe(selection.settings)
        except Exception as exc:
            log.warning("ASR calibration probe failed: %s", exc)
    progress("Готово", 1.0)
    cal = derive(
        selection,
        fingerprint=fingerprint,
        cpu=cpu,
        gpu=gpu,
        asr_rtf=asr,
        duration_s=time.perf_counter() - t0,
    )
    log.info("calibration: %s", cal.model_dump_json())
    return cal


def calibration_audio_path() -> Path:
    """Bundled public-domain speech clip used by the ASR probe (added with the pipeline)."""
    return Path(__file__).resolve().parent / "data" / "calibration_speech.flac"
