"""Data describing the detected hardware. Pure data, no detection logic."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, Field


class GpuVendor(StrEnum):
    NVIDIA = "nvidia"
    AMD = "amd"
    INTEL = "intel"
    OTHER = "other"


class Accelerator(StrEnum):
    """How heavy models are executed on this machine."""

    CUDA = "cuda"
    ROCM = "rocm"
    DIRECTML = "directml"  # ONNX Runtime DirectML only, torch runs on CPU
    CPU = "cpu"


class GpuInfo(BaseModel):
    vendor: GpuVendor
    name: str
    vram_total_mb: int | None = None
    vram_free_mb: int | None = None
    driver_version: str | None = None
    cuda_version: str | None = None  # max CUDA version the driver supports, e.g. "13.0"
    compute_capability: str | None = None  # NVIDIA only, e.g. "8.6"
    integrated: bool = False
    source: str = ""  # nvml / nvidia-smi / wmi / sysfs


class CpuInfo(BaseModel):
    name: str
    physical_cores: int
    logical_threads: int
    avx2: bool | None = None
    avx512: bool | None = None


class MemoryInfo(BaseModel):
    total_mb: int
    available_mb: int


class DiskInfo(BaseModel):
    role: str  # "models" / "output"
    path: str
    free_mb: int
    total_mb: int
    kind: str = "unknown"  # ssd / hdd / unknown


class EncoderInfo(BaseModel):
    nvenc: bool = False
    qsv: bool = False
    amf: bool = False
    ffmpeg_found: bool = False

    def available(self) -> list[str]:
        return [n for n in ("nvenc", "qsv", "amf") if getattr(self, n)]


class PowerInfo(BaseModel):
    has_battery: bool = False
    on_battery: bool = False
    percent: float | None = None


class NetworkInfo(BaseModel):
    mbit_per_s: float | None = None  # None = not measured / failed


class HardwareReport(BaseModel):
    """Everything the profile selector needs. Stored in the config as the last scan."""

    os: str
    os_version: str
    gpus: list[GpuInfo] = Field(default_factory=list)
    cpu: CpuInfo
    memory: MemoryInfo
    disks: list[DiskInfo] = Field(default_factory=list)
    encoders: EncoderInfo = Field(default_factory=EncoderInfo)
    power: PowerInfo = Field(default_factory=PowerInfo)
    network: NetworkInfo = Field(default_factory=NetworkInfo)
    warnings: list[str] = Field(default_factory=list)
    scanned_at: str = ""

    def primary_gpu(self) -> GpuInfo | None:
        """The discrete GPU with the most VRAM; falls back to any GPU."""
        if not self.gpus:
            return None
        return max(
            self.gpus,
            key=lambda g: (not g.integrated, g.vendor == GpuVendor.NVIDIA, g.vram_total_mb or 0),
        )

    def disk(self, role: str) -> DiskInfo | None:
        return next((d for d in self.disks if d.role == role), None)

    def fingerprint(self) -> str:
        """Identity of the hardware and drivers. A change triggers an automatic rescan.

        Volatile values (free memory, battery, network) are deliberately excluded.
        """
        ident = {
            "gpus": sorted(
                (g.vendor, g.name, g.vram_total_mb or 0, g.driver_version or "") for g in self.gpus
            ),
            "cpu": (self.cpu.name, self.cpu.physical_cores, self.cpu.logical_threads),
            # Round to whole GB so OS reservations do not change the fingerprint.
            "ram_gb": round(self.memory.total_mb / 1024),
        }
        return hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()[:16]
