"""Compare TTS engines on the same Russian sentences and the same voice reference.

Metrics per engine:
* WER — the synthesized speech is transcribed back with Whisper; lower = clearer, correct stress;
* speaker similarity — cosine of speaker embeddings vs the reference; higher = more alike;
* RTF — synthesis seconds per second of produced audio; lower = faster;
* blind A/B page — docs/tts-compare/index.html with shuffled, unlabeled samples for listening.

    uv run python scripts/compare_tts.py --reference ref.wav --ref-text "..." \
        --engine-python ENV/bin/python --models MODELS_DIR
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from pathlib import Path

import soundfile as sf

from autodub.engines.client import EngineProcess

SENTENCES = [
    "Привет! Сегодня я расскажу, как устроена эта программа.",
    "Мы потратили почти три года, чтобы всё заработало как надо.",
    "Честно говоря, я не ожидал, что это окажется настолько сложно.",
    "Посмотрите внимательно: здесь видно, как меняется картинка.",
    "Если коротко — это лучший результат, который у нас когда-либо был.",
    "Подождите, а вы уверены, что всё правильно подключили?",
    "Ну что ж, давайте попробуем ещё раз, только медленнее.",
    "Это не баг, это особенность, о которой мало кто знает.",
    "Самое интересное начинается, когда включаешь второй режим.",
    "Я до сих пор помню тот день, когда мы впервые запустили сервер.",
    "К сожалению, без интернета эта функция не работает.",
    "Двадцать пятого марта выходит новая версия.",
    "Ой, кажется, я забыл сохранить файл. Сейчас исправлю.",
    "Здесь всего три шага: скачать, установить и запустить.",
    "Вы удивитесь, насколько просто это сделать самостоятельно.",
    "Музыку мы оставили оригинальной, а голос заменили.",
    "Он сказал: «Это невозможно». А потом сделал это сам.",
    "Скорость загрузки зависит от вашего провайдера.",
    "Спасибо, что досмотрели до конца! Увидимся в следующем выпуске.",
    "Ёжик в тумане — мой любимый мультфильм с самого детства.",
]


def norm(t: str) -> list[str]:
    return re.sub(r"[^а-яёa-z0-9 ]", " ", t.lower().replace("ё", "е")).split()


def wer(ref: str, hyp: str) -> float:
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(len(r), 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reference", required=True)
    ap.add_argument("--ref-text", required=True)
    ap.add_argument("--engine-python", required=True)
    ap.add_argument("--models", required=True)
    ap.add_argument("--out", default="docs/tts-compare")
    ap.add_argument("--whisper", default="small")
    ap.add_argument("-n", type=int, default=len(SENTENCES))
    args = ap.parse_args()
    models, out = Path(args.models), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    sents = SENTENCES[: args.n]
    engines = {
        "xtts_v2": (
            "tts_xtts",
            {"model_dir": str(models / "xtts_v2"), "language": "ru", "speaker_refs": {"S": [args.reference]}},
        ),
        "f5_tts_ru": (
            "tts_f5",
            {
                "ckpt_file": str(models / "f5_tts_ru/F5TTS_v1_Base_v2/model_last_inference.safetensors"),
                "vocab_file": str(models / "f5_tts_ru/F5TTS_v1_Base/vocab.txt"),
                "speaker_refs": {"S": args.reference},
                "speaker_texts": {"S": args.ref_text},
                "hf_cache_dir": str(models / "hf"),
            },
        ),
    }
    env = {"HF_HOME": str(models / "hf"), "TORCH_HOME": str(models / "torch"), "COQUI_TOS_AGREED": "1"}
    results: dict[str, dict] = {}
    for name, (task, extra) in engines.items():
        items = [
            {"id": i, "speaker": "S", "text": t, "out": str(out / f"{name}_{i}.wav")}
            for i, t in enumerate(sents)
        ]
        with EngineProcess(args.engine_python, env=env) as eng:
            eng.call(task, {**extra, "items": items[:1], "device": "cpu"})  # warm-up / load
            t0 = time.perf_counter()
            res = eng.call(task, {**extra, "items": items, "device": "cpu", "keep_loaded": True})
            elapsed = time.perf_counter() - t0
            sim = eng.call(
                "similarity",
                {
                    "reference": args.reference,
                    "files": [it["out"] for it in items],
                    "cache_dir": str(models / "hf"),
                },
            )["similarity"]
        audio_s = sum(res["durations"].values())
        results[name] = {
            "rtf": elapsed / audio_s,
            "audio_s": audio_s,
            "similarity": sum(sim.values()) / len(sim),
        }
        print(name, results[name], flush=True)

    from faster_whisper import WhisperModel

    local = models / f"whisper-{args.whisper}"
    wm = WhisperModel(str(local) if local.exists() else args.whisper, device="cpu", compute_type="int8")
    for name in engines:
        errs = []
        for i, t in enumerate(sents):
            segs, _ = wm.transcribe(str(out / f"{name}_{i}.wav"), language="ru", beam_size=5)
            errs.append(wer(t, " ".join(s.text for s in segs)))
        results[name]["wer"] = sum(errs) / len(errs)
        print(name, "WER", round(results[name]["wer"], 3), flush=True)

    (out / "results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), "utf-8")
    # Blind A/B page: shuffled, labels hidden behind a <details>.
    rng = random.Random(7)
    rows, key = [], {}
    for i, t in enumerate(sents):
        names = list(engines)
        rng.shuffle(names)
        key[i] = names
        cells = "".join(
            f'<td><audio controls src="{n}_{i}.wav"></audio><br>{chr(65 + j)}</td>'
            for j, n in enumerate(names)
        )
        rows.append(f"<tr><td>{t}</td>{cells}</tr>")
    answers = "<br>".join(
        f"{i + 1}: " + ", ".join(f"{chr(65 + j)}={n}" for j, n in enumerate(ns)) for i, ns in key.items()
    )
    (out / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Слепое сравнение TTS</title>"
        "<style>body{font-family:sans-serif;max-width:1100px;margin:auto;padding:16px}"
        "td{padding:6px;border-bottom:1px solid #ddd}</style>"
        "<h1>Слепое сравнение озвучки</h1><p>Оцените естественность каждой записи, потом "
        "откройте ответы внизу.</p><p>Референс: <audio controls src=reference.wav></audio></p>"
        f"<table>{''.join(rows)}</table><details><summary>Ответы</summary>{answers}</details>",
        "utf-8",
    )
    data, sr = sf.read(args.reference)
    sf.write(str(out / "reference.wav"), data, sr)


if __name__ == "__main__":
    main()
