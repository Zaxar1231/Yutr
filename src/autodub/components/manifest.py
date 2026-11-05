"""Component manifest: what can be installed, from where, and how it is verified."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any


@dataclass(frozen=True)
class Binary:
    key: str
    title: str
    description: str
    license: str
    url: str
    size_mb: int
    update: str
    checksums: str | None = None
    sha256: str | None = None
    extract: tuple[str, ...] = ()
    dest: str = ""  # folder relative to data root; default bin/


@dataclass(frozen=True)
class ModelSpec:
    key: str
    title: str
    repo: str
    dir: str
    license: str
    files: tuple[str, ...] = ()
    noncommercial: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@cache
def load() -> dict[str, Any]:
    text = resources.files("autodub.components.data").joinpath("components.toml").read_text("utf-8")
    return tomllib.loads(text)


def binaries() -> dict[str, Binary]:
    data = load()
    out: dict[str, Binary] = {}
    for section in ("binaries", "data"):
        for key, b in data.get(section, {}).items():
            out[key] = Binary(
                key=key,
                title=b["title"],
                description=b["description"],
                license=b["license"],
                url=b["url"],
                size_mb=int(b.get("size_mb", 0)),
                update=b.get("update", "pinned"),
                checksums=b.get("checksums"),
                sha256=b.get("sha256"),
                extract=tuple(b.get("extract", ())),
                dest=b.get("dest", "bin" if section == "binaries" else ""),
            )
    return out


def models() -> dict[str, ModelSpec]:
    return {
        key: ModelSpec(
            key=key,
            title=m["title"],
            repo=m["repo"],
            dir=m["dir"],
            license=m["license"],
            files=tuple(m.get("files", ())),
            noncommercial=bool(m.get("noncommercial")),
        )
        for key, m in load().get("models", {}).items()
    }


def models_for(settings: Any, *, offline_translation: bool, tts_engine: str | None = None) -> list[str]:
    """Model keys a profile needs. Only these are downloaded on first run."""
    keys = [f"whisper:{settings.whisper_model}", f"tts:{tts_engine or settings.tts_model}"]
    if offline_translation:
        keys.append("translate:madlad400-3b")
    return keys
