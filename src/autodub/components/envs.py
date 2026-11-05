"""Heavy dependency layer: the torch engine environment, built with uv.

The environment is described by a small manifest (below). It is rebuilt only when the
manifest or the chosen accelerator changes, so app updates stay a few megabytes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from autodub.hardware.models import Accelerator, HardwareReport

log = logging.getLogger(__name__)
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
ENV_NAME = "torch-main"

PACKAGES = [
    "torch==2.8.*",
    "torchaudio==2.8.*",
    "torchvision==0.23.*",
    "whisperx==3.8.6",
    "demucs==4.1.0",
    "coqui-tts==0.28.0",
    "f5-tts==1.1.22",
    "pyannote.audio==4.0.*",
    "soundfile",
    "num2words",
    "psutil",
]
# Packages that need wheels matching the accelerator.
TORCH_PACKAGES = ("torch", "torchaudio", "torchvision")


def torch_index(report: HardwareReport | None, accelerator: Accelerator) -> str:
    """PyTorch wheel index for this machine (torch 2.8 has cu126/cu128/cu129 and cpu)."""
    if accelerator == Accelerator.CUDA and report:
        gpu = report.primary_gpu()
        cuda = tuple(int(x) for x in (gpu.cuda_version or "0.0").split(".")[:2]) if gpu else (0, 0)
        if cuda >= (12, 8):
            return "https://download.pytorch.org/whl/cu128"
        if cuda >= (12, 6):
            return "https://download.pytorch.org/whl/cu126"
    if accelerator == Accelerator.ROCM:
        # AMD publishes Windows ROCm wheels for its own torch builds; experimental.
        return "https://repo.radeon.com/rocm/windows/pytorch/"
    return "https://download.pytorch.org/whl/cpu"


def pyproject(index: str) -> str:
    deps = ",\n".join(f'  "{p}"' for p in PACKAGES)
    sources = "\n".join(f'{p} = {{ index = "torch" }}' for p in TORCH_PACKAGES)
    return f"""[project]
name = "autodub-engine-{ENV_NAME}"
version = "0"
requires-python = ">=3.12,<3.13"
dependencies = [
{deps}
]

[tool.uv]
environments = ["sys_platform == 'win32' and platform_machine == 'AMD64'",
                "sys_platform == 'linux' and platform_machine == 'x86_64'"]

[tool.uv.sources]
{sources}

[[tool.uv.index]]
name = "torch"
url = "{index}"
explicit = true
"""


def manifest_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def env_dir(envs_root: Path) -> Path:
    return envs_root / ENV_NAME


def python_path(envs_root: Path) -> Path:
    venv = env_dir(envs_root) / ".venv"
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def is_current(envs_root: Path, index: str) -> bool:
    stamp = env_dir(envs_root) / "installed.json"
    if not stamp.exists() or not python_path(envs_root).exists():
        return False
    return bool(json.loads(stamp.read_text("utf-8")).get("hash") == manifest_hash(pyproject(index)))


def build(envs_root: Path, index: str, *, uv: str, progress: Callable[[float, str], None]) -> Path:
    """Create or update the environment. Downloads are resumed by uv's cache on retry."""
    d = env_dir(envs_root)
    d.mkdir(parents=True, exist_ok=True)
    text = pyproject(index)
    (d / "pyproject.toml").write_text(text, "utf-8")
    steps = [
        ([uv, "python", "install", "3.12"], "Устанавливаю Python 3.12", 0.05),
        (
            [uv, "sync", "--python", "3.12", "--directory", str(d)],
            "Скачиваю PyTorch и модели обработки",
            0.95,
        ),
    ]
    for cmd, title, frac in steps:
        progress(frac * 0.1, title)
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
        assert proc.stdout
        for seen, line in enumerate(proc.stdout, 1):
            log.info("uv: %s", line.rstrip())
            if "Downloaded" in line or "Prepared" in line:
                progress(min(frac, 0.1 + seen / 400), line.strip()[:80])
        if proc.wait() != 0:
            raise RuntimeError(f"uv failed: {' '.join(cmd)}")
    (d / "installed.json").write_text(json.dumps({"hash": manifest_hash(text), "index": index}), "utf-8")
    progress(1.0, "Готово")
    return python_path(envs_root)
