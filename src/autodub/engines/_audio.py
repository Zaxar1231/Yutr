"""Audio helpers for the worker (soundfile + numpy are present in the engine environment)."""

from __future__ import annotations

from typing import Any


def read(path: str) -> tuple[Any, int]:
    import soundfile as sf

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return data.T, int(sr)  # (channels, samples)


def write(path: str, data: Any, sr: int) -> None:
    import numpy as np
    import soundfile as sf

    arr = np.asarray(data, dtype="float32")
    if arr.ndim == 2:
        arr = arr.T
    sf.write(path, arr, sr, subtype="FLOAT")


def device(name: str) -> str:
    """'cuda' falls back to CPU when torch has no GPU (e.g. CPU wheels)."""
    import torch

    if name == "cuda" and not torch.cuda.is_available():
        return "cpu"
    return name
