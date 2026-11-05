"""Voice / background separation with Demucs (htdemucs family)."""

from __future__ import annotations

from typing import Any

from autodub.engines import _audio
from autodub.engines.worker import CACHE


def run(params: dict[str, Any], progress: Any) -> dict[str, Any]:
    import torch
    from demucs.api import Separator

    dev = _audio.device(params.get("device", "cpu"))
    model = params.get("model", "htdemucs")
    threads = int(params.get("threads") or 0)
    if threads:
        torch.set_num_threads(threads)
    state: dict[str, Any] = {"progress": progress}

    def callback(info: dict[str, Any]) -> None:
        length = float(info.get("audio_length") or 0)
        if not length:
            return
        models = int(info.get("models") or 1)
        idx = int(info.get("model_idx_in_bag") or 0)
        frac = (idx + float(info.get("segment_offset", 0)) / length) / max(models, 1)
        state["progress"](min(frac, 0.99), "Отделяю голос от фона")

    key = f"demucs:{model}:{dev}"
    sep = CACHE.get(key)
    if sep is None:
        sep = Separator(model=model, device=dev, progress=False, callback=callback)
        CACHE[key] = sep
    sep.update_parameter(
        shifts=int(params.get("shifts", 1)),
        segment=float(params["segment"]) if params.get("segment") else None,
        callback=callback,
    )
    wav, sr = _audio.read(params["input"])
    tensor = torch.from_numpy(wav)
    with torch.inference_mode():
        _origin, stems = sep.separate_tensor(tensor, sr)
    vocals = stems["vocals"]
    background = sum(v for k, v in stems.items() if k != "vocals")
    _audio.write(params["vocals_out"], vocals.cpu().numpy(), sep.samplerate)
    _audio.write(params["background_out"], background.cpu().numpy(), sep.samplerate)
    if not params.get("keep_loaded"):
        CACHE.pop(key, None)
    progress(1.0, "Готово")
    return {"samplerate": sep.samplerate, "device": dev}
