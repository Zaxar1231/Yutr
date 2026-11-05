"""Engine worker: runs inside the torch environment, talks JSON lines over stdin/stdout.

Request:  {"id": 1, "task": "separate", "params": {...}}
Progress: {"id": 1, "progress": 0.42, "message": "..."}
Result:   {"id": 1, "result": {...}, "peak_vram_mb": 1234, "peak_ram_mb": 2345}
Error:    {"id": 1, "error": "text", "oom": false, "trace": "..."}

Library output that goes to stdout is redirected to stderr so it cannot corrupt the protocol.
Loaded models are cached between requests until an ``unload`` request (or process exit).
"""

from __future__ import annotations

import gc
import importlib
import json
import os
import sys
import traceback
from typing import Any

TASKS = {
    "separate": "autodub.engines.separate:run",
    "align": "autodub.engines.align:run",
    "diarize": "autodub.engines.diarize:run",
    "tts_xtts": "autodub.engines.tts_xtts:run",
    "tts_f5": "autodub.engines.tts_f5:run",
    "similarity": "autodub.engines.similarity:run",
    "ping": "autodub.engines.worker:ping",
}

CACHE: dict[str, Any] = {}


def ping(params: dict[str, Any], progress: Any) -> dict[str, Any]:
    info: dict[str, Any] = {"python": sys.version.split()[0]}
    try:
        import torch

        info["torch"] = torch.__version__
        info["cuda"] = bool(torch.cuda.is_available())
        if info["cuda"]:
            info["device"] = torch.cuda.get_device_name(0)
    except Exception as exc:  # pragma: no cover - environment specific
        info["torch_error"] = str(exc)
    return info


def is_oom(exc: BaseException) -> bool:
    if isinstance(exc, MemoryError) or type(exc).__name__ == "OutOfMemoryError":
        return True
    text = str(exc).lower()
    return "out of memory" in text or "alloc_failed" in text or "hiperroroutofmemory" in text


def unload() -> None:
    CACHE.clear()
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        torch.cuda.empty_cache()


def _peaks() -> dict[str, int]:
    out: dict[str, int] = {}
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_available():
        out["peak_vram_mb"] = int(torch.cuda.max_memory_allocated() // 2**20)
        torch.cuda.reset_peak_memory_stats()
    try:
        import resource

        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        out["peak_ram_mb"] = int(kb // 1024)
    except ImportError:  # Windows
        try:
            import psutil

            info = psutil.Process().memory_info()
            out["peak_ram_mb"] = int(getattr(info, "peak_wset", info.rss) // 2**20)
        except Exception:
            pass
    return out


def main() -> None:
    proto = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def send(obj: dict[str, Any]) -> None:
        proto.write(json.dumps(obj, ensure_ascii=False) + "\n")
        proto.flush()

    send({"ready": True, "pid": os.getpid()})
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        rid = req.get("id")
        task = req.get("task")
        if task == "unload":
            unload()
            send({"id": rid, "result": {}})
            continue
        if task == "exit":
            send({"id": rid, "result": {}})
            break

        def progress(fraction: float, message: str = "", _rid: Any = rid) -> None:
            send({"id": _rid, "progress": round(float(fraction), 4), "message": message})

        try:
            target = TASKS[task]
            mod_name, func_name = target.split(":")
            func = getattr(importlib.import_module(mod_name), func_name)
            result = func(req.get("params") or {}, progress)
            send({"id": rid, "result": result, **_peaks()})
        except BaseException as exc:  # report everything, never die silently
            oom = is_oom(exc)
            if oom:
                unload()
            send(
                {
                    "id": rid,
                    "error": f"{type(exc).__name__}: {exc}",
                    "oom": oom,
                    "trace": traceback.format_exc(limit=20),
                }
            )
            if isinstance(exc, KeyboardInterrupt):
                break


if __name__ == "__main__":
    main()
