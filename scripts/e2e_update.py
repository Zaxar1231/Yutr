"""End-to-end check of install → update → rollback with the real launcher and updater.

    uv run python scripts/e2e_update.py [--workdir DIR]

1. "Installs" v0.1.0 into a temp folder exactly as the installer lays it out.
2. The launcher bootstraps the core environment with uv (real `uv venv` + `uv pip sync`).
3. A local fake GitHub serves release v0.1.1 (update zip + checksums.txt); the running
   app finds it, downloads, verifies SHA-256, stages and switches.
4. Next launch starts v0.1.1 and it reports a healthy start.
5. v0.1.2 is broken (crashes on start): the launcher rolls back to v0.1.1 and marks 0.1.2 bad.
6. Settings/models/cache in the data folder survive all of it.

Runs on Linux and Windows (CI); exits non-zero on the first failed check.
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[1]


def build_version(dst: Path, version: str) -> Path:
    """versions/<v>/ = src + requirements.txt + VERSION (same as scripts/build_release.py)."""
    shutil.copytree(
        ROOT / "src" / "autodub", dst / "src" / "autodub", ignore=shutil.ignore_patterns("__pycache__")
    )
    req = subprocess.run(
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
    )
    (dst / "requirements.txt").write_text(req.stdout, "utf-8")
    (dst / "VERSION").write_text(version, "utf-8")
    return dst


def zip_dir(src: Path, archive: Path) -> str:
    with zipfile.ZipFile(archive, "w") as z:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(src).as_posix())
    return hashlib.sha256(archive.read_bytes()).hexdigest()


class FakeGitHub(http.server.SimpleHTTPRequestHandler):
    releases: ClassVar[list[dict]] = []
    files: ClassVar[Path] = Path()

    def do_GET(self) -> None:
        if self.path.startswith("/repos/"):
            body = json.dumps(self.releases).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("ETag", '"v1"')
            self.end_headers()
            self.wfile.write(body)
            return
        p = self.files / self.path.lstrip("/")
        if not p.exists():
            self.send_error(404)
            return
        data = p.read_bytes()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a: object) -> None:
        pass


def check(cond: bool, what: str) -> None:
    print(("OK   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        sys.exit(1)


def launcher(install: Path, data: Path, action: str, report: Path, extra: dict[str, str]) -> int:
    env = (
        os.environ
        | extra
        | {
            "AUTODUB_DATA_DIR": str(data),
            "AUTODUB_LAUNCH_MODULE": "autodub.updater.selftest",
            "AUTODUB_SELFTEST_ACTION": action,
            "AUTODUB_SELFTEST_REPORT": str(report),
            "AUTODUB_LAUNCHER_HEADLESS": "1",
            "AUTODUB_START_TIMEOUT": "120",
        }
    )
    env.pop("VIRTUAL_ENV", None)
    report.unlink(missing_ok=True)
    code = subprocess.run([sys.executable, str(install / "launcher.pyw")], env=env, timeout=1800).returncode
    if not report.exists():
        log = data / "logs" / "launcher.log"
        print(log.read_text("utf-8")[-6000:] if log.exists() else "no launcher log", flush=True)
    return code


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir")
    args = ap.parse_args()
    work = Path(args.workdir or tempfile.mkdtemp(prefix="autodub-e2e-"))
    install, data, srv = work / "install", work / "data", work / "server"
    for d in (install, data, srv):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)
    (data / "config.toml").write_text('theme = "dark"\n', "utf-8")  # user settings must survive
    (data / "models").mkdir()
    (data / "models" / "keep.bin").write_text("model", "utf-8")

    # 1. install v0.1.0
    build_version(install / "versions" / "0.1.0", "0.1.0")
    shutil.copy2(ROOT / "installer" / "launcher.pyw", install / "launcher.pyw")
    (install / "current").write_text("0.1.0", "utf-8")

    # fake GitHub with v0.1.1
    v011 = build_version(work / "build011", "0.1.1")
    sha = zip_dir(v011, srv / "Autodub-0.1.1-update.zip")
    (srv / "checksums.txt").write_text(f"{sha}  Autodub-0.1.1-update.zip\n", "utf-8")
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeGitHub)
    base = f"http://127.0.0.1:{httpd.server_port}"
    FakeGitHub.files = srv
    FakeGitHub.releases = [
        {
            "tag_name": "v0.1.1",
            "name": "0.1.1",
            "prerelease": False,
            "body": "## Исправлено\n- всё",
            "assets": [
                {
                    "name": "Autodub-0.1.1-update.zip",
                    "browser_download_url": f"{base}/Autodub-0.1.1-update.zip",
                },
                {"name": "checksums.txt", "browser_download_url": f"{base}/checksums.txt"},
            ],
        }
    ]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    gh = {"AUTODUB_GITHUB_API": base, "AUTODUB_SELFTEST_REPO": "o/r"}

    # 2-3. first start: bootstrap + find/stage update
    report = work / "report.json"
    code = launcher(install, data, "update", report, gh)
    rep = json.loads(report.read_text())
    check(code == 0, "v0.1.0 bootstraps the core environment with uv and starts")
    check(rep["version"] == "0.1.0", f"running version is 0.1.0 (got {rep['version']})")
    check(rep.get("found") == "0.1.1", "update 0.1.1 found through the GitHub API")
    check(
        (install / "versions" / "0.1.1" / "VERSION").exists(), "update downloaded, SHA-256 verified, unpacked"
    )
    check((install / "current").read_text() == "0.1.1", "pointer switched to 0.1.1 for the next start")

    # 4. start 0.1.1
    code = launcher(install, data, "ok", report, gh)
    check(
        code == 0 and json.loads(report.read_text())["version"] == "0.1.1", "v0.1.1 starts after the update"
    )
    check((install / "versions" / "0.1.1" / ".started_ok").exists(), "v0.1.1 reported a healthy start")

    # 5. broken 0.1.2 -> rollback
    shutil.copytree(install / "versions" / "0.1.1", install / "versions" / "0.1.2")
    (install / "versions" / "0.1.2" / "VERSION").write_text("0.1.2", "utf-8")
    (install / "versions" / "0.1.2" / ".started_ok").unlink(missing_ok=True)
    (install / "previous").write_text("0.1.1", "utf-8")
    (install / "current").write_text("0.1.2", "utf-8")
    env_crash = gh | {"AUTODUB_SELFTEST_CRASH_VERSION": "0.1.2"}
    code = launcher(install, data, "crash", report, env_crash)
    check(code == 0, "launcher recovered: the previous version started instead")
    check((install / "current").read_text().strip() == "0.1.1", "broken 0.1.2 rolled back to 0.1.1")
    check("0.1.2" in json.loads((install / "bad.json").read_text()), "0.1.2 remembered as bad")

    # 6. user data untouched
    check((data / "config.toml").read_text() == 'theme = "dark"\n', "settings untouched by updates")
    check((data / "models" / "keep.bin").exists(), "models untouched by updates")
    httpd.shutdown()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
