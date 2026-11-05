"""Builds the pipeline Runtime from the installed components (or env overrides for dev/CI)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from autodub.core.paths import AppPaths
from autodub.core.secrets import get_secret
from autodub.pipeline.context import Runtime
from autodub.pipeline.media import Tools

EXE = ".exe" if sys.platform == "win32" else ""


def find_tool(paths: AppPaths, name: str) -> str | None:
    local = paths.bin / f"{name}{EXE}"
    if local.exists():
        return str(local)
    return shutil.which(name)


def engine_python(paths: AppPaths, env_name: str = "torch-main") -> str | None:
    if os.environ.get("AUTODUB_ENGINE_PYTHON"):
        return os.environ["AUTODUB_ENGINE_PYTHON"]
    venv = paths.envs / env_name / ".venv"
    py = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    return str(py) if py.exists() else None


def build_runtime(paths: AppPaths) -> Runtime:
    models = Path(os.environ.get("AUTODUB_MODELS_DIR") or paths.models)
    ffmpeg = find_tool(paths, "ffmpeg") or "ffmpeg"
    ffprobe = find_tool(paths, "ffprobe") or "ffprobe"
    path_extra = str(paths.bin)
    if path_extra not in os.environ.get("PATH", ""):
        # yt-dlp finds deno / ffmpeg in our bin folder through PATH.
        os.environ["PATH"] = path_extra + os.pathsep + os.environ.get("PATH", "")
    return Runtime(
        tools=Tools(ffmpeg, ffprobe),
        # Standalone yt-dlp (self-updating, bundles EJS) when installed; module otherwise.
        ytdlp=[ytdlp_exe] if (ytdlp_exe := find_tool(paths, "yt-dlp")) else [sys.executable, "-m", "yt_dlp"],
        engine_python=engine_python(paths),
        models_dir=models,
        cache_dir=paths.cache,
        aria2c=find_tool(paths, "aria2c"),
        secrets=get_secret,
    )
