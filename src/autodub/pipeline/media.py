"""ffmpeg / ffprobe helpers. Everything goes through argument lists, never a shell."""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class MediaError(RuntimeError):
    pass


class Tools:
    def __init__(self, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe"):
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def run(self, args: list[str], *, what: str) -> None:
        cmd = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", *args]
        log.debug("ffmpeg %s", " ".join(cmd))
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
        if cp.returncode != 0:
            raise MediaError(f"ffmpeg failed ({what}): {cp.stderr.strip()[-800:]}")

    def probe(self, path: Path) -> dict:  # type: ignore[type-arg]
        cp = subprocess.run(
            [
                self.ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
        if cp.returncode != 0:
            raise MediaError(f"ffprobe failed: {cp.stderr.strip()[-400:]}")
        return dict(json.loads(cp.stdout))

    def duration(self, path: Path) -> float:
        return float(self.probe(path)["format"]["duration"])

    def has_video(self, path: Path) -> bool:
        return any(s.get("codec_type") == "video" for s in self.probe(path)["streams"])

    def extract_audio(
        self, src: Path, dst: Path, *, sr: int, channels: int, start: float = 0, length: float | None = None
    ) -> None:
        args = []
        if start:
            args += ["-ss", f"{start:.3f}"]
        args += ["-i", str(src)]
        if length:
            args += ["-t", f"{length:.3f}"]
        args += ["-vn", "-ac", str(channels), "-ar", str(sr), "-c:a", "pcm_f32le", str(dst)]
        self.run(args, what="extract audio")

    def stretch(self, src: Path, dst: Path, *, speed: float, sr: int = 48000) -> None:
        """Change tempo without changing pitch (rubberband, formant-preserving)."""
        if abs(speed - 1.0) < 1e-3:
            self.run(
                ["-i", str(src), "-ar", str(sr), "-ac", "1", "-c:a", "pcm_f32le", str(dst)], what="resample"
            )
            return
        filt = f"rubberband=tempo={speed:.4f}:formant=preserved:pitchq=quality"
        self.run(
            ["-i", str(src), "-af", filt, "-ar", str(sr), "-ac", "1", "-c:a", "pcm_f32le", str(dst)],
            what="time stretch",
        )

    def has_filter(self, name: str) -> bool:
        cp = subprocess.run(
            [self.ffmpeg, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
        return any(line.split()[1:2] == [name] for line in cp.stdout.splitlines() if line.strip())
