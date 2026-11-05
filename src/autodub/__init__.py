"""Автодубляж — локальный дубляж видео на русский язык."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _version() -> str:
    # Installed layout: versions/<x.y.z>/VERSION next to src/ (code is on PYTHONPATH, not pip-installed).
    marker = Path(__file__).resolve().parents[2] / "VERSION"
    if marker.exists():
        return marker.read_text("utf-8").strip()
    try:
        return version("autodub")
    except PackageNotFoundError:  # running from a source tree without installation
        return "0.0.0+unknown"


__version__ = _version()
