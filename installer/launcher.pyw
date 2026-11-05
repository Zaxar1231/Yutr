"""Autodub launcher: prepares the core environment, starts the current version, rolls back
automatically if a freshly installed version does not start.

Lives in the install root, outside the versioned app folders, and uses only the standard
library (it runs before any dependency is installed). Started by the shortcut through
``uvw.exe run --python 3.12 --no-project --gui-script launcher.pyw`` so no system Python is
needed and no console window appears.

Layout (see autodub.updater.install):
    <root>/versions/<x.y.z>/src/autodub/...   app code
    <root>/versions/<x.y.z>/requirements.txt  pinned core dependencies (with hashes)
    <root>/current, <root>/previous, <root>/bad.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HEALTH = ".started_ok"
START_TIMEOUT = float(os.environ.get("AUTODUB_START_TIMEOUT", "120"))
IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0


def log(msg: str) -> None:
    logs = data_root() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "launcher.log").open("a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")


def data_root() -> Path:
    if (ROOT / "portable.flag").exists():
        return ROOT / "data"
    if os.environ.get("AUTODUB_DATA_DIR"):
        return Path(os.environ["AUTODUB_DATA_DIR"])
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "Autodub"


def read(name: str) -> str | None:
    p = ROOT / name
    return p.read_text("utf-8").strip() if p.exists() else None


def write(name: str, value: str) -> None:
    tmp = ROOT / (name + ".tmp")
    tmp.write_text(value, "utf-8")
    os.replace(tmp, ROOT / name)


def mark_bad(version: str) -> None:
    p = ROOT / "bad.json"
    bad = set(json.loads(p.read_text("utf-8"))) if p.exists() else set()
    bad.add(version)
    p.write_text(json.dumps(sorted(bad)), "utf-8")


def message(title: str, text: str) -> None:
    log(f"{title}: {text}")
    if os.environ.get("AUTODUB_LAUNCHER_HEADLESS"):
        return
    try:
        import tkinter
        from tkinter import messagebox

        r = tkinter.Tk()
        r.withdraw()
        messagebox.showinfo(title, text)
        r.destroy()
    except Exception:
        pass


class Splash:
    """Tiny progress window for the one-time core environment setup."""

    def __init__(self, text: str):
        self.root = None
        if os.environ.get("AUTODUB_LAUNCHER_HEADLESS"):
            return
        try:
            import tkinter
            from tkinter import ttk

            self.root = tkinter.Tk()
            self.root.title("Автодубляж")
            self.root.geometry("420x120")
            self.root.resizable(False, False)
            self.label = tkinter.Label(self.root, text=text, font=("Segoe UI", 11), wraplength=380)
            self.label.pack(pady=(20, 10))
            self.bar = ttk.Progressbar(self.root, mode="indeterminate", length=360)
            self.bar.pack()
            self.bar.start(12)
            self.root.update()
        except Exception:
            self.root = None

    def pump(self, text: str | None = None) -> None:
        if self.root is not None:
            if text:
                self.label.config(text=text)
            self.root.update()

    def close(self) -> None:
        if self.root is not None:
            self.root.destroy()
            self.root = None


def uv() -> str:
    for name in ("uv.exe", "uv"):
        if (ROOT / name).exists():
            return str(ROOT / name)
    return "uv"


def core_python(env_dir: Path, gui: bool) -> Path:
    if IS_WIN:
        return env_dir / "Scripts" / ("pythonw.exe" if gui else "python.exe")
    return env_dir / "bin" / "python"


def run_step(cmd: list[str], splash: Splash) -> None:
    log("run: " + " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
                            env=os.environ | uv_env())
    assert proc.stdout
    for line in proc.stdout:
        log("  " + line.rstrip())
        splash.pump()
    if proc.wait() != 0:
        raise RuntimeError(f"{cmd[1]} failed")


def uv_env() -> dict[str, str]:
    """uv-managed Python only (never the system one); download cache inside our data folder."""
    return {"UV_CACHE_DIR": str(data_root() / "cache" / "uv"), "UV_PYTHON_PREFERENCE": "only-managed"}


def ensure_core_env(version: str) -> Path:
    """Create/sync the core venv when the version's pinned requirements changed."""
    req = ROOT / "versions" / version / "requirements.txt"
    env_dir = data_root() / "envs" / "core"
    stamp = env_dir / "requirements.sha256"
    want = hashlib.sha256(req.read_bytes()).hexdigest() if req.exists() else "none"
    if stamp.exists() and stamp.read_text().strip() == want and core_python(env_dir, False).exists():
        return env_dir
    splash = Splash("Подготавливаю программу к первому запуску… Это займёт пару минут.")
    try:
        if not core_python(env_dir, False).exists():
            run_step([uv(), "venv", "--python", "3.12", str(env_dir)], splash)
        if req.exists():
            splash.pump("Устанавливаю библиотеки…")
            run_step([uv(), "pip", "sync", "--python", str(core_python(env_dir, False)), str(req)], splash)
        stamp.write_text(want)
    finally:
        splash.close()
    return env_dir


def start(version: str) -> subprocess.Popen[bytes]:
    vdir = ROOT / "versions" / version
    (vdir / HEALTH).unlink(missing_ok=True)
    env_dir = ensure_core_env(version)
    python = core_python(env_dir, gui=True)
    module = os.environ.get("AUTODUB_LAUNCH_MODULE", "autodub.app.main")
    env = os.environ | {
        "PYTHONPATH": str(vdir / "src"),
        "AUTODUB_INSTALL_ROOT": str(ROOT),
        "AUTODUB_APP_ROOT": str(ROOT),
    }
    log(f"starting {version}: {python} -m {module}")
    return subprocess.Popen([str(python), "-m", module], env=env, cwd=str(vdir), creationflags=NO_WINDOW)


def watch(proc: subprocess.Popen[bytes], version: str) -> bool:
    """True if the version reported a healthy start (UI up) in time."""
    marker = ROOT / "versions" / version / HEALTH
    deadline = time.monotonic() + START_TIMEOUT
    while time.monotonic() < deadline:
        if marker.exists():
            return True
        code = proc.poll()
        if code is not None:
            return marker.exists()
        time.sleep(0.5)
    return marker.exists()


def wait_pid(pid: int, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if IS_WIN:
                out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True,
                                     creationflags=NO_WINDOW).stdout
                if str(pid) not in out:
                    return
            else:
                os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.5)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--after-update", action="store_true")
    ap.add_argument("--wait-pid", type=int)
    args = ap.parse_args()
    if args.wait_pid:
        wait_pid(args.wait_pid)
    version = read("current")
    if not version or not (ROOT / "versions" / version).exists():
        message("Автодубляж", "Программа установлена не полностью. Запустите установщик ещё раз.")
        return 1
    try:
        proc = start(version)
    except Exception as exc:
        proc = None
        log(f"start failed: {exc!r}")
    if proc is not None and watch(proc, version):
        log(f"{version} started")
        return 0
    previous = read("previous")
    if previous and previous != version and (ROOT / "versions" / previous).exists():
        if proc is not None and proc.poll() is None:
            proc.kill()
        mark_bad(version)
        write("current", previous)
        (ROOT / "previous").unlink(missing_ok=True)
        message("Автодубляж", f"Версия {version} не запустилась, поэтому возвращена версия {previous}. "
                              "Это обновление больше не будет предлагаться.")
        proc = start(previous)
        return 0 if watch(proc, previous) else 2
    message("Автодубляж", "Не удалось запустить программу. Откройте её ещё раз; если не поможет — "
                          "переустановите. Подробности в журнале launcher.log.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
