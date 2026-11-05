from __future__ import annotations

import math

import pytest

from autodub.hardware.guard import VRAM_HEADROOM_MB
from autodub.hardware.models import Accelerator, EncoderInfo, GpuVendor
from autodub.hardware.profiles import (
    GIB,
    TIER_ORDER,
    Tier,
    apply_overrides,
    estimate_minutes,
    fragments_for,
    load_profile_data,
    model_keys,
    resident_sets,
    select_profile,
)

from .conftest import gpu, make_report, nvidia


@pytest.mark.parametrize(
    ("vram_mb", "tier"),
    [
        (24564, Tier.ULTRA),  # RTX 4090
        (12282, Tier.ULTRA),  # "12 GB" card reports slightly less than 12 GiB
        (11264, Tier.HIGH),  # 11 GB (RTX 2080 Ti)
        (8188, Tier.HIGH),
        (6144, Tier.MEDIUM),
        (5120, Tier.LOW),
        (4096, Tier.LOW),
        (3072, Tier.CPU),
        (2048, Tier.CPU),
    ],
)
def test_tier_by_vram(vram_mb: int, tier: Tier) -> None:
    sel = select_profile(make_report([nvidia(vram_mb)]))
    assert sel.tier == tier
    if tier == Tier.CPU:
        assert any("видеопамяти" in w for w in sel.warnings)
        assert sel.settings.whisper_device == "cpu"
    else:
        assert sel.accelerator == Accelerator.CUDA
        assert sel.settings.whisper_device == "cuda"


def test_profile_values_match_spec() -> None:
    ultra = select_profile(make_report([nvidia(16384)])).settings
    assert ultra.keep_models_loaded and ultra.whisper_model == "large-v3"
    assert ultra.whisper_batch_size >= 16
    medium = select_profile(make_report([nvidia(6144)])).settings
    assert medium.whisper_compute_type == "int8_float16" and not medium.keep_models_loaded
    low = select_profile(make_report([nvidia(4096)])).settings
    assert low.demucs_segment < medium.demucs_segment
    cpu = select_profile(make_report([])).settings
    assert cpu.whisper_compute_type == "int8" and cpu.whisper_model in ("small", "medium")


def test_low_ram_lowers_gpu_tier() -> None:
    sel = select_profile(make_report([nvidia(16384)], ram_gb=8))
    assert sel.tier == Tier.MEDIUM
    assert any("оперативной памяти" in r for r in sel.reasons)


def test_old_nvidia_driver_falls_back_to_cpu() -> None:
    sel = select_profile(make_report([nvidia(8192, cuda="11.8", driver="522.06")]))
    assert sel.accelerator == Accelerator.CPU
    assert sel.tier == Tier.CPU
    assert any("Обновите драйвер" in w for w in sel.warnings)


def test_amd_rdna3_on_windows11_uses_rocm_but_whisper_on_cpu() -> None:
    rep = make_report([gpu(GpuVendor.AMD, "AMD Radeon RX 7900 XTX", 24560)])
    sel = select_profile(rep)
    assert sel.accelerator == Accelerator.ROCM
    assert sel.tier == Tier.ULTRA
    assert sel.settings.tts_device == "cuda"  # torch ROCm exposes the "cuda" device
    assert sel.settings.whisper_device == "cpu"
    assert sel.settings.translate_offline_device == "cpu"


def test_amd_on_windows10_uses_directml_for_separation() -> None:
    rep = make_report([gpu(GpuVendor.AMD, "AMD Radeon RX 7800 XT", 16368)], os_version="10.0.19045")
    sel = select_profile(rep)
    assert sel.accelerator == Accelerator.DIRECTML
    assert sel.tier == Tier.CPU
    assert sel.settings.separation_engine == "mdx_onnx"
    assert sel.settings.separation_device == "directml"


def test_old_amd_card_uses_directml() -> None:
    sel = select_profile(make_report([gpu(GpuVendor.AMD, "AMD Radeon RX 580 Series", 8192)]))
    assert sel.accelerator == Accelerator.DIRECTML


def test_integrated_only_is_cpu() -> None:
    rep = make_report([gpu(GpuVendor.INTEL, "Intel(R) UHD Graphics 770", 128, integrated=True)])
    sel = select_profile(rep)
    assert sel.accelerator == Accelerator.CPU and sel.tier == Tier.CPU


def test_hybrid_laptop_prefers_nvidia() -> None:
    rep = make_report(
        [
            gpu(GpuVendor.INTEL, "Intel(R) Iris(R) Xe Graphics", 128, integrated=True),
            nvidia(8188, name="NVIDIA GeForce RTX 4060 Laptop GPU"),
        ],
        encoders=EncoderInfo(ffmpeg_found=True, nvenc=True, qsv=True),
    )
    sel = select_profile(rep)
    assert sel.tier == Tier.HIGH
    assert sel.settings.ffmpeg_encoder == "h264_nvenc"


def test_pascal_without_tensor_cores_avoids_fp16() -> None:
    sel = select_profile(make_report([nvidia(8192, name="NVIDIA GeForce GTX 1080", cc="6.1")]))
    assert sel.settings.tts_precision == "fp32"
    assert "float16" not in sel.settings.whisper_compute_type
    assert any("тензорных ядер" in r for r in sel.reasons)


@pytest.mark.parametrize(
    ("cores", "ram", "avx2", "model"),
    [
        (8, 32, True, "medium"),
        (4, 32, True, "small"),
        (8, 8, True, "small"),
        (16, 64, False, "small"),
    ],
)
def test_cpu_whisper_model(cores: int, ram: int, avx2: bool, model: str) -> None:
    sel = select_profile(make_report([], cores=cores, threads=cores * 2, ram_gb=ram, avx2=avx2))
    assert sel.settings.whisper_model == model
    assert sel.settings.cpu_threads == cores - 1
    assert any("займёт долго" in w for w in sel.warnings)


def test_eco_mode() -> None:
    rep = make_report([nvidia(12282)], on_battery=True, cores=8)
    auto = select_profile(rep)
    assert auto.suggest_eco and auto.tier == Tier.ULTRA
    eco = select_profile(rep, eco=True)
    assert eco.tier == Tier.HIGH
    assert eco.settings.cpu_threads == 4
    assert not eco.suggest_eco and not eco.settings.keep_models_loaded


def test_forced_tier_and_cannot_force_gpu_without_gpu() -> None:
    sel = select_profile(make_report([nvidia(24564)]), forced_tier=Tier.LOW)
    assert sel.tier == Tier.LOW
    sel = select_profile(make_report([]), forced_tier=Tier.HIGH)
    assert sel.tier == Tier.CPU
    assert any("нельзя" in w for w in sel.warnings)


def test_overrides() -> None:
    sel = select_profile(make_report([nvidia(8192)]), overrides={"whisper_batch_size": 2})
    assert sel.settings.whisper_batch_size == 2
    assert sel.overridden == ["whisper_batch_size"]
    with pytest.raises(ValueError):
        apply_overrides(sel, {"no_such_setting": 1})
    with pytest.raises(ValueError):
        apply_overrides(sel, {"demucs_segment": 30})  # htdemucs maximum is 7.8 s


@pytest.mark.parametrize(("mbit", "frags"), [(None, 8), (5, 4), (50, 8), (500, 16)])
def test_network_fragments(mbit: float | None, frags: int) -> None:
    assert fragments_for(mbit, load_profile_data()) == frags
    sel = select_profile(make_report([], mbit=mbit))
    assert sel.settings.ytdlp_fragments == frags
    assert sel.settings.aria2_connections <= 16


def test_amd_gpu_prefers_amf() -> None:
    rep = make_report(
        [gpu(GpuVendor.AMD, "AMD Radeon RX 7900 XT", 20464)],
        encoders=EncoderInfo(ffmpeg_found=True, amf=True, nvenc=True),
    )
    assert select_profile(rep).settings.ffmpeg_encoder == "h264_amf"


@pytest.mark.parametrize("tier", [Tier.ULTRA, Tier.HIGH, Tier.MEDIUM, Tier.LOW])
def test_models_fit_tier_minimum_vram(tier: Tier) -> None:
    """Every set of models resident at the same time must fit the smallest card of the tier."""
    data = load_profile_data()
    vram_mb = math.ceil(data["thresholds"][tier.value] * GIB * (1 - data["thresholds"]["tolerance"]))
    sel = select_profile(make_report([nvidia(vram_mb)]))
    assert sel.tier == tier
    keys = model_keys(sel.settings)
    for group in resident_sets(sel.settings):
        need = sum(data["models"][keys[s]]["vram_mb"] for s in group) + VRAM_HEADROOM_MB
        assert need <= vram_mb, f"{tier}: {group} needs {need} MB > {vram_mb} MB"


def test_every_tier_model_has_resource_entry() -> None:
    data = load_profile_data()
    for vram in (24564, 12282, 8188, 6144, 4096, 0):
        sel = select_profile(make_report([nvidia(vram)] if vram else []))
        for key in model_keys(sel.settings).values():
            assert key in data["models"], key


def test_estimates_grow_down_the_tiers() -> None:
    prev = 0
    for vram in (24564, 8188, 6144, 4096, 0):
        sel = select_profile(make_report([nvidia(vram)] if vram else []))
        lo, hi = estimate_minutes(sel)
        assert lo < hi
        assert hi >= prev
        prev = hi
    assert TIER_ORDER[-1] == Tier.CPU


def test_faster_machine_gets_shorter_estimate() -> None:
    sel = select_profile(make_report([nvidia(8192)]))
    assert estimate_minutes(sel, speed_factor=2)[1] < estimate_minutes(sel)[1]


def test_every_setting_has_ui_description() -> None:
    from autodub.hardware.profiles import ProfileSettings
    from autodub.hardware.settings_meta import SETTINGS

    assert set(SETTINGS) == set(ProfileSettings.model_fields)
    for meta in SETTINGS.values():
        assert meta.description and meta.label
