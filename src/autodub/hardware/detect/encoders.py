"""Hardware video encoders available in ffmpeg.

``ffmpeg -encoders`` lists what the build supports, not what the machine can run (an NVENC
build on a PC without NVIDIA lists ``h264_nvenc`` too). Each listed encoder is therefore
probed with a tiny real encode.
"""

from __future__ import annotations

from autodub.core import proc
from autodub.hardware.models import EncoderInfo

CANDIDATES = {"nvenc": "h264_nvenc", "qsv": "h264_qsv", "amf": "h264_amf"}


def listed_encoders(encoders_output: str) -> set[str]:
    names = set()
    for line in encoders_output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            names.add(parts[1])
    return names


def probe(ffmpeg: str, codec: str, runner: proc.Runner = proc.run) -> bool:
    res = runner(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=256x256:d=0.2",
            "-c:v",
            codec,
            "-f",
            "null",
            "-",
        ],
        timeout=20,
    )
    return res is not None and res.ok


def detect_encoders(ffmpeg: str = "ffmpeg", runner: proc.Runner = proc.run) -> EncoderInfo:
    res = runner([ffmpeg, "-hide_banner", "-encoders"])
    if res is None or not res.ok:
        return EncoderInfo(ffmpeg_found=False)
    listed = listed_encoders(res.stdout)
    found = {
        key: codec in listed and probe(ffmpeg, codec, runner) for key, codec in CANDIDATES.items()
    }
    return EncoderInfo(ffmpeg_found=True, **found)
