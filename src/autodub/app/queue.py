"""Job queue of the GUI: one job at a time, each in its own worker process.

The queue is saved to disk, so after a crash or restart unfinished jobs are still there and
continue from their last finished stage (the pipeline caches every stage).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from autodub.pipeline.context import JobOptions

log = logging.getLogger(__name__)
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass
class Job:
    id: str
    options: dict[str, Any]
    status: str = "queued"  # queued|running|paused|done|error|cancelled|already_russian
    title: str = ""
    stage: str = ""
    stage_title: str = ""
    stage_index: int = 0
    stage_count: int = 14
    fraction: float = 0.0
    overall: float = 0.0
    eta: float | None = None
    message: str = ""
    output: str | None = None
    source: str | None = None
    error: str | None = None
    notices: list[str] = field(default_factory=list)
    created: float = field(default_factory=time.time)

    @property
    def url(self) -> str:
        return str(self.options["url"])


class JobQueue:
    def __init__(self, state_file: Path, on_change: Callable[[Job], None], python: str = sys.executable):
        self.state_file = state_file
        self.on_change = on_change
        self.python = python
        self.jobs: list[Job] = []
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._load()

    # --- persistence ----------------------------------------------------------------------

    def _load(self) -> None:
        if self.state_file.exists():
            try:
                raw = json.loads(self.state_file.read_text("utf-8"))
                self.jobs = [Job(**j) for j in raw]
            except (ValueError, TypeError):
                log.warning("queue file unreadable, starting empty")
        if os.environ.get("AUTODUB_DEMO_HARDWARE"):
            return  # screenshots show the queue exactly as prepared
        for j in self.jobs:  # a job that was running when the app closed resumes later
            if j.status in ("running", "paused"):
                j.status = "queued"

    def save(self) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(j) for j in self.jobs], ensure_ascii=False), "utf-8")
        tmp.replace(self.state_file)

    # --- API ------------------------------------------------------------------------------

    def add(self, options: JobOptions) -> Job:
        job = Job(id=uuid.uuid4().hex[:8], options=options.model_dump(mode="json"), title=options.url)
        with self._lock:
            self.jobs.append(job)
            self.save()
        self.on_change(job)
        if os.environ.get("AUTODUB_DEMO_SIMULATE"):
            threading.Thread(target=self._simulate, args=(job,), daemon=True).start()
        else:
            self.kick()
        return job

    def _simulate(self, job: Job) -> None:
        """Screenshots/GIF only: walk the job through the real stage list without doing work."""
        from autodub.pipeline.stages import STAGES

        job.status = "running"
        total = sum(s.weight for s in STAGES)
        done = 0.0
        speed = float(os.environ.get("AUTODUB_DEMO_SIMULATE", "8"))  # seconds for the whole job
        for i, st in enumerate(STAGES):
            steps = max(2, int(st.weight / total * speed * 4))
            for k in range(1, steps + 1):
                frac = k / steps
                remaining = speed * (1 - (done + st.weight * frac) / total) * 40
                self.apply_event(
                    job,
                    {
                        "event": "progress",
                        "stage": st.name,
                        "title": st.title,
                        "index": i,
                        "count": len(STAGES),
                        "fraction": frac,
                        "overall": (done + st.weight * frac) / total,
                        "eta": remaining,
                    },
                )
                time.sleep(0.25)
            done += st.weight
        output = os.environ.get("AUTODUB_DEMO_SIM_OUTPUT", "demo [RU].mp4")
        self.apply_event(
            job, {"event": "done", "output": output, "source": os.environ.get("AUTODUB_DEMO_SIM_SOURCE")}
        )

    def active(self) -> Job | None:
        return next((j for j in self.jobs if j.status in ("running", "paused")), None)

    def busy(self) -> bool:
        return any(j.status in ("running", "paused", "queued") for j in self.jobs)

    def kick(self) -> None:
        if os.environ.get("AUTODUB_DEMO_HARDWARE"):  # screenshots: show the queue, run nothing
            return
        with self._lock:
            if self.active():
                return
            nxt = next((j for j in self.jobs if j.status == "queued"), None)
            if nxt:
                self._start(nxt)

    def _send(self, cmd: str) -> None:
        if self._proc and self._proc.poll() is None and self._proc.stdin:
            self._proc.stdin.write(cmd + "\n")
            self._proc.stdin.flush()

    def pause(self, job: Job) -> None:
        if job.status == "running":
            self._send("pause")
            job.status = "paused"
            self.on_change(job)

    def resume(self, job: Job) -> None:
        if job.status == "paused":
            self._send("resume")
            job.status = "running"
            self.on_change(job)
        elif job.status in ("error", "cancelled"):
            job.status, job.error = "queued", None
            self.save()
            self.on_change(job)
            self.kick()

    def cancel(self, job: Job) -> None:
        if job.status in ("running", "paused"):
            self._send("cancel")
        elif job.status == "queued":
            job.status = "cancelled"
            self.save()
            self.on_change(job)

    def remove(self, job: Job) -> None:
        if job.status in ("running", "paused"):
            return
        with self._lock:
            self.jobs = [j for j in self.jobs if j.id != job.id]
            self.save()

    # --- worker ---------------------------------------------------------------------------

    def _start(self, job: Job) -> None:
        job.status, job.error = "running", None
        self.save()
        self.on_change(job)
        self._proc = subprocess.Popen(
            [self.python, "-m", "autodub.app.job_worker", json.dumps(job.options)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            creationflags=_CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._pump, args=(job, self._proc), daemon=True).start()

    def _pump(self, job: Job, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout
        final = False
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            final = self.apply_event(job, ev) or final
        proc.wait()
        if not final:
            job.status = "error"
            job.error = "Обработка неожиданно завершилась. Задачу можно продолжить."
        self.save()
        self.on_change(job)
        self.kick()

    def apply_event(self, job: Job, ev: dict[str, Any]) -> bool:
        """Update the job from a worker event. Returns True for a final event."""
        kind = ev.get("event")
        if kind == "progress":
            job.stage, job.stage_title = ev["stage"], ev["title"]
            job.stage_index, job.stage_count = ev["index"], ev["count"]
            job.fraction, job.overall = ev["fraction"], ev["overall"]
            job.eta, job.message = ev.get("eta"), ev.get("message", "")
        elif kind == "notice":
            job.notices.append(ev["text"])
        elif kind == "done":
            job.status, job.output, job.source = "done", ev["output"], ev.get("source")
            job.overall = 1.0
            job.title = Path(ev["output"]).stem
        elif kind == "already_russian":
            job.status = "already_russian"
        elif kind == "cancelled":
            job.status = "cancelled"
        elif kind == "error":
            job.status, job.error = "error", ev["text"]
        self.on_change(job)
        return kind in ("done", "already_russian", "cancelled", "error")
