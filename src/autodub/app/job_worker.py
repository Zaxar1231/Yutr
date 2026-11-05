"""Runs one dubbing job in its own process so the UI never freezes.

stdin  (commands, one per line): pause | resume | cancel
stdout (events, JSON lines): progress / notice / done / already_russian / error / cancelled
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from typing import Any

from autodub.core.config import load_config
from autodub.core.errors import UserFacingError
from autodub.core.logs import setup_logging
from autodub.core.paths import resolve_paths
from autodub.hardware.guard import OutOfMemoryAtLowestProfile, ResourceGuard
from autodub.hardware.service import HardwareService
from autodub.pipeline.context import Cancelled, Control, JobContext, JobOptions
from autodub.pipeline.runner import Progress, Runner
from autodub.pipeline.stages import AlreadyRussian
from autodub.runtime import build_runtime

log = logging.getLogger("autodub.job")


def main() -> None:
    options = JobOptions.model_validate_json(sys.argv[1])
    out = sys.stdout
    lock = threading.Lock()

    def send(kind: str, **data: Any) -> None:
        with lock:
            out.write(json.dumps({"event": kind, **data}, ensure_ascii=False) + "\n")
            out.flush()

    paths = resolve_paths().ensure()
    setup_logging(paths.logs, console=False)
    cfg = load_config(paths.config_file)
    hw = HardwareService(paths=paths, config=cfg)
    if hw.rescan_reason():
        hw.scan()
    control = Control()

    def commands() -> None:
        for line in sys.stdin:
            cmd = line.strip()
            if cmd == "pause":
                control.pause()
            elif cmd == "resume":
                control.unpause()
            elif cmd == "cancel":
                control.cancel.set()
                control.unpause()

    threading.Thread(target=commands, daemon=True).start()

    def on_progress(p: Progress) -> None:
        send(
            "progress",
            stage=p.stage,
            title=p.stage_title,
            index=p.stage_index,
            count=p.stage_count,
            fraction=p.stage_fraction,
            overall=p.overall,
            message=p.message,
            eta=p.eta_seconds,
        )

    guard = ResourceGuard(learned_peaks=hw.state.learned_peaks)
    ctx = JobContext(
        options,
        build_runtime(paths),
        hw.selection(),
        guard=guard,
        notify=lambda m: send("notice", text=m),
        control=control,
    )
    try:
        result = Runner(ctx, hw.reselect, on_progress).run()
        send(
            "done",
            output=result.output,
            details=result.details,
            source=str(ctx.workdir / ctx.result("download")["video"]),
        )
    except AlreadyRussian as ar:
        send("already_russian", confidence=ar.verdict.confidence)
    except Cancelled:
        send("cancelled")
    except (UserFacingError, OutOfMemoryAtLowestProfile) as e:
        log.error("job failed: %s | %s", e, getattr(e, "detail", ""))
        send("error", text=str(e))
    except Exception:
        log.exception("unexpected job failure")
        send(
            "error",
            text="Непредвиденная ошибка. Подробности записаны в журнал — "
            "задачу можно продолжить с того же места.",
        )
    finally:
        hw.record_peaks(guard.learned_peaks)


if __name__ == "__main__":
    main()
