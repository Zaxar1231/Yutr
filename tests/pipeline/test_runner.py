from pathlib import Path

import pytest

from autodub.hardware.profiles import Tier, select_profile
from autodub.pipeline.context import Cancelled, JobContext, JobOptions, Runtime
from autodub.pipeline.media import Tools
from autodub.pipeline.runner import Runner
from autodub.pipeline.stages import Stage

from ..hardware.conftest import make_report, nvidia


def make_ctx(tmp_path: Path, report=None) -> JobContext:
    rt = Runtime(tools=Tools(), ytdlp=[], engine_python=None, models_dir=tmp_path, cache_dir=tmp_path)
    return JobContext(JobOptions(url="u"), rt, select_profile(report or make_report([])))


def stages(log: list[str], fail_once: dict[str, Exception] | None = None) -> list[Stage]:
    fail_once = fail_once if fail_once is not None else {}

    def mk(name: str, heavy: bool = False) -> Stage:
        def run(ctx: JobContext) -> dict:
            log.append(f"{name}:{ctx.selection.tier.value}")
            ctx.progress(0.5, "half")
            if name in fail_once:
                raise fail_once.pop(name)
            ctx.path(f"{name}.out").write_text("x")
            return {"v": name}

        return Stage(name, name.title(), 1.0, run, lambda c: {}, heavy=heavy)

    s = [mk("download"), mk("asr", heavy=True), mk("mux")]
    s[-1] = Stage("mux", "Mux", 1.0, lambda c: (log.append("mux"), {"output": "o.mp4"})[1], lambda c: {})
    return s


def test_progress_is_monotonic_and_bounded(tmp_path: Path) -> None:
    seen: list[float] = []
    Runner(make_ctx(tmp_path), lambda t: None, lambda p: seen.append(p.overall), stages([])).run()
    assert all(0 <= x <= 1 for x in seen)
    assert seen == sorted(seen) and seen[-1] == 1.0


def test_resume_skips_finished_stages(tmp_path: Path) -> None:
    log: list[str] = []
    boom = {"asr": RuntimeError("crash")}
    with pytest.raises(RuntimeError):
        Runner(make_ctx(tmp_path), lambda t: None, None, stages(log, boom)).run()
    log.clear()
    Runner(make_ctx(tmp_path), lambda t: None, None, stages(log)).run()
    assert log == ["asr:cpu", "mux"]  # download came from the cache


def test_oom_falls_back_to_lower_profile_and_continues(tmp_path: Path) -> None:
    report = make_report([nvidia(16384)])
    log: list[str] = []
    notes: list[str] = []
    ctx = make_ctx(tmp_path, report)
    ctx.notify = notes.append
    res = Runner(
        ctx,
        lambda t: select_profile(report, forced_tier=t),
        None,
        stages(log, {"asr": RuntimeError("CUDA out of memory")}),
    ).run()
    assert res.status == "done"
    assert log == ["download:ultra", "asr:ultra", "asr:high", "mux"]
    assert ctx.selection.tier == Tier.HIGH and notes


def test_cancel(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    ctx.control.cancel.set()
    with pytest.raises(Cancelled):
        Runner(ctx, lambda t: None, None, stages([])).run()
