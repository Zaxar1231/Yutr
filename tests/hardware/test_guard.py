from __future__ import annotations

import pytest

from autodub.hardware.guard import (
    FreeMemory,
    InsufficientDisk,
    InsufficientResources,
    OutOfMemoryAtLowestProfile,
    ResourceGuard,
    check_disk,
    is_oom,
    run_with_fallback,
)
from autodub.hardware.profiles import ProfileSelection, Tier, select_profile

from .conftest import make_report, nvidia


class FakeOOM(Exception):
    pass


FakeOOM.__name__ = "OutOfMemoryError"


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"),
        RuntimeError("CUDA failed with error out of memory"),  # CTranslate2
        RuntimeError("CUBLAS_STATUS_ALLOC_FAILED when calling cublasCreate(handle)"),
        RuntimeError("HIP error: hipErrorOutOfMemory"),
        MemoryError(),
        FakeOOM("x"),
    ],
)
def test_is_oom(exc: Exception) -> None:
    assert is_oom(exc)


def test_engine_error_with_oom_flag() -> None:
    err = RuntimeError("worker exited")
    err.oom = True  # type: ignore[attr-defined]
    assert is_oom(err)


def test_not_oom() -> None:
    assert not is_oom(ValueError("bad url"))


def _reselect(report):
    return lambda tier: select_profile(report, forced_tier=tier)


def test_fallback_steps_down_until_it_fits() -> None:
    report = make_report([nvidia(16384)])
    sel = select_profile(report)
    tried: list[Tier] = []
    messages: list[str] = []

    def stage(s: ProfileSelection) -> str:
        tried.append(s.tier)
        if s.tier in (Tier.ULTRA, Tier.HIGH):
            raise RuntimeError("CUDA out of memory")
        return s.settings.whisper_model

    result, final = run_with_fallback("asr", stage, sel, _reselect(report), notify=messages.append)
    assert tried == [Tier.ULTRA, Tier.HIGH, Tier.MEDIUM]
    assert final.tier == Tier.MEDIUM and result == "large-v3-turbo"
    assert len(messages) == 2 and "распознавание речи" in messages[0] and "Высокий" in messages[0]


def test_fallback_gives_clear_error_at_cpu() -> None:
    report = make_report([])
    sel = select_profile(report)

    def stage(_s: ProfileSelection) -> None:
        raise MemoryError()

    with pytest.raises(OutOfMemoryAtLowestProfile) as ei:
        run_with_fallback("tts", stage, sel, _reselect(report))
    assert "озвучка" in ei.value.message


def test_non_oom_errors_are_not_swallowed() -> None:
    report = make_report([nvidia(8192)])
    with pytest.raises(ValueError):
        run_with_fallback(
            "asr",
            lambda s: (_ for _ in ()).throw(ValueError("x")),
            select_profile(report),
            _reselect(report),
        )


def guard(vram: int | None, ram: int) -> ResourceGuard:
    return ResourceGuard(probe=lambda: FreeMemory(vram_mb=vram, ram_mb=ram))


def test_check_vram() -> None:
    guard(8000, 16000).check("whisper:large-v3:float16", on_gpu=True)
    with pytest.raises(InsufficientResources) as ei:
        guard(3000, 16000).check("whisper:large-v3:float16", on_gpu=True, stage="asr")
    assert "видеопамяти" in ei.value.message and "распознавание" in ei.value.message


def test_check_ram() -> None:
    with pytest.raises(InsufficientResources) as ei:
        guard(None, 1500).check("tts:xtts_v2", on_gpu=False)
    assert "оперативной" in ei.value.message


def test_insufficient_resources_triggers_fallback() -> None:
    assert is_oom(InsufficientResources("x"))


def test_learned_peak_overrides_estimate() -> None:
    g = guard(3000, 16000)
    with pytest.raises(InsufficientResources):
        g.check("tts:xtts_v2", on_gpu=True)  # estimate 3000 + headroom
    g.record_peak("tts:xtts_v2", vram_mb=2000, ram_mb=None)
    g.check("tts:xtts_v2", on_gpu=True)  # measured 2000 * 1.1 + headroom fits
    g.record_peak("tts:xtts_v2", vram_mb=1000, ram_mb=None)
    assert g.needs("tts:xtts_v2")["vram_mb"] == 2200  # keeps the maximum


def test_plan_load_evicts_largest_resident_first() -> None:
    g = guard(2000, 32000)
    evict = g.plan_load("demucs:htdemucs_ft", resident=["tts:xtts_v2", "whisper:large-v3:float16"])
    assert evict == ["whisper:large-v3:float16"]
    assert guard(8000, 32000).plan_load("demucs:htdemucs_ft", resident=["tts:xtts_v2"]) == []
    with pytest.raises(InsufficientResources):
        guard(500, 32000).plan_load("demucs:htdemucs_ft", resident=[])


def test_check_disk(tmp_path) -> None:
    check_disk(tmp_path / "not" / "yet" / "created", 1, what="видео")
    with pytest.raises(InsufficientDisk) as ei:
        check_disk(tmp_path, 10**12, what="видео")
    assert "Освободите место" in str(ei.value)
