"""Автодубляж — локальный дубляж видео на русский язык."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("autodub")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0+unknown"
