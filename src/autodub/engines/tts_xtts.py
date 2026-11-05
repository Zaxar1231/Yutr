"""XTTS-v2 voice cloning (coqui-tts fork by Idiap).

Timbre comes from the speaker's clean reference (6–15 s). Intonation and emotion come from
the original phrase itself: the GPT conditioning latent is computed from the phrase's own
vocal clip (when long enough), so the Russian line follows the original delivery.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from autodub.engines import _audio
from autodub.engines.worker import CACHE

SR = 24000
MAX_CHARS = 180  # XTTS-v2 warns above ~182 characters for Russian


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split on sentence ends, then on commas, then on spaces, keeping pieces <= limit."""
    import re

    text = " ".join(text.split())
    if len(text) <= limit:
        return [text]
    for pattern in (r"(?<=[.!?…])\s+", r"(?<=[,;:—])\s+", r"\s+"):
        parts = [p for p in re.split(pattern, text) if p]
        if len(parts) > 1:
            out, cur = [], ""
            for p in parts:
                if cur and len(cur) + 1 + len(p) > limit:
                    out.append(cur)
                    cur = p
                else:
                    cur = f"{cur} {p}".strip()
            if cur:
                out.append(cur)
            if all(len(x) <= limit for x in out):
                return out
            return [y for x in out for y in split_text(x, limit)]
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def _load(model_dir: str, dev: str, fp16: bool) -> Any:
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts

    os.environ.setdefault("COQUI_TOS_AGREED", "1")  # the user accepts CPML in the app UI
    cfg = XttsConfig()
    cfg.load_json(str(Path(model_dir) / "config.json"))
    model = Xtts(cfg)  # init_from_config is deprecated in coqui-tts 0.28
    model.load_checkpoint(cfg, checkpoint_dir=model_dir, use_deepspeed=False)
    model.to(dev)
    model.eval()
    return model


def run(params: dict[str, Any], progress: Any) -> dict[str, Any]:
    import torch

    dev = _audio.device(params.get("device", "cpu"))
    fp16 = params.get("precision") == "fp16" and dev == "cuda"
    if params.get("threads"):
        torch.set_num_threads(int(params["threads"]))
    key = f"xtts:{params['model_dir']}:{dev}"
    if key not in CACHE:
        progress(0.01, "Загружаю модель озвучки")
        CACHE[key] = _load(params["model_dir"], dev, fp16)
    model = CACHE[key]

    speakers: dict[str, Any] = {}
    for spk, refs in params["speaker_refs"].items():
        gpt, emb = model.get_conditioning_latents(audio_path=refs, gpt_cond_len=12, max_ref_length=15)
        speakers[spk] = (gpt, emb)

    items = params["items"]
    durations: dict[str, float] = {}
    lang = params.get("language", "ru")
    for i, item in enumerate(items):
        gpt, emb = speakers[item["speaker"]]
        prosody = item.get("prosody_ref")
        if prosody:
            gpt, _ = model.get_conditioning_latents(
                audio_path=[prosody, *params["speaker_refs"][item["speaker"]]],
                gpt_cond_len=6,
                max_ref_length=20,
            )
        import contextlib

        import numpy as np

        ctx = torch.autocast("cuda", dtype=torch.float16) if fp16 else contextlib.nullcontext()
        pieces = []
        for chunk in split_text(item["text"]):
            with torch.inference_mode(), ctx:
                out = model.inference(
                    chunk,
                    lang,
                    gpt,
                    emb,
                    temperature=float(params.get("temperature", 0.7)),
                    speed=float(item.get("speed", 1.0)),
                    enable_text_splitting=False,
                )
            w = out["wav"]
            pieces.append(w.float().cpu().numpy() if hasattr(w, "cpu") else np.asarray(w))
            pieces.append(np.zeros(int(0.12 * SR), dtype=np.float32))
        wav = _audio.trim_silence(np.concatenate(pieces[:-1]).astype(np.float32), SR)
        _audio.write(item["out"], wav, SR)
        durations[str(item["id"])] = round(len(wav) / SR, 3)
        progress((i + 1) / len(items), f"Озвучено фраз: {i + 1} из {len(items)}")
    if not params.get("keep_loaded"):
        CACHE.pop(key, None)
    return {"durations": durations, "samplerate": SR}
