"""Runs the stages of one job: cache/resume, OOM fallback, progress with ETA."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autodub.hardware.guard import run_with_fallback
from autodub.hardware.profiles import ProfileSelection, Tier
from autodub.pipeline.context import JobContext
from autodub.pipeline.models import LanguageVerdict
from autodub.pipeline.stages import STAGES, AlreadyRussian, Stage

log = logging.getLogger(__name__)


@dataclass
class Progress:
    stage: str
    stage_title: str
    stage_index: int
    stage_count: int
    stage_fraction: float
    overall: float
    message: str
    eta_seconds: float | None


@dataclass
class JobResult:
    status: str  # done | already_russian
    output: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _snapshot(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


class Runner:
    def __init__(
        self,
        ctx: JobContext,
        reselect: Callable[[Tier], ProfileSelection],
        on_progress: Callable[[Progress], None] | None = None,
        stages: list[Stage] | None = None,
    ):
        self.ctx = ctx
        self.reselect = reselect
        self.on_progress = on_progress or (lambda p: None)
        self.stages = stages or STAGES
        self.total_weight = sum(s.weight for s in self.stages)
        self._started = time.monotonic()
        self._done_weight = 0.0
        self._skipped_weight = 0.0

    def _emit(self, idx: int, st: Stage, frac: float, msg: str) -> None:
        overall = (self._done_weight + st.weight * frac) / self.total_weight
        worked = (self._done_weight - self._skipped_weight + st.weight * frac) / self.total_weight
        elapsed = time.monotonic() - self._started
        eta = None
        if worked > 0.02 and elapsed > 5:
            eta = elapsed / worked * (1 - overall)
        self.on_progress(Progress(st.name, st.title, idx, len(self.stages), frac, overall, msg, eta))

    def _stage_progress(self, idx: int, st: Stage) -> Callable[[str, float, str], None]:
        def report(_stage: str, frac: float, msg: str) -> None:
            self._emit(idx, st, frac, msg)

        return report

    def run(self) -> JobResult:
        ctx = self.ctx
        rerun = False
        for idx, st in enumerate(self.stages):
            ctx.current_stage = st.name
            ctx._progress = self._stage_progress(idx, st)
            key = st.key(ctx)
            if not rerun and ctx.cached(st.name, key) is not None:
                log.info("stage %s: cached", st.name)
                self._done_weight += st.weight
                self._skipped_weight += st.weight
                self._emit(idx, st, 1.0, "Готово (из кэша)")
                self._after(st)
                continue
            rerun = True  # every later stage depends on this one
            ctx.checkpoint()
            before = _snapshot(ctx.workdir)
            t0 = time.monotonic()
            self._emit(idx, st, 0.0, st.title)
            if st.heavy:

                def attempt(sel: ProfileSelection, _st: Stage = st) -> dict[str, Any]:
                    ctx.selection = sel
                    return _st.run(ctx)

                result, ctx.selection = run_with_fallback(
                    st.name, attempt, ctx.selection, self.reselect, notify=ctx.notify
                )
            else:
                result = st.run(ctx)
            files = sorted(_snapshot(ctx.workdir) - before - {"state.json", "state.tmp"})
            ctx.store(st.name, key, result, files)
            log.info("stage %s done in %.1fs", st.name, time.monotonic() - t0)
            self._done_weight += st.weight
            self._emit(idx, st, 1.0, "Готово")
            self._after(st)
        mux = ctx.result("mux")
        return JobResult("done", mux["output"], {"fit": ctx.result("fit"), **mux})

    def _after(self, st: Stage) -> None:
        if st.name == "langid":
            verdict = LanguageVerdict.model_validate(self.ctx.result("langid"))
            if verdict.skip_translation and not self.ctx.options.force_translate:
                raise AlreadyRussian(verdict)
            if verdict.mixed:
                self.ctx.notify("В видео несколько языков: русские фрагменты останутся в оригинале.")
