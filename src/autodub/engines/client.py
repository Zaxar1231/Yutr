"""App-side handle to an engine worker process."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SRC_ROOT = Path(__file__).resolve().parents[2]  # .../src
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

ProgressFn = Callable[[float, str], None]


class EngineError(RuntimeError):
    def __init__(self, message: str, *, oom: bool = False, trace: str = ""):
        super().__init__(message)
        self.oom = oom
        self.trace = trace


class EngineCancelled(RuntimeError):
    pass


class EngineProcess:
    """One long-lived worker. Use as a context manager; ``close()`` frees all its memory."""

    def __init__(self, python: str, *, env: dict[str, str] | None = None, cwd: Path | None = None):
        self.python = python
        self._env = env or {}
        self._cwd = cwd
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 0
        self._lock = threading.Lock()
        self.last_peaks: dict[str, int] = {}

    def start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        env = os.environ | self._env
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC_ROOT), env.get("PYTHONPATH")]))
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUNBUFFERED", "1")
        log.info("starting engine worker: %s", self.python)
        self._proc = subprocess.Popen(
            [self.python, "-u", "-m", "autodub.engines.worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,  # inherits: goes to the log / console
            text=True,
            encoding="utf-8",
            env=env,
            cwd=self._cwd,
            creationflags=_CREATE_NO_WINDOW,
        )
        hello = self._read()
        if not hello.get("ready"):
            raise EngineError(f"engine did not start: {hello}")

    def _read(self) -> dict[str, Any]:
        assert self._proc and self._proc.stdout
        line = self._proc.stdout.readline()
        if not line:
            code = self._proc.wait()
            # Exit codes of a hard crash on OOM (Windows 0xC0000409 etc.) are not reliable;
            # treat a vanished worker as a possible OOM so the caller can try a lower profile.
            raise EngineError(f"engine process exited with code {code}", oom=True)
        return dict(json.loads(line))

    def call(
        self,
        task: str,
        params: dict[str, Any],
        *,
        progress: ProgressFn | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self.start()
            assert self._proc and self._proc.stdin
            self._next_id += 1
            rid = self._next_id
            self._proc.stdin.write(json.dumps({"id": rid, "task": task, "params": params}) + "\n")
            self._proc.stdin.flush()
            while True:
                if cancelled and cancelled():
                    self.kill()
                    raise EngineCancelled(task)
                msg = self._read()
                if msg.get("id") != rid:
                    continue
                if "progress" in msg:
                    if progress:
                        progress(float(msg["progress"]), str(msg.get("message", "")))
                    continue
                if "error" in msg:
                    log.error("engine task %s failed: %s\n%s", task, msg["error"], msg.get("trace"))
                    raise EngineError(str(msg["error"]), oom=bool(msg.get("oom")), trace=msg.get("trace", ""))
                self.last_peaks = {k: int(v) for k, v in msg.items() if k.startswith("peak_")}
                return dict(msg.get("result") or {})

    def unload(self) -> None:
        if self._proc and self._proc.poll() is None:
            self.call("unload", {})

    def kill(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait(10)
        self._proc = None

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                assert self._proc.stdin
                self._proc.stdin.write(json.dumps({"id": 0, "task": "exit"}) + "\n")
                self._proc.stdin.flush()
                self._proc.wait(15)
            except Exception:
                self.kill()
        self._proc = None

    def __enter__(self) -> EngineProcess:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
