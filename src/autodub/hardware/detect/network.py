"""Quick download-speed estimate used to pick yt-dlp/aria2c parallelism.

Downloads from a configurable list of public speed-test endpoints for at most a few seconds.
A failure is not an error: the selector then uses a safe default.
"""

from __future__ import annotations

import logging
import time
import urllib.request
from collections.abc import Sequence

from autodub import __version__
from autodub.hardware.models import NetworkInfo

log = logging.getLogger(__name__)

DEFAULT_ENDPOINTS: tuple[str, ...] = (
    "https://speed.cloudflare.com/__down?bytes=50000000",
    "https://proof.ovh.net/files/100Mb.dat",
)


def measure(
    endpoints: Sequence[str] = DEFAULT_ENDPOINTS,
    *,
    max_seconds: float = 4.0,
    warmup_seconds: float = 0.5,
    opener: urllib.request.OpenerDirector | None = None,
) -> NetworkInfo:
    op = opener or urllib.request.build_opener()
    for url in endpoints:
        req = urllib.request.Request(url, headers={"User-Agent": f"Autodub/{__version__}"})
        try:
            with op.open(req, timeout=5) as resp:
                start = time.perf_counter()
                counted_from = None
                got = 0
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    now = time.perf_counter()
                    # Ignore TCP slow start: count bytes only after the warm-up.
                    if counted_from is None and now - start >= warmup_seconds:
                        counted_from = now
                    if counted_from is not None:
                        got += len(chunk)
                    if now - start >= max_seconds:
                        break
                elapsed = time.perf_counter() - (counted_from or start)
                if got and elapsed > 0.2:
                    return NetworkInfo(mbit_per_s=round(got * 8 / elapsed / 1e6, 1))
        except Exception as exc:
            log.info("speed test via %s failed: %s", url, exc)
    return NetworkInfo(mbit_per_s=None)
