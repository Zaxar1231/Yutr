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


def trim_silence(wav: Any, sr: int, *, threshold_db: float = -42.0, pad: float = 0.06) -> Any:
    """Cut leading/trailing silence (TTS models sometimes add a long tail)."""
    import numpy as np

    x = np.asarray(wav, dtype="float32")
    if x.size == 0:
        return x
    win = max(1, int(0.02 * sr))
    n = len(x) // win
    if n == 0:
        return x
    frames = x[: n * win].reshape(n, win)
    db = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-9)
    loud = np.nonzero(db > max(threshold_db, db.max() - 45))[0]
    if loud.size == 0:
        return x
    a = max(0, loud[0] * win - int(pad * sr))
    b = min(len(x), (loud[-1] + 1) * win + int(pad * sr))
    return x[a:b]
