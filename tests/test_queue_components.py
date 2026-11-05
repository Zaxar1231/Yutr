from pathlib import Path

from autodub.app.queue import JobQueue
from autodub.components import manifest
from autodub.components.fetch import expected_sha256
from autodub.pipeline.context import JobOptions


def test_queue_events_and_persistence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AUTODUB_DEMO_HARDWARE", "1")  # never spawn workers in tests
    changes = []
    q = JobQueue(tmp_path / "q.json", changes.append)
    job = q.add(JobOptions(url="https://example.com/v"))
    assert job.status == "queued" and changes
    final = q.apply_event(job, {"event": "progress", "stage": "tts", "title": "Озвучка", "index": 10,
                                "count": 14, "fraction": 0.5, "overall": 0.7, "eta": 30})
    assert not final and job.stage_title == "Озвучка" and job.eta == 30
    assert q.apply_event(job, {"event": "done", "output": str(tmp_path / "x [RU].mp4")})
    assert job.status == "done" and job.title == "x [RU]"
    q.save()
    q2 = JobQueue(tmp_path / "q.json", lambda j: None)
    assert q2.jobs[0].status == "done"


def test_interrupted_job_is_requeued(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("AUTODUB_DEMO_HARDWARE", raising=False)
    (tmp_path / "q.json").write_text(
        '[{"id": "a", "options": {"url": "u"}, "status": "running"}]', "utf-8")
    q = JobQueue(tmp_path / "q.json", lambda j: None)
    assert q.jobs[0].status == "queued"


def test_checksum_sources(monkeypatch) -> None:
    import autodub.components.fetch as fetch

    pages = {
        "sums": "a" * 64 + "  ffmpeg.zip\n" + "b" * 64 + "  other.zip\n",
        "deno": "\nAlgorithm : SHA256\nHash      : " + "C" * 64 + "\nPath : x\n",
    }
    monkeypatch.setattr(fetch, "get_text", lambda url: pages[url])
    assert expected_sha256("https://x/ffmpeg.zip", checksums_url="sums", pinned=None) == "a" * 64
    assert expected_sha256("https://x/deno.zip", checksums_url="deno", pinned=None) == "c" * 64
    assert expected_sha256("https://x/a.zip", checksums_url=None, pinned="D" * 64) == "d" * 64


def test_manifest_is_complete() -> None:
    for b in manifest.binaries().values():
        assert b.sha256 or b.checksums, b.key  # every download is verified
    for key in ("whisper:large-v3", "whisper:large-v3-turbo", "whisper:medium", "whisper:small",
                "tts:xtts_v2", "tts:f5_tts_ru", "translate:madlad400-3b"):
        assert key in manifest.models()
    assert manifest.models()["tts:xtts_v2"].noncommercial
