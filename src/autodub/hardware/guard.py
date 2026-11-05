"""Crash protection: resource checks before loading models and automatic profile fallback."""

from __future__ import annotations

import gc
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

from autodub.core.errors import UserFacingError
from autodub.hardware.detect.disk import existing_parent
from autodub.hardware.profiles import (
    ProfileSelection,
    ProfileSettings,
    Tier,
    load_profile_data,
    model_keys,
)

log = logging.getLogger(__name__)
_MB = 1024 * 1024

# Headroom on top of a model's own needs: CUDA context, fragmentation, the desktop.
VRAM_HEADROOM_MB = 400
RAM_HEADROOM_MB = 1024

STAGE_TITLES = {
    "download": "скачивание",
    "separate": "разделение голоса и фона",
    "asr": "распознавание речи",
    "align": "выравнивание по словам",
    "diarize": "разделение по спикерам",
    "translate": "перевод",
    "tts": "озвучка",
    "mix": "сведение",
}


class InsufficientResources(UserFacingError):
    """Not enough VRAM/RAM to load a model right now."""


class InsufficientDisk(UserFacingError):
    pass


class OutOfMemoryAtLowestProfile(UserFacingError):
    pass


def is_oom(exc: BaseException) -> bool:
    """Recognise out-of-memory errors from torch, CTranslate2, ROCm and engine processes."""
    if isinstance(exc, (MemoryError, InsufficientResources)):
        return True
    if getattr(exc, "oom", False):  # EngineError from a worker process
        return True
    if type(exc).__name__ == "OutOfMemoryError":  # torch.OutOfMemoryError / torch.cuda.*
        return True
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "out of memory",
            "cublas_status_alloc_failed",
            "cudnn_status_alloc_failed",
            "cudnn_status_not_enough_workspace",
            "hipErrorOutOfMemory".lower(),
            "failed to allocate",
        )
    )


def release_gpu_memory() -> None:
    """Free cached GPU memory. Only touches torch if this process already imported it."""
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                torch.cuda.ipc_collect()
        except Exception as exc:  # never let cleanup crash a stage
            log.warning("empty_cache failed: %s", exc)


@dataclass
class FreeMemory:
    vram_mb: int | None  # None = no GPU / unknown
    ram_mb: int


def default_probe() -> FreeMemory:
    vram = None
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        free, _total = torch.cuda.mem_get_info()
        vram = free // _MB
    else:
        from autodub.hardware.detect.gpu import detect_nvml

        gpus = detect_nvml()
        if gpus:
            vram = max(g.vram_free_mb or 0 for g in gpus)
    return FreeMemory(vram_mb=vram, ram_mb=psutil.virtual_memory().available // _MB)


@dataclass
class ResourceGuard:
    learned_peaks: dict[str, dict[str, int]] = field(default_factory=dict)
    probe: Callable[[], FreeMemory] = default_probe
    data: dict[str, Any] = field(default_factory=load_profile_data)

    def needs(self, model_key: str) -> dict[str, int]:
        base = dict(self.data["models"].get(model_key, {"vram_mb": 0, "ram_mb": 0, "disk_mb": 0}))
        learned = self.learned_peaks.get(model_key, {})
        for k in ("vram_mb", "ram_mb"):
            if k in learned:
                base[k] = int(learned[k])
        return base

    def record_peak(self, model_key: str, *, vram_mb: int | None, ram_mb: int | None) -> None:
        """Remember the measured peak (with 10 % margin) for future checks."""
        entry = self.learned_peaks.setdefault(model_key, {})
        if vram_mb:
            entry["vram_mb"] = max(int(vram_mb * 1.1), entry.get("vram_mb", 0))
        if ram_mb:
            entry["ram_mb"] = max(int(ram_mb * 1.1), entry.get("ram_mb", 0))

    def plan_load(self, model_key: str, *, resident: list[str], stage: str = "") -> list[str]:
        """Which resident GPU models to unload so that ``model_key`` fits (largest first).

        Raises InsufficientResources if it does not fit even with everything unloaded.
        """
        free = self.probe()
        if free.vram_mb is None:
            return []
        required = self.needs(model_key)["vram_mb"] + VRAM_HEADROOM_MB
        available = free.vram_mb
        evict: list[str] = []
        for key in sorted(resident, key=lambda k: -self.needs(k)["vram_mb"]):
            if available >= required:
                break
            evict.append(key)
            available += self.needs(key)["vram_mb"]
        if available < required:
            self.check(model_key, on_gpu=True, stage=stage)  # raises with a clear message
        return evict

    def check(self, model_key: str, *, on_gpu: bool, stage: str = "") -> None:
        need = self.needs(model_key)
        free = self.probe()
        what = STAGE_TITLES.get(stage, stage) or model_key
        if on_gpu and free.vram_mb is not None:
            required = need["vram_mb"] + VRAM_HEADROOM_MB
            if free.vram_mb < required:
                raise InsufficientResources(
                    f"Для этапа «{what}» не хватает видеопамяти: свободно {free.vram_mb} МБ, "
                    f"нужно около {required} МБ.",
                    hint="Закройте игры и другие программы, которые используют видеокарту.",
                    detail=f"model={model_key} need={need} free={free}",
                )
        required_ram = need["ram_mb"] + RAM_HEADROOM_MB
        if free.ram_mb < required_ram:
            raise InsufficientResources(
                f"Для этапа «{what}» не хватает оперативной памяти: свободно {free.ram_mb} МБ, "
                f"нужно около {required_ram} МБ.",
                hint="Закройте браузер и другие тяжёлые программы.",
                detail=f"model={model_key} need={need} free={free}",
            )


def run_with_fallback[T](
    stage: str,
    fn: Callable[[ProfileSelection], T],
    selection: ProfileSelection,
    reselect: Callable[[Tier], ProfileSelection],
    *,
    notify: Callable[[str], None] = lambda _msg: None,
) -> tuple[T, ProfileSelection]:
    """Run a stage; on OOM retry it on the next lower profile instead of crashing.

    Returns the result and the profile that finally worked, so the caller can keep using it
    for the remaining stages of the job.
    """
    current = selection
    while True:
        try:
            return fn(current), current
        except Exception as exc:
            if not is_oom(exc):
                raise
            release_gpu_memory()
            log.warning("OOM in stage %s on tier %s: %s", stage, current.tier, exc)
            if current.tier == Tier.CPU:
                raise OutOfMemoryAtLowestProfile(
                    f"Не хватило памяти даже в самом лёгком профиле на этапе "
                    f"«{STAGE_TITLES.get(stage, stage)}».",
                    hint="Закройте другие программы и попробуйте снова.",
                    detail=repr(exc),
                ) from exc
            lower = reselect(current.tier.lower())
            notify(
                f"Не хватило памяти на этапе «{STAGE_TITLES.get(stage, stage)}». "
                f"Повторяю в профиле «{lower.tier.label}» — будет медленнее, но без ошибки."
            )
            current = lower


# --- disk ---------------------------------------------------------------------------------


def models_disk_mb(
    settings: ProfileSettings, *, offline_translation: bool = False
) -> dict[str, int]:
    data = load_profile_data()["models"]
    keys = model_keys(settings)
    if not offline_translation:
        keys.pop("translate")
    return {k: int(data.get(v, {}).get("disk_mb", 0)) for k, v in keys.items()}


def estimate_job_disk_mb(video_size_mb: float, duration_s: float) -> int:
    """Video + 48 kHz float stems (vocals, background, dub) + intermediate audio + result."""
    stems_mb = duration_s * 48_000 * 2 * 4 * 4 / _MB
    return int(video_size_mb * 2 + stems_mb + 200)


def check_disk(path: Path, needed_mb: int, *, what: str) -> None:
    target = existing_parent(path)
    free = psutil.disk_usage(str(target)).free // _MB
    if free < needed_mb:
        raise InsufficientDisk(
            f"Не хватает места на диске для {what}: свободно {free / 1024:.1f} ГБ, "
            f"нужно около {needed_mb / 1024:.1f} ГБ.",
            hint=f"Освободите место на диске {target.anchor or target} или выберите другую папку.",
        )
