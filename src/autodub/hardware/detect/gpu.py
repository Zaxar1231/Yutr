"""GPU detection.

Order of sources:
1. NVML (``nvidia-ml-py``): exact total/free VRAM, driver and CUDA version for NVIDIA.
2. ``nvidia-smi``: same data when NVML cannot be loaded.
3. Windows display-adapter registry keys: every adapter incl. AMD/Intel. ``Win32_VideoController``
   is not used for VRAM because its ``AdapterRAM`` is a 32-bit field capped at 4 GB.
4. Linux ``/sys/class/drm``: amdgpu exposes VRAM counters there.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sys
from pathlib import Path

from autodub.core import proc
from autodub.hardware.models import GpuInfo, GpuVendor

log = logging.getLogger(__name__)

_MB = 1024 * 1024
_PCI_VENDORS = {"0x10de": GpuVendor.NVIDIA, "0x1002": GpuVendor.AMD, "0x8086": GpuVendor.INTEL}
_IGNORED_ADAPTERS = re.compile(
    r"basic (display|render)|remote display|virtual|parsec|idd|dummy|mirage|citrix|vmware|hyper-v",
    re.IGNORECASE,
)


def vendor_from_name(name: str) -> GpuVendor:
    n = name.lower()
    if any(k in n for k in ("nvidia", "geforce", "quadro", "tesla", "rtx", "gtx")):
        return GpuVendor.NVIDIA
    if any(k in n for k in ("amd", "radeon", "ati ")):
        return GpuVendor.AMD
    if any(k in n for k in ("intel", "arc ", "iris", "uhd graphics")):
        return GpuVendor.INTEL
    return GpuVendor.OTHER


_INTEGRATED = [
    re.compile(r"intel.*(uhd|hd graphics|iris)", re.I),
    re.compile(r"radeon\(tm\) graphics|radeon graphics$|radeon \d{3}m\b|vega \d+ graphics", re.I),
]


def is_integrated(name: str, vram_mb: int | None) -> bool:
    if any(p.search(name) for p in _INTEGRATED):
        return True
    # Integrated GPUs typically report a tiny dedicated carve-out.
    return vram_mb is not None and vram_mb <= 1024 and vendor_from_name(name) != GpuVendor.NVIDIA


def _cuda_from_int(v: int) -> str:
    return f"{v // 1000}.{(v % 1000) // 10}"


def detect_nvml() -> list[GpuInfo] | None:
    try:
        import pynvml
    except ImportError:
        return None
    try:
        pynvml.nvmlInit()
    except Exception as exc:  # NVMLError_LibraryNotFound, DriverNotLoaded, ...
        log.info("NVML unavailable: %s", exc)
        return None
    try:
        driver = pynvml.nvmlSystemGetDriverVersion()
        driver = driver.decode() if isinstance(driver, bytes) else str(driver)
        try:
            cuda = _cuda_from_int(int(pynvml.nvmlSystemGetCudaDriverVersion_v2()))
        except Exception:
            cuda = None
        gpus = []
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            name = pynvml.nvmlDeviceGetName(h)
            name = name.decode() if isinstance(name, bytes) else str(name)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            try:
                major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(h)
                cc: str | None = f"{major}.{minor}"
            except Exception:
                cc = None
            gpus.append(
                GpuInfo(
                    vendor=GpuVendor.NVIDIA,
                    name=name,
                    vram_total_mb=int(mem.total) // _MB,
                    vram_free_mb=int(mem.free) // _MB,
                    driver_version=driver,
                    cuda_version=cuda,
                    compute_capability=cc,
                    source="nvml",
                )
            )
        return gpus
    except Exception as exc:
        log.warning("NVML query failed: %s", exc)
        return None
    finally:
        with contextlib.suppress(Exception):
            pynvml.nvmlShutdown()


_SMI_CUDA = re.compile(r"CUDA Version:\s*([\d.]+)")


def parse_nvidia_smi(csv_out: str, header_out: str | None) -> list[GpuInfo]:
    cuda = None
    if header_out and (m := _SMI_CUDA.search(header_out)):
        cuda = m.group(1)
    gpus = []
    for line in csv_out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        name, total, free, driver = parts[:4]
        cc = parts[4] if len(parts) > 4 and parts[4] not in ("", "[N/A]") else None
        gpus.append(
            GpuInfo(
                vendor=GpuVendor.NVIDIA,
                name=name,
                vram_total_mb=_int_or_none(total),
                vram_free_mb=_int_or_none(free),
                driver_version=driver or None,
                cuda_version=cuda,
                compute_capability=cc,
                source="nvidia-smi",
            )
        )
    return gpus


def detect_nvidia_smi(runner: proc.Runner = proc.run) -> list[GpuInfo] | None:
    fields = "name,memory.total,memory.free,driver_version"
    res = None
    # Old drivers do not know the compute_cap field and reject the whole query.
    for query in (fields + ",compute_cap", fields):
        res = runner(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"])
        if res is None:
            return None
        if res.ok:
            break
    if res is None or not res.ok:
        return None
    header = runner(["nvidia-smi"])
    return parse_nvidia_smi(res.stdout, header.stdout if header and header.ok else None)


_WIN_ADAPTERS_PS = r"""
$k = 'HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}'
Get-ChildItem $k -ErrorAction SilentlyContinue | Where-Object { $_.PSChildName -match '^\d{4}$' } |
 ForEach-Object { $p = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
  if ($p.DriverDesc) { [pscustomobject]@{
    Name = $p.DriverDesc; Driver = $p.DriverVersion
    Qw = $p.'HardwareInformation.qwMemorySize'; Mem = $p.'HardwareInformation.MemorySize' } } } |
 ConvertTo-Json -Compress
"""

_WIN_VRAM_USAGE_PS = (
    r"(Get-Counter '\GPU Adapter Memory(*)\Dedicated Usage' -ErrorAction SilentlyContinue)"
    r".CounterSamples | Select-Object InstanceName,CookedValue | ConvertTo-Json -Compress"
)


def _mem_value(v: object) -> int | None:
    """Registry memory size: QWORD int, DWORD int or REG_BINARY (list of bytes, little-endian)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v if v > 0 else None
    if isinstance(v, list) and all(isinstance(b, int) for b in v):
        val = int.from_bytes(bytes(b & 0xFF for b in v), "little")
        return val or None
    return None


def parse_windows_adapters(raw_json: str) -> list[GpuInfo]:
    data = json.loads(raw_json) if raw_json.strip() else []
    if isinstance(data, dict):
        data = [data]
    gpus: list[GpuInfo] = []
    seen: set[str] = set()
    for item in data:
        name = str(item.get("Name") or "").strip()
        if not name or _IGNORED_ADAPTERS.search(name) or name in seen:
            continue
        seen.add(name)
        size = _mem_value(item.get("Qw")) or _mem_value(item.get("Mem"))
        vram = size // _MB if size else None
        gpus.append(
            GpuInfo(
                vendor=vendor_from_name(name),
                name=name,
                vram_total_mb=vram,
                driver_version=item.get("Driver"),
                integrated=is_integrated(name, vram),
                source="registry",
            )
        )
    return gpus


def parse_windows_vram_usage(raw_json: str) -> int | None:
    """Largest per-adapter dedicated usage in MB (good enough for a single discrete GPU)."""
    if not raw_json.strip():
        return None
    data = json.loads(raw_json)
    if isinstance(data, dict):
        data = [data]
    values = [float(d.get("CookedValue") or 0) for d in data]
    return int(max(values) // _MB) if values else None


def detect_windows(runner: proc.Runner = proc.run) -> list[GpuInfo]:
    res = proc.powershell(_WIN_ADAPTERS_PS, runner=runner)
    if res is None or not res.ok:
        return []
    try:
        gpus = parse_windows_adapters(res.stdout)
    except (ValueError, TypeError) as exc:
        log.warning("cannot parse adapters: %s", exc)
        return []
    discrete = [g for g in gpus if not g.integrated and g.vendor != GpuVendor.NVIDIA]
    if len(discrete) == 1 and discrete[0].vram_total_mb:
        usage = proc.powershell(_WIN_VRAM_USAGE_PS, runner=runner)
        try:
            used = parse_windows_vram_usage(usage.stdout) if usage and usage.ok else None
        except (ValueError, TypeError):
            used = None
        if used is not None:
            discrete[0].vram_free_mb = max(0, discrete[0].vram_total_mb - used)
    return gpus


def detect_linux_sysfs(root: Path = Path("/sys/class/drm")) -> list[GpuInfo]:
    gpus = []
    for card in sorted(root.glob("card[0-9]")):
        dev = card / "device"
        try:
            vendor = _PCI_VENDORS.get((dev / "vendor").read_text().strip(), GpuVendor.OTHER)
        except OSError:
            continue
        total = _read_int(dev / "mem_info_vram_total")
        used = _read_int(dev / "mem_info_vram_used")
        total_mb = total // _MB if total else None
        name = f"{vendor.value.upper()} GPU ({card.name})"
        gpus.append(
            GpuInfo(
                vendor=vendor,
                name=name,
                vram_total_mb=total_mb,
                vram_free_mb=(total - used) // _MB if total and used is not None else None,
                integrated=vendor == GpuVendor.INTEL or (total_mb is not None and total_mb <= 1024),
                source="sysfs",
            )
        )
    return gpus


def detect_gpus(runner: proc.Runner = proc.run) -> list[GpuInfo]:
    nvidia = detect_nvml() or detect_nvidia_smi(runner) or []
    if sys.platform == "win32":
        others = detect_windows(runner)
    elif sys.platform.startswith("linux"):
        others = detect_linux_sysfs()
    else:
        others = []
    if nvidia:
        others = [g for g in others if g.vendor != GpuVendor.NVIDIA]
    return nvidia + others


def _int_or_none(s: str) -> int | None:
    try:
        return int(float(s))
    except ValueError:
        return None


def _read_int(p: Path) -> int | None:
    try:
        return int(p.read_text().strip())
    except (OSError, ValueError):
        return None
