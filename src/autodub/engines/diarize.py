"""Speaker diarization with pyannote (community-1; needs a Hugging Face token)."""

from __future__ import annotations

from typing import Any

from autodub.engines import _audio
from autodub.engines.worker import CACHE

MODEL = "pyannote/speaker-diarization-community-1"


def run(params: dict[str, Any], progress: Any) -> dict[str, Any]:
    import torch
    from pyannote.audio import Pipeline

    dev = _audio.device(params.get("device", "cpu"))
    key = f"diarize:{dev}"
    if key not in CACHE:
        progress(0.05, "Загружаю модель спикеров")
        pipe = Pipeline.from_pretrained(MODEL, token=params["token"], cache_dir=params.get("cache_dir"))
        pipe.to(torch.device(dev))
        CACHE[key] = pipe
    pipe = CACHE[key]
    wav, sr = _audio.read(params["audio"])
    waveform = torch.from_numpy(wav[:1])
    kwargs: dict[str, Any] = {}
    if params.get("num_speakers"):
        kwargs["num_speakers"] = int(params["num_speakers"])
    progress(0.2, "Определяю, кто говорит")
    out = pipe({"waveform": waveform, "sample_rate": sr}, **kwargs)
    ann = getattr(out, "exclusive_speaker_diarization", None) or getattr(out, "speaker_diarization", out)
    turns = [
        {"start": float(turn.start), "end": float(turn.end), "speaker": str(spk)}
        for turn, _track, spk in ann.itertracks(yield_label=True)
    ]
    if not params.get("keep_loaded"):
        CACHE.pop(key, None)
    progress(1.0, "Готово")
    return {"turns": turns}
