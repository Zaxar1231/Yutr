"""Full hardware scan. Every probe is isolated: one failing probe never breaks the scan."""

from __future__ import annotations

import logging
import platform
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from autodub.core import proc
from autodub.hardware.detect.cpu import detect_cpu
from autodub.hardware.detect.disk import detect_disk
from autodub.hardware.detect.encoders import detect_encoders
from autodub.hardware.detect.gpu import detect_gpus
from autodub.hardware.detect.memory import detect_memory
from autodub.hardware.detect.network import measure
from autodub.hardware.detect.power import detect_power
from autodub.hardware.models import (
    EncoderInfo,
    GpuInfo,
    HardwareReport,
    NetworkInfo,
    PowerInfo,
)

log = logging.getLogger(__name__)


def _safe[T](fn: Callable[[], T], default: T, label: str, warnings: list[str]) -> T:
    try:
        return fn()
    except Exception as exc:
        log.exception("hardware probe %s failed", label)
        warnings.append(f"Не удалось определить: {label} ({type(exc).__name__})")
        return default


def scan(
    *,
    models_dir: Path,
    output_dir: Path,
    ffmpeg: str = "ffmpeg",
    measure_network: bool = True,
    runner: proc.Runner = proc.run,
) -> HardwareReport:
    """Detect everything. Slow probes (PowerShell, ffmpeg, network) run in parallel."""
    warnings: list[str] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        f_gpu = pool.submit(
            _safe, lambda: detect_gpus(runner), list[GpuInfo](), "видеокарта", warnings
        )
        f_enc = pool.submit(
            _safe,
            lambda: detect_encoders(ffmpeg, runner),
            EncoderInfo(),
            "энкодеры ffmpeg",
            warnings,
        )
        f_net = pool.submit(
            _safe,
            measure if measure_network else NetworkInfo,
            NetworkInfo(),
            "скорость сети",
            warnings,
        )
        f_models = pool.submit(
            _safe, lambda: detect_disk("models", models_dir, runner), None, "диск моделей", warnings
        )
        f_out = pool.submit(
            _safe, lambda: detect_disk("output", output_dir, runner), None, "диск вывода", warnings
        )
        cpu = detect_cpu()
        memory = detect_memory()
        power = _safe(detect_power, PowerInfo(), "питание", warnings)
        disks = [d for d in (f_models.result(), f_out.result()) if d is not None]
        report = HardwareReport(
            os=platform.system(),
            os_version=platform.version(),
            gpus=f_gpu.result(),
            cpu=cpu,
            memory=memory,
            disks=disks,
            encoders=f_enc.result(),
            power=power,
            network=f_net.result(),
            warnings=warnings,
            scanned_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    log.info("hardware scan: %s", report.model_dump_json())
    return report


def quick_fingerprint(runner: proc.Runner = proc.run) -> str:
    """Fingerprint without slow probes, used at every start to detect hardware/driver changes."""
    report = HardwareReport(
        os=platform.system(),
        os_version=platform.version(),
        gpus=detect_gpus(runner),
        cpu=detect_cpu(),
        memory=detect_memory(),
    )
    return report.fingerprint()
