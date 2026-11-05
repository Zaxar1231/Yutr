from __future__ import annotations

import json
from pathlib import Path

from autodub.hardware.detect import gpu as gpu_mod
from autodub.hardware.detect.cpu import parse_proc_cpuinfo
from autodub.hardware.detect.disk import kind_linux
from autodub.hardware.detect.encoders import detect_encoders, listed_encoders
from autodub.hardware.models import GpuVendor

from .conftest import FakeRunner, make_report, nvidia

SMI_CSV = "NVIDIA GeForce RTX 3060, 12288, 11800, 576.02, 8.6\n"
SMI_HEADER = "| NVIDIA-SMI 576.02   Driver Version: 576.02   CUDA Version: 12.9 |"


def test_parse_nvidia_smi() -> None:
    [g] = gpu_mod.parse_nvidia_smi(SMI_CSV, SMI_HEADER)
    assert g.name == "NVIDIA GeForce RTX 3060"
    assert (g.vram_total_mb, g.vram_free_mb) == (12288, 11800)
    assert g.cuda_version == "12.9" and g.compute_capability == "8.6"


def test_nvidia_smi_retries_without_compute_cap_on_old_driver() -> None:
    runner = FakeRunner(
        {
            "compute_cap": (2, "Field compute_cap is not a valid field"),
            "--query-gpu=name,memory.total,memory.free,driver_version --": (
                0,
                "NVIDIA GeForce GTX 1060 6GB, 6144, 5900, 472.12\n",
            ),
            "nvidia-smi": (0, "CUDA Version: 11.4"),
        }
    )
    [g] = gpu_mod.detect_nvidia_smi(runner) or []
    assert g.vram_total_mb == 6144 and g.compute_capability is None
    assert g.cuda_version == "11.4"


def test_nvidia_smi_missing() -> None:
    assert gpu_mod.detect_nvidia_smi(FakeRunner({})) is None


def test_parse_windows_adapters_qword_binary_and_virtual() -> None:
    raw = json.dumps(
        [
            {
                "Name": "AMD Radeon RX 7800 XT",
                "Driver": "32.0.21013.1000",
                "Qw": 17163091968,
                "Mem": None,
            },
            # REG_BINARY 4 GB value, little-endian
            {
                "Name": "Intel(R) Arc(TM) A380 Graphics",
                "Driver": "32.0.101.6078",
                "Qw": None,
                "Mem": [0, 0, 0, 0, 1, 0, 0, 0],
            },
            {"Name": "Intel(R) UHD Graphics 770", "Driver": "31.0.101.4502", "Qw": 134217728},
            {"Name": "Microsoft Basic Display Adapter", "Driver": "10.0"},
            {"Name": "Parsec Virtual Display Adapter", "Driver": "0.45"},
        ]
    )
    gpus = gpu_mod.parse_windows_adapters(raw)
    assert [g.name for g in gpus] == [
        "AMD Radeon RX 7800 XT",
        "Intel(R) Arc(TM) A380 Graphics",
        "Intel(R) UHD Graphics 770",
    ]
    amd, arc, uhd = gpus
    assert amd.vendor == GpuVendor.AMD and amd.vram_total_mb == 16368 and not amd.integrated
    assert arc.vram_total_mb == 4096 and not arc.integrated
    assert uhd.integrated


def test_parse_windows_single_adapter_object() -> None:
    raw = json.dumps({"Name": "AMD Radeon(TM) Graphics", "Qw": 536870912})
    [g] = gpu_mod.parse_windows_adapters(raw)
    assert g.integrated


def test_windows_vram_usage() -> None:
    raw = json.dumps(
        [
            {"InstanceName": "luid_0x1_phys_0", "CookedValue": 1073741824.0},
            {"InstanceName": "luid_0x2_phys_0", "CookedValue": 104857600.0},
        ]
    )
    assert gpu_mod.parse_windows_vram_usage(raw) == 1024


def test_detect_windows_fills_free_vram(monkeypatch) -> None:
    adapters = json.dumps([{"Name": "AMD Radeon RX 6700 XT", "Qw": 12868124672}])
    usage = json.dumps({"InstanceName": "x", "CookedValue": 2147483648})
    runner = FakeRunner({"Dedicated Usage": (0, usage), "4d36e968": (0, adapters)})
    [g] = gpu_mod.detect_windows(runner)
    assert g.vram_total_mb == 12272 and g.vram_free_mb == 12272 - 2048


def test_vendor_from_name() -> None:
    assert gpu_mod.vendor_from_name("NVIDIA GeForce RTX 4090") == GpuVendor.NVIDIA
    assert gpu_mod.vendor_from_name("AMD Radeon RX 9070 XT") == GpuVendor.AMD
    assert gpu_mod.vendor_from_name("Intel(R) Arc(TM) B580 Graphics") == GpuVendor.INTEL


def test_linux_sysfs(tmp_path: Path) -> None:
    dev = tmp_path / "card0" / "device"
    dev.mkdir(parents=True)
    (dev / "vendor").write_text("0x1002\n")
    (dev / "mem_info_vram_total").write_text(str(16 * 1024**3))
    (dev / "mem_info_vram_used").write_text(str(1024**3))
    [g] = gpu_mod.detect_linux_sysfs(tmp_path)
    assert g.vendor == GpuVendor.AMD and g.vram_total_mb == 16384 and g.vram_free_mb == 15360


def test_parse_proc_cpuinfo() -> None:
    text = (
        "processor\t: 0\nmodel name\t: AMD Ryzen 7 7800X3D\nflags\t\t: fpu sse avx avx2 avx512f\n"
    )
    name, flags = parse_proc_cpuinfo(text)
    assert name == "AMD Ryzen 7 7800X3D" and {"avx2", "avx512f"} <= flags


def test_disk_kind_linux(tmp_path: Path) -> None:
    for name, rot in (("nvme0n1", "0"), ("sda", "1")):
        (tmp_path / name / "queue").mkdir(parents=True)
        (tmp_path / name / "queue" / "rotational").write_text(rot)
    assert kind_linux("nvme0n1p2", tmp_path) == "ssd"
    assert kind_linux("sda1", tmp_path) == "hdd"
    assert kind_linux(None, tmp_path) == "unknown"


ENCODERS = """Encoders:
 V..... = Video
 ------
 V....D libx264              libx264 H.264 / AVC
 V....D h264_nvenc           NVIDIA NVENC H.264 encoder
 V....D h264_qsv             H.264 (Intel Quick Sync Video acceleration)
 V....D h264_amf             AMD AMF H.264 Encoder
 A....D aac                  AAC
"""


def test_listed_encoders() -> None:
    assert {"h264_nvenc", "h264_qsv", "h264_amf", "libx264"} <= listed_encoders(ENCODERS)


def test_encoders_are_probed_not_just_listed() -> None:
    # Build lists all three, but only NVENC actually works on this machine.
    runner = FakeRunner(
        {
            "-encoders": (0, ENCODERS),
            "h264_nvenc -f null": (0, ""),
            "h264_qsv -f null": (1, ""),
            "h264_amf -f null": (1, ""),
        }
    )
    enc = detect_encoders("ffmpeg", runner)
    assert enc.ffmpeg_found and enc.nvenc and not enc.qsv and not enc.amf


def test_no_ffmpeg() -> None:
    assert not detect_encoders("ffmpeg", FakeRunner({})).ffmpeg_found


def test_fingerprint_ignores_volatile_values_but_sees_driver_change() -> None:
    a = make_report([nvidia(8192)], mbit=10, on_battery=True)
    b = make_report([nvidia(8192)], mbit=900, on_battery=False)
    assert a.fingerprint() == b.fingerprint()
    c = make_report([nvidia(8192, driver="590.10")])
    assert a.fingerprint() != c.fingerprint()


def test_primary_gpu_prefers_discrete() -> None:
    from .conftest import gpu

    rep = make_report(
        [gpu(GpuVendor.INTEL, "Intel(R) UHD Graphics", 128, integrated=True), nvidia(6144)]
    )
    assert rep.primary_gpu().vendor == GpuVendor.NVIDIA
