"""Logging to a rotating file plus stderr."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(log_dir: Path, *, level: int = logging.INFO, console: bool = True) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = RotatingFileHandler(
        log_dir / "autodub.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(fh)
    if console:
        ch = logging.StreamHandler()
        ch.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        ch.setLevel(logging.WARNING)
        root.addHandler(ch)
