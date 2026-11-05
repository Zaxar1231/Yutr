"""Component manager: status, install, update, verify and repair of every external piece."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autodub.components import envs, manifest
from autodub.components.fetch import FetchError, expected_sha256, fetch_verified, hf_download, hf_list
from autodub.core.paths import AppPaths
from autodub.hardware.models import Accelerator, HardwareReport
from autodub.updater.install import sha256

log = logging.getLogger(__name__)
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
Progress = Callable[[float, str], None]


@dataclass
class Status:
    key: str
    title: str
    kind: str  # binary | data | model | env
    installed: bool
    size_mb: float
    license: str
    detail: str = ""
    noncommercial: bool = False


class ComponentManager:
    def __init__(self, paths: AppPaths, *, uv: str | None = None):
        self.paths = paths
        self.uv = uv or shutil.which("uv") or str(paths.root / "uv.exe")
        self.lock_file = paths.root / "components.json"
        self.lock: dict[str, dict[str, str]] = (
            json.loads(self.lock_file.read_text("utf-8")) if self.lock_file.exists() else {}
        )

    def _save(self) -> None:
        self.lock_file.write_text(json.dumps(self.lock, indent=1), "utf-8")

    # --- binaries / data ------------------------------------------------------------------

    def _bin_targets(self, b: manifest.Binary) -> list[Path]:
        base = self.paths.root / b.dest if b.dest != "bin" else self.paths.bin
        if b.key == "nltk_punkt_tab":
            return [self.paths.models / b.dest / "punkt_tab"]
        if b.extract:
            return [base / Path(x).name for x in b.extract]
        return [base / b.url.rsplit("/", 1)[-1]]

    def install_binary(self, key: str, progress: Progress | None = None) -> None:
        b = manifest.binaries()[key]
        sha = expected_sha256(b.url, checksums_url=b.checksums, pinned=b.sha256)
        dl = self.paths.cache / "downloads" / b.url.rsplit("/", 1)[-1]
        fetch_verified(b.url, dl, sha, progress)
        if key == "nltk_punkt_tab":
            dest = self.paths.models / b.dest
            dest.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(dl) as z:
                z.extractall(dest)
        elif dl.suffix == ".zip":
            with zipfile.ZipFile(dl) as z:
                names = z.namelist()
                for want in b.extract:
                    member = next((n for n in names if n.endswith("/" + want) or n == want), None)
                    if not member:
                        raise FetchError(f"{want} not found in {dl.name}")
                    target = self.paths.bin / Path(want).name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        else:
            self.paths.bin.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dl, self.paths.bin / dl.name)
        self.lock[key] = {
            "sha256": sha,
            "url": b.url,
            **{t.name: sha256(t) for t in self._bin_targets(b) if t.is_file()},
        }
        self._save()
        dl.unlink(missing_ok=True)

    def update_ytdlp(self) -> str:
        """yt-dlp updates itself (signed releases) at every start."""
        exe = self.paths.bin / ("yt-dlp.exe" if sys.platform == "win32" else "yt-dlp")
        if not exe.exists():
            return "not installed"
        cp = subprocess.run(
            [str(exe), "-U"],
            capture_output=True,
            text=True,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
            timeout=180,
        )
        return (cp.stdout or cp.stderr).strip().splitlines()[-1] if (cp.stdout or cp.stderr) else ""

    # --- models ---------------------------------------------------------------------------

    def model_dir(self, key: str) -> Path:
        return self.paths.models / manifest.models()[key].dir

    def model_size_mb(self, key: str) -> float:
        m = manifest.models()[key]
        _ep, files = hf_list(m.repo, m.files)
        return sum(f.size for f in files) / 2**20

    def install_model(self, key: str, progress: Progress | None = None) -> None:
        m = manifest.models()[key]
        hf_download(m.repo, self.model_dir(key), m.files, progress)

    # --- engine environment ---------------------------------------------------------------

    def install_env(
        self, report: HardwareReport | None, accelerator: Accelerator, progress: Progress | None = None
    ) -> Path:
        index = envs.torch_index(report, accelerator)
        if envs.is_current(self.paths.envs, index):
            return envs.python_path(self.paths.envs)
        return envs.build(self.paths.envs, index, uv=self.uv, progress=progress or (lambda f, m: None))

    # --- status / verify / repair ---------------------------------------------------------

    def status(self, *, deep: bool = False) -> list[Status]:
        out: list[Status] = []
        for key, b in manifest.binaries().items():
            targets = self._bin_targets(b)
            ok = all(t.exists() for t in targets)
            if ok and deep and key in self.lock:
                ok = all(sha256(t) == self.lock[key].get(t.name) for t in targets if t.is_file())
            out.append(
                Status(
                    key,
                    b.title,
                    "binary" if b.dest == "bin" else "data",
                    ok,
                    b.size_mb,
                    b.license,
                    b.description,
                )
            )
        for key, m in manifest.models().items():
            d = self.model_dir(key)
            done = d / ".complete.json"
            ok = done.exists()
            size = 0.0
            if ok:
                files = json.loads(done.read_text("utf-8"))["files"]
                size = sum(f["size"] for f in files) / 2**20
                for f in files:
                    p = d / f["path"]
                    if (
                        not p.exists()
                        or p.stat().st_size != f["size"]
                        or (deep and f.get("sha256") and sha256(p) != f["sha256"])
                    ):
                        ok = False
                        break
            out.append(Status(key, m.title, "model", ok, size, m.license, m.repo, m.noncommercial))
        py = envs.python_path(self.paths.envs)
        out.append(
            Status(
                "env:torch-main", "PyTorch и движки нейросетей", "env", py.exists(), 0, "BSD/MIT/MPL", str(py)
            )
        )
        return out

    def repair(
        self,
        needed_models: list[str],
        report: HardwareReport | None,
        accelerator: Accelerator,
        progress: Progress | None = None,
    ) -> list[str]:
        """Check integrity of everything and reinstall what is broken or missing."""
        fixed = []
        for st in self.status(deep=True):
            if st.installed:
                continue
            if st.kind in ("binary", "data"):
                self.install_binary(st.key, progress)
            elif st.kind == "model" and st.key in needed_models:
                (self.model_dir(st.key) / ".complete.json").unlink(missing_ok=True)
                self.install_model(st.key, progress)
            elif st.kind == "env":
                self.install_env(report, accelerator, progress)
            else:
                continue
            fixed.append(st.key)
        return fixed
