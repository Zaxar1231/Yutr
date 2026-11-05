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
SCREENS = ["home", "system", "settings", "components", "about", "result"]


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


def shoot(
    machine: str,
    theme: str,
    browser_path: str | None,
    screen: str,
    *,
    expand: bool,
    demo_output: str | None,
    demo_source: str | None,
) -> list[Path]:
    import importlib.util

    spec = importlib.util.spec_from_file_location("demo_queue", ROOT / "scripts" / "demo_queue.py")
    assert spec and spec.loader
    demo_queue = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo_queue)
    port = free_port()
    with tempfile.TemporaryDirectory() as data:
        Path(data, "config.toml").write_text(
            f'theme = "{theme}"\nfirst_run_done = true\n[hardware]\nnetwork_test = false\n', "utf-8"
        )
        import json as _json

        Path(data, "queue.json").write_text(
            _json.dumps(demo_queue.build(demo_output, demo_source), ensure_ascii=False), "utf-8"
        )
        env = os.environ | {
            "AUTODUB_DATA_DIR": data,
            "AUTODUB_DEMO_HARDWARE": str(ROOT / "scripts" / "demo_hardware" / f"{machine}.json"),
            "AUTODUB_WEB_PORT": str(port),
            "AUTODUB_START_SCREEN": screen,
        }
        app = subprocess.Popen([sys.executable, "-m", "autodub.app.main"], env=env)
        saved = []
        try:
            wait_port(port)
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=browser_path)
                page = browser.new_page(viewport={"width": 1280, "height": 860}, device_scale_factor=2)
                page.goto(f"http://127.0.0.1:{port}/")
                page.wait_for_timeout(7000)
                page.evaluate("document.querySelector('flt-semantics-placeholder')?.click()")
                page.wait_for_timeout(500)
                OUT.mkdir(parents=True, exist_ok=True)
                path = OUT / f"{screen}-{machine}-{theme}.png"
                page.screenshot(path=str(path))
                saved.append(path)
                if expand and screen == "system":
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
    ap.add_argument("--screen", action="append", choices=SCREENS)
    ap.add_argument("--browser", default=os.environ.get("AUTODUB_CHROMIUM"))
    ap.add_argument("--demo-output", help="a dubbed video for the result screen")
    ap.add_argument("--demo-source", help="its original video")
    args = ap.parse_args()
    for machine in args.machine or ["rtx4070"]:
        for theme in args.theme or ["light", "dark"]:
            for screen in args.screen or SCREENS:
                if screen == "result" and not args.demo_output:
                    continue
                for p in shoot(
                    machine,
                    theme,
                    args.browser,
                    screen,
                    expand=machine == "rtx4070",
                    demo_output=args.demo_output,
                    demo_source=args.demo_source,
                ):
                    print(p.relative_to(ROOT), flush=True)


if __name__ == "__main__":
    main()
