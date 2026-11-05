from pathlib import Path

from autodub.hardware.profiles import select_profile
from autodub.pipeline.context import JobContext, JobOptions, Runtime
from autodub.pipeline.media import Tools

from ..hardware.conftest import make_report


def ctx(tmp_path: Path) -> JobContext:
    rt = Runtime(tools=Tools(), ytdlp=[], engine_python=None, models_dir=tmp_path, cache_dir=tmp_path)
    return JobContext(JobOptions(url="x", section=(1.0, 2.0)), rt, select_profile(make_report([])))


def test_cache_survives_restart_with_tuple_keys(tmp_path: Path) -> None:
    c = ctx(tmp_path)
    (c.workdir / "a.wav").write_text("x")
    key = {"sec": c.options.section}
    c.store("download", key, {"video": "a.wav"}, ["a.wav"])
    c2 = ctx(tmp_path)  # new process
    assert c2.cached("download", key) == {"video": "a.wav"}
    assert c2.cached("download", {"sec": (1.0, 3.0)}) is None
    (c2.workdir / "a.wav").unlink()
    assert c2.cached("download", key) is None  # output file gone -> recompute
