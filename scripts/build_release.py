"""Assemble release artefacts.

    uv run python scripts/build_release.py [--uv-zip PATH]

Produces in dist/:
  * Autodub-<v>-update.zip — the versioned app folder (src + pinned requirements) used by
    the auto-updater;
  * build/stage/ — the folder Inno Setup packs into Autodub-Setup-<v>.exe;
  * checksums.txt is written by the release workflow after the installer is built.

The version comes from pyproject.toml only.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UV_VERSION = "0.13.7"
UV_ZIP_URL = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/uv-x86_64-pc-windows-msvc.zip"
UV_ZIP_SHA256 = "0cdc5b38fe5a8168ed6a25814f5ba43c21d313dd3187314fccb37b7cec46e021"


def version() -> str:
    return str(tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"])


def requirements(dst: Path) -> None:
    out = subprocess.run(
        [
            "uv",
            "export",
            "--format",
            "requirements-txt",
            "--no-dev",
            "--no-emit-project",
            "--frozen",
            "--no-header",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    dst.write_text(out, "utf-8")


def app_folder(dst: Path, ver: str) -> None:
    shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(
        ROOT / "src" / "autodub",
        dst / "src" / "autodub",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    requirements(dst / "requirements.txt")
    (dst / "VERSION").write_text(ver, "utf-8")


def zip_dir(src: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(src).as_posix())


def fetch_uv(cache: Path, given: str | None) -> Path:
    path = Path(given) if given else cache / "uv-win.zip"
    if not path.exists():
        cache.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(UV_ZIP_URL, path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != UV_ZIP_SHA256:
        raise SystemExit("uv archive checksum mismatch")
    return path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--uv-zip")
    ap.add_argument("--skip-uv", action="store_true", help="only the update archive (no Windows bits)")
    args = ap.parse_args()
    ver = version()
    build, dist = ROOT / "build", ROOT / "dist"
    dist.mkdir(exist_ok=True)
    app = build / "app" / ver
    app_folder(app, ver)
    update_zip = dist / f"Autodub-{ver}-update.zip"
    zip_dir(app, update_zip)
    print(update_zip.relative_to(ROOT))

    stage = build / "stage"
    shutil.rmtree(stage, ignore_errors=True)
    shutil.copytree(app, stage / "versions" / ver)
    shutil.copy2(ROOT / "installer" / "launcher.pyw", stage / "launcher.pyw")
    if not args.skip_uv:
        with zipfile.ZipFile(fetch_uv(build / "cache", args.uv_zip)) as z:
            for name in ("uv.exe", "uvw.exe"):
                z.extract(name, stage)
    print(stage.relative_to(ROOT))


if __name__ == "__main__":
    main()
