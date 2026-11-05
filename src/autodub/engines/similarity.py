"""Speaker similarity (cosine of WeSpeaker ResNet34 embeddings, CC-BY-4.0) for TTS evaluation."""

from __future__ import annotations

from typing import Any

from autodub.engines.worker import CACHE


def run(params: dict[str, Any], progress: Any) -> dict[str, Any]:
    import numpy as np
    from pyannote.audio import Inference, Model

    if "spk_model" not in CACHE:
        model = Model.from_pretrained(
            "pyannote/wespeaker-voxceleb-resnet34-LM", cache_dir=params.get("cache_dir")
        )
        CACHE["spk_model"] = Inference(model, window="whole")
    inf = CACHE["spk_model"]
    ref = np.asarray(inf(params["reference"])).ravel()
    out: dict[str, float] = {}
    files = params["files"]
    for i, f in enumerate(files):
        emb = np.asarray(inf(f)).ravel()
        out[f] = float(np.dot(ref, emb) / (np.linalg.norm(ref) * np.linalg.norm(emb) + 1e-9))
        progress((i + 1) / len(files), "")
    return {"similarity": out}
