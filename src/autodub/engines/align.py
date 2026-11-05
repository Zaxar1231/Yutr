"""Word-level alignment with WhisperX (wav2vec2 CTC forced alignment)."""

from __future__ import annotations

from typing import Any

from autodub.engines import _audio
from autodub.engines.worker import CACHE


def run(params: dict[str, Any], progress: Any) -> dict[str, Any]:
    import whisperx

    dev = _audio.device(params.get("device", "cpu"))
    lang = params["language"]
    key = f"align:{lang}:{dev}"
    if key not in CACHE:
        progress(0.05, "Загружаю модель выравнивания")
        CACHE[key] = whisperx.load_align_model(
            language_code=lang, device=dev, model_dir=params.get("models_dir")
        )
    model, meta = CACHE[key]
    audio = whisperx.load_audio(params["audio"])
    progress(0.2, "Выравниваю слова")
    result = whisperx.align(params["segments"], model, meta, audio, dev, return_char_alignments=False)
    if not params.get("keep_loaded"):
        CACHE.pop(key, None)
    segments = []
    for seg in result["segments"]:
        words = [
            {"text": w["word"], "start": w["start"], "end": w["end"], "score": w.get("score")}
            for w in seg.get("words", [])
            if "start" in w and "end" in w
        ]
        segments.append({"start": seg["start"], "end": seg["end"], "text": seg["text"], "words": words})
    progress(1.0, "Готово")
    return {"segments": segments}
