"""Automatic screenshots of the app screens in light and dark themes.

Runs the real app in Flet web mode on a fixed demo machine (scripts/demo_hardware/*.json),
opens it in headless Chromium and saves PNGs to docs/assets/screenshots/.

    uv run python scripts/screenshots.py [--machine rtx4070] [--browser /path/to/chromium]
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets" / "screenshots"
SCREENS = {"system": "Моя система"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_port(port: int, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    raise TimeoutError(f"app did not start on port {port}")


def shoot(machine: str, theme: str, browser_path: str | None, *, expand: bool) -> list[Path]:
    port = free_port()
    with tempfile.TemporaryDirectory() as data:
        Path(data, "config.toml").write_text(
            f'theme = "{theme}"\n[hardware]\nnetwork_test = false\n', "utf-8"
        )
        env = os.environ | {
            "AUTODUB_DATA_DIR": data,
            "AUTODUB_DEMO_HARDWARE": str(ROOT / "scripts" / "demo_hardware" / f"{machine}.json"),
            "AUTODUB_WEB_PORT": str(port),
        }
        app = subprocess.Popen([sys.executable, "-m", "autodub.app.main"], env=env)
        saved = []
        try:
            wait_port(port)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=browser_path)
                page = browser.new_page(
                    viewport={"width": 1280, "height": 860}, device_scale_factor=2
                )
                page.goto(f"http://127.0.0.1:{port}/")
                # Flutter draws into a canvas: wait for the first frame and for the
                # startup snackbar ("Железо определено заново") to go away.
                page.wait_for_timeout(11000)
                # Turn on Flutter's accessibility tree so controls can be found by text.
                page.evaluate("document.querySelector('flt-semantics-placeholder')?.click()")
                page.wait_for_timeout(500)
                OUT.mkdir(parents=True, exist_ok=True)
                for key in SCREENS:
                    path = OUT / f"{key}-{machine}-{theme}.png"
                    page.screenshot(path=str(path))
                    saved.append(path)
                if expand:
                    # Scroll to «Ручная настройка» and open it.
                    page.mouse.move(800, 500)
                    page.mouse.wheel(0, 2000)
                    page.wait_for_timeout(800)
                    page.get_by_text("Ручная настройка").click()
                    page.wait_for_timeout(1200)
                    page.mouse.wheel(0, 500)
                    page.wait_for_timeout(800)
                    path = OUT / f"system-overrides-{machine}-{theme}.png"
                    page.screenshot(path=str(path))
                    saved.append(path)
                browser.close()
        finally:
            app.terminate()
            app.wait(10)
        return saved


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--machine", action="append", help="demo machine name (repeatable)")
    ap.add_argument("--theme", action="append", choices=["light", "dark"])
    ap.add_argument("--browser", default=os.environ.get("AUTODUB_CHROMIUM"))
    args = ap.parse_args()
    for machine in args.machine or ["rtx4070"]:
        for theme in args.theme or ["light", "dark"]:
            for p in shoot(machine, theme, args.browser, expand=machine == "rtx4070"):
                print(p.relative_to(ROOT))


if __name__ == "__main__":
    main()
