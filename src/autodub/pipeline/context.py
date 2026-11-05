"""Job options, runtime environment and the per-job context with a resumable stage cache."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autodub.hardware.guard import ResourceGuard
from autodub.hardware.profiles import ProfileSelection, ProfileSettings
from autodub.pipeline.media import Tools
from autodub.pipeline.models import MixMode, Quality

log = logging.getLogger(__name__)


class JobOptions(BaseModel):
    url: str
    quality: Quality = Quality.BEST
    mode: MixMode = MixMode.DUB
    original_volume: float = Field(default=0.2, ge=0, le=1)  # voice-over mode only
    translator: str = "claude"  # claude | madlad
    claude_model: str = "claude-opus-5-5"
    tts_engine: str | None = None  # None = from profile
    diarize: bool = False
    num_speakers: int | None = None
    cookies_from_browser: str | None = None
    section: tuple[float, float] | None = None  # only this part of the video (seconds)
    output_dir: str = "."
    force_translate: bool = False  # translate even if the video is Russian
    skip_threshold: float = 0.8
    prosody_from_phrase: bool = True

    def job_id(self) -> str:
        raw = f"{self.url}|{self.section}"
        return hashlib.sha1(raw.encode()).hexdigest()[:12]


@dataclass
class Runtime:
    """Where the external tools and models are. Filled by the component manager."""

    tools: Tools
    ytdlp: list[str]  # command prefix, e.g. [python, "-m", "yt_dlp"]
    engine_python: str | None
    models_dir: Path
    cache_dir: Path
    aria2c: str | None = None
    secrets: Callable[[str], str | None] = lambda _name: None


ProgressFn = Callable[[str, float, str], None]  # stage, fraction, message


class Cancelled(Exception):
    pass


@dataclass
class Control:
    """Pause / cancel from the UI. Stages call ``ctx.checkpoint()`` regularly."""

    cancel: threading.Event = field(default_factory=threading.Event)
    resume: threading.Event = field(default_factory=lambda: _set_event())

    def pause(self) -> None:
        self.resume.clear()

    def unpause(self) -> None:
        self.resume.set()


def _set_event() -> threading.Event:
    e = threading.Event()
    e.set()
    return e


class JobContext:
    def __init__(
        self,
        options: JobOptions,
        runtime: Runtime,
        selection: ProfileSelection,
        *,
        guard: ResourceGuard | None = None,
        progress: ProgressFn | None = None,
        notify: Callable[[str], None] | None = None,
        control: Control | None = None,
    ):
        self.options = options
        self.rt = runtime
        self.selection = selection
        self.guard = guard or ResourceGuard()
        self._progress = progress or (lambda s, f, m: None)
        self.notify = notify or (lambda m: log.info("notice: %s", m))
        self.control = control or Control()
        self.workdir = runtime.cache_dir / "jobs" / options.job_id()
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.workdir / "state.json"
        self.state: dict[str, Any] = (
            json.loads(self.state_file.read_text("utf-8")) if self.state_file.exists() else {}
        )
        self.current_stage = ""

    # --- paths ----------------------------------------------------------------------------

    def path(self, name: str) -> Path:
        return self.workdir / name

    @property
    def settings(self) -> ProfileSettings:
        return self.selection.settings

    # --- progress / control ---------------------------------------------------------------

    def progress(self, fraction: float, message: str = "") -> None:
        self.checkpoint()
        self._progress(self.current_stage, max(0.0, min(1.0, fraction)), message)

    def checkpoint(self) -> None:
        if self.control.cancel.is_set():
            raise Cancelled()
        if not self.control.resume.is_set():
            self.control.resume.wait()
            if self.control.cancel.is_set():
                raise Cancelled()

    def cancelled(self) -> bool:
        return self.control.cancel.is_set()

    # --- stage cache ----------------------------------------------------------------------

    @staticmethod
    def _norm(key: dict[str, Any]) -> Any:
        # Compare keys as they look after a JSON round trip (tuples become lists etc.).
        return json.loads(json.dumps(key, sort_keys=True, default=str))

    def cached(self, stage: str, key: dict[str, Any]) -> dict[str, Any] | None:
        entry = self.state.get(stage)
        if not entry or entry.get("key") != self._norm(key):
            return None
        files = entry.get("files", [])
        if not all((self.workdir / f).exists() for f in files):
            return None
        return dict(entry["result"])

    def store(self, stage: str, key: dict[str, Any], result: dict[str, Any], files: list[str]) -> None:
        self.state[stage] = {"key": self._norm(key), "result": result, "files": files}
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), "utf-8")
        tmp.replace(self.state_file)

    def result(self, stage: str) -> dict[str, Any]:
        return dict(self.state[stage]["result"])

    def invalidate_from(self, stages: list[str]) -> None:
        for s in stages:
            self.state.pop(s, None)
