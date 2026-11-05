"""Performance profile selection: hardware report -> concrete settings for every stage."""

from __future__ import annotations

import re
import tomllib
from enum import StrEnum
from functools import cache
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, Field

from autodub.core.text import count
from autodub.hardware.models import Accelerator, GpuInfo, GpuVendor, HardwareReport

GIB = 1024


class Tier(StrEnum):
    ULTRA = "ultra"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    CPU = "cpu"

    @property
    def label(self) -> str:
        return TIER_TITLES[self]

    def lower(self, steps: int = 1) -> Tier:
        idx = min(TIER_ORDER.index(self) + steps, len(TIER_ORDER) - 1)
        return TIER_ORDER[idx]


TIER_ORDER = [Tier.ULTRA, Tier.HIGH, Tier.MEDIUM, Tier.LOW, Tier.CPU]
TIER_TITLES = {
    Tier.ULTRA: "Ультра",
    Tier.HIGH: "Высокий",
    Tier.MEDIUM: "Средний",
    Tier.LOW: "Низкий",
    Tier.CPU: "Только процессор",
}

Device = Literal["cuda", "cpu"]
SeparationDevice = Literal["cuda", "cpu", "directml"]


class ProfileSettings(BaseModel):
    """Everything a profile controls. Each field can be overridden by the user."""

    whisper_model: str
    whisper_compute_type: str
    whisper_batch_size: int = Field(ge=1)
    whisper_device: Device
    align_device: Device
    demucs_model: str
    demucs_segment: float = Field(gt=0, le=7.8)  # htdemucs was trained on 7.8 s segments
    demucs_shifts: int = Field(ge=0)
    # "demucs" (torch) or "mdx_onnx" (UVR MDX-Net through ONNX Runtime, works with DirectML)
    separation_engine: Literal["demucs", "mdx_onnx"]
    separation_device: SeparationDevice
    tts_model: str
    tts_precision: Literal["fp16", "fp32"]
    tts_device: Device
    keep_models_loaded: bool
    translate_offline_compute_type: str
    translate_offline_device: Device
    cpu_threads: int = Field(ge=1)
    ffmpeg_encoder: str
    ytdlp_fragments: int = Field(ge=1, le=64)
    aria2_connections: int = Field(ge=1, le=16)


OVERRIDABLE = tuple(ProfileSettings.model_fields)


class ProfileSelection(BaseModel):
    tier: Tier
    accelerator: Accelerator
    settings: ProfileSettings
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    suggest_eco: bool = False
    eco: bool = False
    overridden: list[str] = Field(default_factory=list)


@cache
def load_profile_data() -> dict[str, Any]:
    text = resources.files("autodub.hardware.data").joinpath("profiles.toml").read_text("utf-8")
    return tomllib.loads(text)


# --- accelerator --------------------------------------------------------------------------

# AMD GPUs with official PyTorch ROCm wheels on Windows 11 (RDNA3/RDNA4 desktop + Ryzen AI).
_ROCM_WINDOWS = re.compile(
    r"radeon\s+(rx\s+(7[6-9]\d\d|9[06]\d\d)|pro\s+w7\d{3}|ai\s+max|8[89]0m)", re.IGNORECASE
)


def _version_tuple(v: str | None) -> tuple[int, ...]:
    if not v:
        return ()
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def windows_11(report: HardwareReport) -> bool:
    if report.os != "Windows":
        return False
    parts = _version_tuple(report.os_version)
    return len(parts) >= 3 and parts[2] >= 22000


def choose_accelerator(report: HardwareReport) -> tuple[Accelerator, GpuInfo | None, list[str]]:
    """Return accelerator, the GPU it uses, and human-readable notes."""
    notes: list[str] = []
    discrete = [g for g in report.gpus if not g.integrated and g.vendor != GpuVendor.OTHER]
    nvidia = sorted(
        (g for g in discrete if g.vendor == GpuVendor.NVIDIA),
        key=lambda g: g.vram_total_mb or 0,
        reverse=True,
    )
    if nvidia:
        gpu = nvidia[0]
        if _version_tuple(gpu.cuda_version)[:1] >= (12,):
            return Accelerator.CUDA, gpu, notes
        notes.append(
            f"Драйвер NVIDIA {gpu.driver_version or ''} слишком старый для CUDA 12. "
            "Обновите драйвер, и программа начнёт использовать видеокарту."
        )
    amd = [g for g in discrete if g.vendor == GpuVendor.AMD]
    for gpu in amd:
        supported = (
            report.os == "Windows" and windows_11(report) and _ROCM_WINDOWS.search(gpu.name)
        ) or (report.os == "Linux" and (gpu.vram_total_mb or 0) >= 4 * GIB)
        if supported:
            return Accelerator.ROCM, gpu, notes
    others = [g for g in discrete if g.vendor in (GpuVendor.AMD, GpuVendor.INTEL)]
    if others and report.os == "Windows":
        notes.append(
            f"Видеокарта {others[0].name} не поддерживается PyTorch на Windows. "
            "Разделение голоса пойдёт через DirectML, остальное — на процессоре."
        )
        return Accelerator.DIRECTML, others[0], notes
    return Accelerator.CPU, None, notes


# --- tier ---------------------------------------------------------------------------------


def tier_by_vram(vram_mb: int, ram_mb: int, data: dict[str, Any]) -> tuple[Tier, str | None]:
    th = data["thresholds"]
    tol = 1 - float(th["tolerance"])
    tier = Tier.CPU
    for t in (Tier.ULTRA, Tier.HIGH, Tier.MEDIUM, Tier.LOW):
        if vram_mb >= th[t.value] * GIB * tol:
            tier = t
            break
    note = None
    while tier != Tier.CPU and ram_mb < th["min_ram_gb"][tier.value] * GIB * tol:
        lowered = tier.lower()
        note = (
            f"Профиль понижен с «{tier.label}» до «{lowered.label}»: "
            f"для него нужно от {th['min_ram_gb'][tier.value]} ГБ оперативной памяти."
        )
        tier = lowered
    return tier, note


def fragments_for(mbit: float | None, data: dict[str, Any]) -> int:
    net = data["network"]
    if mbit is None:
        return int(net["default_fragments"])
    for step in net["steps"]:
        if mbit < step["below_mbit"]:
            return int(step["fragments"])
    return int(net["steps"][-1]["fragments"])


def encoder_for(report: HardwareReport, gpu: GpuInfo | None) -> str:
    enc = report.encoders
    by_vendor = {GpuVendor.NVIDIA: "nvenc", GpuVendor.INTEL: "qsv", GpuVendor.AMD: "amf"}
    names = {"nvenc": "h264_nvenc", "qsv": "h264_qsv", "amf": "h264_amf"}
    preferred = by_vendor.get(gpu.vendor) if gpu else None
    order = ([preferred] if preferred else []) + ["nvenc", "qsv", "amf"]
    for key in order:
        if key and getattr(enc, key):
            return names[key]
    return "libx264"


def cpu_whisper_model(report: HardwareReport, data: dict[str, Any]) -> str:
    rule = data["cpu_whisper_medium"]
    strong = (
        report.cpu.physical_cores >= rule["min_physical_cores"]
        and report.memory.total_mb >= rule["min_ram_gb"] * GIB * 0.95
        and (report.cpu.avx2 is True or not rule["requires_avx2"])
    )
    return "medium" if strong else "small"


def _has_fast_fp16(gpu: GpuInfo | None) -> bool:
    """Tensor cores appeared with compute capability 7.0; older cards run fp16 slowly."""
    cc = _version_tuple(gpu.compute_capability if gpu else None)
    return not cc or cc >= (7, 0)


def _settings_for(
    tier: Tier,
    accel: Accelerator,
    gpu: GpuInfo | None,
    report: HardwareReport,
    data: dict[str, Any],
    *,
    eco: bool,
) -> tuple[ProfileSettings, list[str]]:
    t = data["tiers"][tier.value]
    notes: list[str] = []
    torch_gpu: Device = (
        "cuda" if accel in (Accelerator.CUDA, Accelerator.ROCM) and tier != Tier.CPU else "cpu"
    )
    # faster-whisper (CTranslate2) supports only NVIDIA CUDA.
    whisper_gpu: Device = "cuda" if accel == Accelerator.CUDA and tier != Tier.CPU else "cpu"

    whisper_model = t["whisper_model"]
    whisper_ct = t["whisper_compute_type"]
    batch = int(t["whisper_batch_size"])
    if whisper_gpu == "cpu":
        whisper_model = cpu_whisper_model(report, data)
        whisper_ct, batch = "int8", 1
        if tier != Tier.CPU:  # AMD GPU via ROCm: Whisper on CPU, torch models on GPU
            notes.append(
                "Распознавание речи (Whisper) работает только на NVIDIA, "
                "поэтому идёт на процессоре."
            )

    tts_precision = t["tts_precision"]
    if torch_gpu == "cuda" and not _has_fast_fp16(gpu):
        notes.append(
            "У видеокарты нет тензорных ядер: вместо float16 используется "
            "int8/float32, так быстрее."
        )
        if whisper_gpu == "cuda":
            whisper_ct = "int8_float32" if whisper_ct.startswith("int8") else "int8"
        tts_precision = "fp32"

    cores = report.cpu.physical_cores
    threads = max(1, cores - 1) if tier == Tier.CPU else cores
    if eco:
        threads = max(1, int(cores * float(data["eco"]["thread_fraction"])))

    fragments = fragments_for(report.network.mbit_per_s, data)
    settings = ProfileSettings(
        whisper_model=whisper_model,
        whisper_compute_type=whisper_ct,
        whisper_batch_size=batch,
        whisper_device=whisper_gpu,
        align_device="cuda" if torch_gpu == "cuda" and t["align_on_gpu"] else "cpu",
        demucs_model=t["demucs_model"],
        demucs_segment=float(t["demucs_segment"]),
        demucs_shifts=int(t["demucs_shifts"]),
        separation_engine="mdx_onnx" if accel == Accelerator.DIRECTML else "demucs",
        separation_device="directml" if accel == Accelerator.DIRECTML else torch_gpu,
        tts_model=t["tts_model"],
        tts_precision=tts_precision if torch_gpu == "cuda" else "fp32",
        tts_device=torch_gpu,
        keep_models_loaded=bool(t["keep_models_loaded"]) and not eco,
        translate_offline_compute_type=t["translate_offline_compute_type"]
        if whisper_gpu == "cuda"
        else "int8",
        # CTranslate2 (MADLAD) is CUDA-only as well.
        translate_offline_device="cuda"
        if whisper_gpu == "cuda" and t["translate_offline_on_gpu"]
        else "cpu",
        cpu_threads=threads,
        ffmpeg_encoder=encoder_for(report, gpu),
        ytdlp_fragments=fragments,
        aria2_connections=min(fragments, int(data["network"]["aria2_max_connections"])),
    )
    return settings, notes


def _gb(mb: int | None) -> str:
    return f"{(mb or 0) / GIB:.1f}".replace(".0", "").replace(".", ",")


def select_profile(
    report: HardwareReport,
    *,
    eco: bool = False,
    forced_tier: Tier | None = None,
    overrides: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> ProfileSelection:
    """Pick the profile for this machine. Pure function of its inputs."""
    data = data or load_profile_data()
    accel, gpu, notes = choose_accelerator(report)
    reasons: list[str] = []
    warnings: list[str] = list(notes)

    if accel in (Accelerator.CUDA, Accelerator.ROCM) and gpu and gpu.vram_total_mb:
        tier, ram_note = tier_by_vram(gpu.vram_total_mb, report.memory.total_mb, data)
        reasons.append(
            f"Видеокарта {gpu.name} с {_gb(gpu.vram_total_mb)} ГБ видеопамяти "
            f"и {_gb(report.memory.total_mb)} ГБ оперативной памяти."
        )
        if ram_note:
            reasons.append(ram_note)
        if tier == Tier.CPU:
            warnings.append(
                f"У видеокарты {_gb(gpu.vram_total_mb)} ГБ видеопамяти, а нужно хотя бы 4 ГБ. "
                "Работа пойдёт на процессоре."
            )
    else:
        tier = Tier.CPU
        if not report.gpus or (accel == Accelerator.CPU and not notes):
            reasons.append("Подходящая видеокарта не найдена.")

    if tier == Tier.CPU:
        reasons.append(
            f"Все модели работают на процессоре {report.cpu.name} "
            f"({count(report.cpu.physical_cores, 'ядро', 'ядра', 'ядер')}, "
            f"{count(report.cpu.logical_threads, 'поток', 'потока', 'потоков')})."
        )
        warnings.append(
            "Без видеокарты обработка займёт долго: десятиминутное видео может "
            "обрабатываться больше часа. Результат будет тем же, просто медленнее."
        )

    if forced_tier is not None and forced_tier != tier:
        if accel not in (Accelerator.CUDA, Accelerator.ROCM) and forced_tier != Tier.CPU:
            warnings.append("Профиль с видеокартой выбрать нельзя: подходящей видеокарты нет.")
        else:
            reasons.append(f"Профиль «{forced_tier.label}» выбран вручную.")
            tier = forced_tier

    if eco and tier != Tier.CPU:
        tier = tier.lower(int(data["eco"]["tier_steps_down"]))
    if eco:
        reasons.append("Включён экономный режим: меньше нагрузки, дольше работает батарея.")

    settings, setting_notes = _settings_for(tier, accel, gpu, report, data, eco=eco)
    reasons.extend(setting_notes)

    sel = ProfileSelection(
        tier=tier,
        accelerator=accel,
        settings=settings,
        reasons=reasons,
        warnings=warnings,
        suggest_eco=report.power.on_battery and not eco,
        eco=eco,
    )
    if overrides:
        sel = apply_overrides(sel, overrides)
    return sel


def apply_overrides(sel: ProfileSelection, overrides: dict[str, Any]) -> ProfileSelection:
    """Apply user overrides on top of the automatic settings (validated by pydantic)."""
    unknown = set(overrides) - set(OVERRIDABLE)
    if unknown:
        raise ValueError(f"unknown profile settings: {sorted(unknown)}")
    merged = ProfileSettings.model_validate(sel.settings.model_dump() | overrides)
    # Every key the user set is "manual", even if it equals the automatic value today:
    # it must not silently change when the hardware or profile changes later.
    return sel.model_copy(update={"settings": merged, "overridden": sorted(overrides)})


# --- resource needs / estimates -----------------------------------------------------------


def model_keys(settings: ProfileSettings) -> dict[str, str]:
    """Model key in the [models] table for each stage of this profile."""
    return {
        "asr": f"whisper:{settings.whisper_model}:{settings.whisper_compute_type}",
        "separate": f"demucs:{settings.demucs_model}"
        if settings.separation_engine == "demucs"
        else "mdx:UVR-MDX-NET-Voc_FT",
        "align": "align:wav2vec2",
        "tts": f"tts:{settings.tts_model}",
        "translate": "translate:madlad400-3b",
    }


def resident_sets(settings: ProfileSettings) -> list[list[str]]:
    """Groups of stages whose models are in GPU memory at the same time."""
    gpu_stages = [
        s
        for s, dev in (
            ("asr", settings.whisper_device),
            ("align", settings.align_device),
            ("separate", settings.separation_device),
            ("tts", settings.tts_device),
            ("translate", settings.translate_offline_device),
        )
        if dev == "cuda"
    ]
    if settings.keep_models_loaded:
        # ASR and TTS stay loaded between jobs of the queue. Other stages load next to them
        # only if free VRAM allows at run time; otherwise the residents are evicted first
        # (the guard decides with the real free memory), so the static worst case is either
        # the resident pair or one transient model alone.
        resident: list[str] = [s for s in ("asr", "tts") if s in gpu_stages]
        transient = [[s] for s in gpu_stages if s not in resident]
        return [resident, *transient] if resident else transient
    return [[s] for s in gpu_stages]


def estimate_minutes(
    sel: ProfileSelection, *, video_minutes: float = 10.0, speed_factor: float = 1.0
) -> tuple[int, int]:
    """Rough processing time range in minutes. speed_factor > 1 means faster than typical."""
    data = load_profile_data()
    rtf = data["tiers"][sel.tier.value]["rtf"]
    total = sum(float(v) for v in rtf.values()) * video_minutes / max(speed_factor, 0.05)
    if sel.settings.demucs_shifts > 1:
        total += float(rtf["separate"]) * (sel.settings.demucs_shifts - 1) * video_minutes
    return max(1, round(total * 0.75)), max(2, round(total * 1.5))
