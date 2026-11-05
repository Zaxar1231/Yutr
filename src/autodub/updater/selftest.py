"""Headless stand-in for the GUI, used by the installer smoke test and the update E2E test.

AUTODUB_SELFTEST_ACTION:
  ok      — report a healthy start and exit;
  update  — healthy start, then check GitHub, download + verify + stage the update, switch;
  crash   — exit with an error before reporting a healthy start (simulates a broken release).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from autodub import __version__
from autodub.core.config import AppConfig, UpdatesConfig
from autodub.updater.service import UpdateService


def main() -> int:
    action = os.environ.get("AUTODUB_SELFTEST_ACTION", "ok")
    report = Path(os.environ.get("AUTODUB_SELFTEST_REPORT", "selftest.json"))
    if action == "crash" and __version__ == os.environ.get("AUTODUB_SELFTEST_CRASH_VERSION", __version__):
        print("simulated crash", file=sys.stderr)
        return 3
    cfg = AppConfig(
        updates=UpdatesConfig(repository=os.environ.get("AUTODUB_SELFTEST_REPO", "o/r"), channel="stable")
    )
    data = Path(os.environ.get("AUTODUB_DATA_DIR", "."))
    svc = UpdateService(cfg, data)
    svc.mark_started()
    result: dict[str, object] = {"version": __version__}
    if action == "update":
        rel = svc.check(force=True)
        result["found"] = str(rel.version) if rel else None
        if rel:
            svc.stage(rel, lambda f: None)
            svc.apply_on_next_start()
            result["staged"] = svc.staged
    report.write_text(json.dumps(result), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
