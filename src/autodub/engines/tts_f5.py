"""F5-TTS with a Russian fine-tune (flow matching, zero-shot cloning from a reference + its text).

Like XTTS, the phrase's own original clip is used as the reference when it is long enough,
which carries over its intonation; otherwise the speaker's clean reference is used.
"""

from __future__ import annotations

import re
from typing import Any

from autodub.engines import _audio
from autodub.engines.worker import CACHE

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def normalize_ru(text: str) -> str:
    try:
        from num2words import num2words
    except ImportError:
        return text

    def repl(m: re.Match[str]) -> str:
        s = m.group(0).replace(",", ".")
        try:
            return str(num2words(float(s) if "." in s else int(s), lang="ru"))
        except Exception:
            return m.group(0)

    return _NUM.sub(repl, text)


def run(params: dict[str, Any], progress: Any) -> dict[str, Any]:
    import torch
    from f5_tts.api import F5TTS

    dev = _audio.device(params.get("device", "cpu"))
    if params.get("threads"):
        torch.set_num_threads(int(params["threads"]))
    key = f"f5:{params['ckpt_file']}:{dev}"
    if key not in CACHE:
        progress(0.01, "Загружаю модель озвучки")
        CACHE[key] = F5TTS(
            model="F5TTS_v1_Base",
            ckpt_file=params["ckpt_file"],
            vocab_file=params["vocab_file"],
            device=dev,
            hf_cache_dir=params.get("hf_cache_dir"),
        )
    tts = CACHE[key]
    items = params["items"]
    durations: dict[str, float] = {}
    for i, item in enumerate(items):
        ref, ref_text = item.get("prosody_ref"), item.get("prosody_text")
        # A phrase clip is a valid reference only if it is long enough to carry the voice.
        if not ref or float(item.get("prosody_seconds", 0)) < 3.0:
            ref, ref_text = params["speaker_refs"][item["speaker"]], params["speaker_texts"][item["speaker"]]
        wav, sr, _ = tts.infer(
            ref,
            ref_text,
            normalize_ru(item["text"]),
            show_info=lambda *a, **k: None,
            speed=float(item.get("speed", 1.0)),
            nfe_step=int(params.get("nfe_step", 32)),
            remove_silence=False,
        )
        wav = _audio.trim_silence(wav, sr)
        _audio.write(item["out"], wav, sr)
        durations[str(item["id"])] = round(len(wav) / sr, 3)
        progress((i + 1) / len(items), f"Озвучено фраз: {i + 1} из {len(items)}")
    if not params.get("keep_loaded"):
        CACHE.pop(key, None)
    return {"durations": durations}
