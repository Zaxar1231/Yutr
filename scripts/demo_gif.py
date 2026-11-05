"""Record the demo GIF: paste a link → progress through the stages → result screen.

uv run python scripts/demo_gif.py --browser CHROMIUM --output OUT.mp4 --source SRC.mp4
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from screenshots import ROOT, free_port, wait_port

URL = "https://archive.org/details/GETLAMP-Eilers"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser")
    ap.add_argument("--output", required=True)
    ap.add_argument("--source")
    ap.add_argument("--theme", default="light")
    args = ap.parse_args()
    port = free_port()
    frames = Path(tempfile.mkdtemp())
    with tempfile.TemporaryDirectory() as data:
        Path(data, "config.toml").write_text(
            f'theme = "{args.theme}"\nfirst_run_done = true\n[hardware]\nnetwork_test = false\n', "utf-8"
        )
        env = os.environ | {
            "AUTODUB_DATA_DIR": data,
            "AUTODUB_WEB_PORT": str(port),
            "AUTODUB_DEMO_HARDWARE": str(ROOT / "scripts" / "demo_hardware" / "rtx4070.json"),
            "AUTODUB_DEMO_SIMULATE": "9",
            "AUTODUB_DEMO_SIM_OUTPUT": args.output,
            "AUTODUB_DEMO_SIM_SOURCE": args.source or "",
        }
        app = subprocess.Popen([sys.executable, "-m", "autodub.app.main"], env=env)
        try:
            wait_port(port)
            with sync_playwright() as pw:
                b = pw.chromium.launch(executable_path=args.browser)
                page = b.new_page(viewport={"width": 1100, "height": 700}, device_scale_factor=1)
                page.goto(f"http://127.0.0.1:{port}/")
                page.wait_for_timeout(7000)
                page.evaluate("document.querySelector('flt-semantics-placeholder')?.click()")
                page.wait_for_timeout(500)
                n = 0

                def shot(count: int = 1) -> None:
                    nonlocal n
                    for _ in range(count):
                        page.screenshot(path=str(frames / f"{n:04d}.png"))
                        n += 1
                        page.wait_for_timeout(200)

                shot(4)
                box = page.get_by_role("textbox").first
                box.click()
                for ch in URL:
                    page.keyboard.type(ch)
                    if len(URL) and ch in "/.":
                        shot()
                shot(3)
                page.get_by_text("Перевести", exact=True).click()
                for _ in range(55):
                    shot()
                page.get_by_text("Открыть", exact=True).first.click()
                page.wait_for_timeout(1500)
                shot(12)
                b.close()
        finally:
            app.terminate()
            app.wait(10)
    out = ROOT / "docs" / "assets" / "demo.gif"
    pal = frames / "pal.png"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            "5",
            "-i",
            str(frames / "%04d.png"),
            "-vf",
            "scale=880:-1:flags=lanczos,palettegen=stats_mode=diff",
            str(pal),
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            "5",
            "-i",
            str(frames / "%04d.png"),
            "-i",
            str(pal),
            "-lavfi",
            "scale=880:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=4",
            str(out),
        ],
        check=True,
    )
    shutil.rmtree(frames, ignore_errors=True)
    print(out.relative_to(ROOT), out.stat().st_size // 1024, "KB")


if __name__ == "__main__":
    main()
