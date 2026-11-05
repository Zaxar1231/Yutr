"""Downloads with resume, retries, checksum verification and Hugging Face mirrors."""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autodub.updater.install import UpdateError, download, parse_checksums, sha256

log = logging.getLogger(__name__)
HF_ENDPOINTS = ("https://huggingface.co", "https://hf-mirror.com")
UA = {"User-Agent": "Autodub-Components"}

Progress = Callable[[float, str], None]


class FetchError(RuntimeError):
    pass


def get_text(url: str) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
        return str(r.read().decode("utf-8", "replace"))


def expected_sha256(url: str, *, checksums_url: str | None, pinned: str | None) -> str:
    if pinned:
        return pinned.lower()
    if not checksums_url:
        raise FetchError(f"no checksum source for {url}")
    text = get_text(checksums_url)
    name = url.rsplit("/", 1)[-1]
    sums = parse_checksums(text)
    if name in sums:
        return sums[name]
    # Deno publishes PowerShell Get-FileHash output: "Hash : ABCD..."
    for line in text.splitlines():
        if line.strip().lower().startswith("hash"):
            return line.split(":", 1)[1].strip().lower()
    raise FetchError(f"checksum for {name} not found in {checksums_url}")


def fetch_verified(url: str, dst: Path, sha: str, progress: Progress | None = None) -> Path:
    if dst.exists() and sha256(dst) == sha:
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        download(url, dst, progress=(lambda f: progress(f, dst.name)) if progress else None)
    except UpdateError as exc:
        raise FetchError(str(exc)) from exc
    actual = sha256(dst)
    if actual != sha:
        dst.unlink(missing_ok=True)
        raise FetchError(f"checksum mismatch for {dst.name}: {actual} != {sha}")
    return dst


@dataclass
class HfFile:
    path: str
    size: int
    sha256: str | None  # None for small non-LFS files (verified by size)


def hf_list(repo: str, files: tuple[str, ...] = ()) -> tuple[str, list[HfFile]]:
    """List repo files with LFS hashes; tries the main Hub, then the mirror."""
    last: Exception | None = None
    for ep in HF_ENDPOINTS:
        try:
            data = json.loads(get_text(f"{ep}/api/models/{repo}/tree/main?recursive=1"))
        except Exception as exc:
            last = exc
            log.warning("HF listing via %s failed: %s", ep, exc)
            continue
        out = []
        for f in data:
            if f.get("type") != "file":
                continue
            if files and f["path"] not in files:
                continue
            if not files and (f["path"].startswith(".") or f["path"].endswith((".md", ".svg", ".png"))):
                continue
            lfs = f.get("lfs") or {}
            out.append(HfFile(f["path"], int(lfs.get("size") or f.get("size") or 0), lfs.get("oid")))
        return ep, out
    raise FetchError(f"Hugging Face недоступен: {last}")


def hf_download(
    repo: str, dest: Path, files: tuple[str, ...] = (), progress: Progress | None = None
) -> list[HfFile]:
    endpoint, listing = hf_list(repo, files)
    total = sum(f.size for f in listing) or 1
    done = 0
    for f in listing:
        target = dest / f.path
        ok = (
            target.exists()
            and target.stat().st_size == f.size
            and (f.sha256 is None or sha256(target) == f.sha256)
        )
        if not ok:
            last: Exception | None = None
            for ep in dict.fromkeys([endpoint, *HF_ENDPOINTS]):
                url = f"{ep}/{repo}/resolve/main/{f.path}"
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    base = done

                    def cb(frac: float, _b: int = base, _s: int = f.size, _p: str = f.path) -> None:
                        if progress:
                            progress((_b + frac * _s) / total, _p)

                    download(url, target, progress=cb)
                    if f.sha256 and sha256(target) != f.sha256:
                        target.unlink(missing_ok=True)
                        raise FetchError(f"checksum mismatch: {f.path}")
                    last = None
                    break
                except Exception as exc:
                    last = exc
                    log.warning("download %s from %s failed: %s", f.path, ep, exc)
            if last:
                raise FetchError(f"не удалось скачать {f.path}: {last}")
        done += f.size
        if progress:
            progress(done / total, f.path)
    (dest / ".complete.json").write_text(
        json.dumps({"repo": repo, "files": [f.__dict__ for f in listing]}, indent=1), "utf-8"
    )
    return listing
