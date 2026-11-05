"""Thin, injectable wrapper around subprocess so detection code can be unit-tested."""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

# Do not flash console windows when the GUI calls helpers on Windows.
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


@dataclass(frozen=True)
class CmdResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class Runner(Protocol):
    def __call__(self, args: Sequence[str], *, timeout: float = 15.0) -> CmdResult | None: ...


def run(args: Sequence[str], *, timeout: float = 15.0) -> CmdResult | None:
    """Run a command; return ``None`` if it is missing or timed out (never raises)."""
    try:
        cp = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
    except FileNotFoundError:
        return None
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("command %s failed: %s", args[0], exc)
        return None
    return CmdResult(cp.returncode, cp.stdout, cp.stderr)


def powershell(script: str, *, runner: Runner = run, timeout: float = 20.0) -> CmdResult | None:
    return runner(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout
    )
