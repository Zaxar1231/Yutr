from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from autodub.hardware.models import (
    CpuInfo,
    EncoderInfo,
    GpuInfo,
    GpuVendor,
    HardwareReport,
    MemoryInfo,
    NetworkInfo,
    PowerInfo,
)


def nvidia(
    vram_mb: int,
    *,
    name: str = "NVIDIA GeForce RTX 4070",
    cuda: str = "13.0",
    cc: str = "8.9",
    driver: str = "581.57",
) -> GpuInfo:
    return GpuInfo(
        vendor=GpuVendor.NVIDIA,
        name=name,
        vram_total_mb=vram_mb,
        vram_free_mb=vram_mb - 500,
        driver_version=driver,
        cuda_version=cuda,
        compute_capability=cc,
        source="nvml",
    )


def gpu(vendor: GpuVendor, name: str, vram_mb: int | None, *, integrated: bool = False) -> GpuInfo:
    return GpuInfo(
        vendor=vendor, name=name, vram_total_mb=vram_mb, integrated=integrated, source="registry"
    )


def make_report(
    gpus: list[GpuInfo] | None = None,
    *,
    ram_gb: float = 32,
    cores: int = 8,
    threads: int = 16,
    avx2: bool | None = True,
    os: str = "Windows",
    os_version: str = "10.0.22631",
    on_battery: bool = False,
    mbit: float | None = 150.0,
    encoders: EncoderInfo | None = None,
) -> HardwareReport:
    return HardwareReport(
        os=os,
        os_version=os_version,
        gpus=gpus or [],
        cpu=CpuInfo(
            name="Test CPU", physical_cores=cores, logical_threads=threads, avx2=avx2, avx512=False
        ),
        memory=MemoryInfo(total_mb=int(ram_gb * 1024), available_mb=int(ram_gb * 512)),
        encoders=encoders or EncoderInfo(ffmpeg_found=True),
        power=PowerInfo(has_battery=on_battery, on_battery=on_battery),
        network=NetworkInfo(mbit_per_s=mbit),
    )


@pytest.fixture
def report_factory() -> Callable[..., HardwareReport]:
    return make_report


class FakeRunner:
    """Maps a command prefix to canned output."""

    def __init__(self, table: dict[str, tuple[int, str]]):
        self.table = table
        self.calls: list[list[str]] = []

    def __call__(self, args: Any, *, timeout: float = 15.0):
        from autodub.core.proc import CmdResult

        args = list(args)
        self.calls.append(args)
        joined = " ".join(args)
        for prefix, (code, out) in self.table.items():
            if joined.startswith(prefix) or prefix in joined:
                return CmdResult(code, out, "")
        return None
