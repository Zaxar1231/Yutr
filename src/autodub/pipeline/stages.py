"""Pipeline stages. Each stage is cached in the job's state.json, so a failed or cancelled job
resumes from the first stage whose inputs changed or whose files are missing."""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from autodub.core.errors import UserFacingError
from autodub.engines.client import EngineProcess
from autodub.hardware.guard import check_disk, estimate_job_disk_mb
from autodub.pipeline import langvote, timing
from autodub.pipeline.context import JobContext
from autodub.pipeline.models import (
    LanguageVerdict,
    MixMode,
    Phrase,
    Quality,
    Segment,
    Transcript,
    Word,
)
from autodub.pipeline.phrases import build_phrases
from autodub.pipeline.translate import make_translator

log = logging.getLogger(__name__)
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
SR_OUT = 48000


@dataclass
class Stage:
    name: str
    title: str
    weight: float  # share of total job time, for overall progress / ETA
    run: Callable[[JobContext], dict[str, Any]]
    key: Callable[[JobContext], dict[str, Any]]
    heavy: bool = False  # retried on a lower profile on OOM


class AlreadyRussian(Exception):
    def __init__(self, verdict: LanguageVerdict):
        self.verdict = verdict


# --- download -------------------------------------------------------------------------------

_FORMATS = {
    Quality.BEST: "bv*+ba/b",
    Quality.P1080: "bv*[height<=1080]+ba/b[height<=1080]/b",
    Quality.P720: "bv*[height<=720]+ba/b[height<=720]/b",
    Quality.AUDIO: "ba/b",
}
_PROGRESS = re.compile(r"\[download\]\s+([\d.]+)%")


def _ffmpeg_location(ffmpeg: str) -> str:
    p = Path(ffmpeg)
    return str(p.parent) if p.is_absolute() else ffmpeg


def ytdlp_args(ctx: JobContext, out_tmpl: str) -> list[str]:
    o, s = ctx.options, ctx.settings
    args = [
        *ctx.rt.ytdlp,
        "--no-playlist",
        "--newline",
        "--no-colors",
        "-f",
        _FORMATS[o.quality],
        "--merge-output-format",
        "mp4/mkv",
        "--concurrent-fragments",
        str(s.ytdlp_fragments),
        "--retries",
        "10",
        "--fragment-retries",
        "10",
        "--continue",
        "--write-info-json",
        "--ffmpeg-location",
        _ffmpeg_location(ctx.rt.tools.ffmpeg),
        "-o",
        out_tmpl,
    ]
    if ctx.rt.aria2c and not o.section:
        n = str(s.aria2_connections)
        args += [
            "--downloader",
            ctx.rt.aria2c,
            "--downloader-args",
            f"aria2c:-x{n} -s{n} -k1M --file-allocation=none",
        ]
    if o.cookies_from_browser:
        args += ["--cookies-from-browser", o.cookies_from_browser]
    if o.section:
        a, b = o.section
        args += ["--download-sections", f"*{a}-{b}", "--force-keyframes-at-cuts"]
    args.append(o.url)
    return args


def run_download(ctx: JobContext) -> dict[str, Any]:
    out_dir = ctx.path("download")
    out_dir.mkdir(exist_ok=True)
    # Rough pre-check before downloading anything (1080p ~ 25 MB/min).
    check_disk(out_dir, 1500, what="скачивания видео")
    local = Path(ctx.options.url)
    if local.exists() and local.is_file():  # a file from disk: no download needed
        dst = out_dir / f"source{local.suffix.lower()}"
        if not dst.exists():
            import shutil

            shutil.copy2(local, dst)
        return {
            "video": str(dst.relative_to(ctx.workdir)),
            "title": local.stem,
            "uploader": None,
            "license": None,
        }
    cmd = ytdlp_args(ctx, str(out_dir / "source.%(ext)s"))
    log.info("yt-dlp: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_CREATE_NO_WINDOW,
    )
    tail: list[str] = []
    assert proc.stdout
    for line in proc.stdout:
        tail = [*tail, line.rstrip()][-30:]
        if m := _PROGRESS.search(line):
            ctx.progress(float(m.group(1)) / 100, "Скачиваю видео")
        if ctx.cancelled():
            proc.kill()
            ctx.checkpoint()
    code = proc.wait()
    files = [p for p in out_dir.glob("source.*") if p.suffix not in (".json", ".part", ".ytdl")]
    if code != 0 or not files:
        text = "\n".join(tail)
        log.error("yt-dlp failed:\n%s", text)
        message, hint = _explain_ytdlp(text)
        raise UserFacingError(message, hint=hint, detail=text)
    video = max(files, key=lambda p: p.stat().st_size)
    info_file = next(out_dir.glob("source.info.json"), None)
    info = json.loads(info_file.read_text("utf-8")) if info_file else {}
    return {
        "video": str(video.relative_to(ctx.workdir)),
        "title": info.get("title") or video.stem,
        "uploader": info.get("uploader"),
        "license": info.get("license"),
    }


def _explain_ytdlp(text: str) -> tuple[str, str]:
    t = text.lower()
    if "sign in to confirm" in t or "login required" in t or "private video" in t:
        return (
            "Видео недоступно без входа в аккаунт.",
            "Включите «Cookies из браузера» в настройках и войдите на сайт в этом браузере.",
        )
    if "unsupported url" in t:
        return ("Эта ссылка не поддерживается.", "Проверьте адрес видео.")
    if "http error 404" in t or "not available" in t:
        return ("Видео не найдено или удалено.", "Проверьте ссылку.")
    if "no space left" in t:
        return ("Закончилось место на диске.", "Освободите место и продолжите задачу.")
    return ("Не удалось скачать видео.", "Проверьте ссылку и интернет. Подробности — в журнале.")


# --- audio ----------------------------------------------------------------------------------


def run_audio(ctx: JobContext) -> dict[str, Any]:
    video = ctx.workdir / ctx.result("download")["video"]
    tools = ctx.rt.tools
    duration = tools.duration(video)
    check_disk(ctx.workdir, estimate_job_disk_mb(video.stat().st_size / 2**20, duration), what="обработки")
    tools.extract_audio(video, ctx.path("audio_44k.wav"), sr=44100, channels=2)
    ctx.progress(0.5, "Извлекаю звук")
    tools.extract_audio(video, ctx.path("audio_16k.wav"), sr=16000, channels=1)
    return {"duration": duration, "has_video": tools.has_video(video)}


# --- whisper (in-process, CTranslate2) -----------------------------------------------------


def _whisper(ctx: JobContext) -> Any:
    from faster_whisper import WhisperModel

    s = ctx.settings
    local = ctx.rt.models_dir / f"whisper-{s.whisper_model}"
    model_ref = str(local) if (local / "model.bin").exists() else s.whisper_model
    key = f"whisper:{s.whisper_model}:{s.whisper_compute_type}"
    ctx.guard.check(key, on_gpu=s.whisper_device == "cuda", stage="asr")
    return WhisperModel(
        model_ref,
        device=s.whisper_device,
        compute_type=s.whisper_compute_type,
        cpu_threads=s.cpu_threads,
        download_root=str(ctx.rt.models_dir / "hf"),
    )


def _load16(path: Path, start: float = 0.0, length: float | None = None) -> Any:
    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    a = int(start * sr)
    b = len(data) if length is None else min(len(data), a + int(length * sr))
    return np.asarray(data[a:b], dtype=np.float32)


def run_langid(ctx: JobContext) -> dict[str, Any]:
    duration = ctx.result("audio")["duration"]
    model = _whisper(ctx)
    probes: list[list[tuple[str, float]]] = []
    offsets = langvote.probe_offsets(duration, count=5, window=30)
    for i, off in enumerate(offsets):
        audio = _load16(ctx.path("audio_16k.wav"), off, 30)
        _lang, _p, all_probs = model.detect_language(audio, vad_filter=True)
        probes.append([(lang, float(p)) for lang, p in all_probs[:5]])
        ctx.progress((i + 1) / len(offsets), "Определяю язык")
    verdict = langvote.decide(probes, skip_threshold=ctx.options.skip_threshold)
    log.info("language verdict: %s", verdict)
    return verdict.model_dump()


def run_asr(ctx: JobContext) -> dict[str, Any]:
    from faster_whisper import BatchedInferencePipeline

    verdict = LanguageVerdict.model_validate(ctx.result("langid"))
    s = ctx.settings
    model = _whisper(ctx)
    vocals16 = ctx.path("vocals_16k.wav")
    ctx.rt.tools.extract_audio(ctx.path("vocals.wav"), vocals16, sr=16000, channels=1)
    audio = _load16(vocals16)
    duration = len(audio) / 16000
    if verdict.mixed:
        seg_iter, _info = model.transcribe(audio, multilingual=True, vad_filter=True, beam_size=5)
    elif s.whisper_batch_size > 1:
        seg_iter, _info = BatchedInferencePipeline(model).transcribe(
            audio, language=verdict.language, batch_size=s.whisper_batch_size, beam_size=5
        )
    else:
        seg_iter, _info = model.transcribe(
            audio, language=verdict.language, vad_filter=True, beam_size=5, condition_on_previous_text=False
        )
    segments: list[Segment] = []
    for seg in seg_iter:
        lang = verdict.language
        if verdict.mixed and seg.end - seg.start >= 1.0:
            clip = audio[int(seg.start * 16000) : int(seg.end * 16000)]
            lang, _p, _all = model.detect_language(clip)
        segments.append(
            Segment(
                start=seg.start,
                end=seg.end,
                text=seg.text.strip(),
                lang=lang,
                avg_logprob=seg.avg_logprob,
                no_speech_prob=seg.no_speech_prob,
            )
        )
        ctx.progress(min(0.99, seg.end / max(duration, 1)), "Распознаю речь")
    segments = [x for x in segments if x.text and (x.no_speech_prob or 0) < 0.8]
    tr = Transcript(language=verdict.language, segments=segments)
    ctx.path("transcript.json").write_text(tr.model_dump_json(indent=1), "utf-8")
    return {"segments": len(segments)}


# --- engine-process stages -----------------------------------------------------------------


def engine(ctx: JobContext) -> EngineProcess:
    if not ctx.rt.engine_python:
        raise UserFacingError(
            "Компоненты для нейросетей не установлены.",
            hint="Откройте «Компоненты» и нажмите «Проверить и починить».",
        )
    env = {
        "TORCH_HOME": str(ctx.rt.models_dir / "torch"),
        "HF_HOME": str(ctx.rt.models_dir / "hf"),
        "COQUI_TOS_AGREED": "1",
        # WhisperX sentence splitting; installed by the component manager, never fetched at run time.
        "NLTK_DATA": str(ctx.rt.models_dir / "nltk_data"),
    }
    return EngineProcess(ctx.rt.engine_python, env=env)


def _engine_call(
    ctx: JobContext, task: str, params: dict[str, Any], model_key: str, *, on_gpu: bool
) -> dict[str, Any]:
    ctx.guard.check(model_key, on_gpu=on_gpu, stage=ctx.current_stage)
    with engine(ctx) as eng:
        res = eng.call(task, params, progress=lambda f, m: ctx.progress(f, m), cancelled=ctx.cancelled)
        peaks = eng.last_peaks
    if peaks:
        ctx.guard.record_peak(model_key, vram_mb=peaks.get("peak_vram_mb"), ram_mb=peaks.get("peak_ram_mb"))
    return res


def run_separate(ctx: JobContext) -> dict[str, Any]:
    s = ctx.settings
    _engine_call(
        ctx,
        "separate",
        {
            "input": str(ctx.path("audio_44k.wav")),
            "vocals_out": str(ctx.path("vocals.wav")),
            "background_out": str(ctx.path("background.wav")),
            "model": s.demucs_model,
            "segment": s.demucs_segment,
            "shifts": s.demucs_shifts,
            "device": s.separation_device,
            "threads": s.cpu_threads,
        },
        f"demucs:{s.demucs_model}",
        on_gpu=s.separation_device == "cuda",
    )
    return {}


def run_align(ctx: JobContext) -> dict[str, Any]:
    tr = Transcript.model_validate_json(ctx.path("transcript.json").read_text("utf-8"))
    s = ctx.settings
    by_lang: dict[str, list[int]] = {}
    for i, seg in enumerate(tr.segments):
        by_lang.setdefault(seg.lang or tr.language, []).append(i)
    for lang, idxs in by_lang.items():
        try:
            res = _engine_call(
                ctx,
                "align",
                {
                    "audio": str(ctx.path("vocals_16k.wav")),
                    "language": lang,
                    "segments": [
                        {
                            "start": tr.segments[i].start,
                            "end": tr.segments[i].end,
                            "text": tr.segments[i].text,
                        }
                        for i in idxs
                    ],
                    "device": s.align_device,
                    "models_dir": str(ctx.rt.models_dir / "align"),
                },
                "align:wav2vec2",
                on_gpu=s.align_device == "cuda",
            )
        except Exception as exc:
            if getattr(exc, "oom", False):
                raise
            # No alignment model for this language: keep segment-level timing.
            log.warning("alignment for %s failed: %s", lang, exc)
            continue
        for i, seg in zip(idxs, res["segments"], strict=False):
            tr.segments[i].words = [Word.model_validate(w) for w in seg["words"]]
    ctx.path("transcript_aligned.json").write_text(tr.model_dump_json(indent=1), "utf-8")
    return {}


def run_diarize(ctx: JobContext) -> dict[str, Any]:
    tr = Transcript.model_validate_json(ctx.path("transcript_aligned.json").read_text("utf-8"))
    token = ctx.rt.secrets("hf_token") if ctx.options.diarize else None
    if ctx.options.diarize and not token:
        ctx.notify("Для разделения по спикерам нужен токен Hugging Face. Все фразы озвучены одним голосом.")
    if token:
        res = _engine_call(
            ctx,
            "diarize",
            {
                "audio": str(ctx.path("vocals_16k.wav")),
                "token": token,
                "device": ctx.settings.tts_device,
                "num_speakers": ctx.options.num_speakers,
                "cache_dir": str(ctx.rt.models_dir / "hf"),
            },
            "diarize:pyannote-community-1",
            on_gpu=ctx.settings.tts_device == "cuda",
        )
        assign_speakers(tr.segments, res["turns"])
    else:
        for seg in tr.segments:
            seg.speaker = "SPEAKER_00"
    ctx.path("transcript_speakers.json").write_text(tr.model_dump_json(indent=1), "utf-8")
    return {"speakers": sorted({s.speaker or "" for s in tr.segments})}


def assign_speakers(segments: list[Segment], turns: list[dict[str, Any]]) -> None:
    """Speaker of each word = diarization turn with the largest overlap; segment = majority."""

    def best(a: float, b: float) -> str | None:
        scores: dict[str, float] = {}
        for t in turns:
            ov = min(b, t["end"]) - max(a, t["start"])
            if ov > 0:
                scores[t["speaker"]] = scores.get(t["speaker"], 0) + ov
        return max(scores, key=lambda k: scores[k]) if scores else None

    last = "SPEAKER_00"
    for seg in segments:
        votes: dict[str, float] = {}
        for w in seg.words or []:
            spk = best(w.start, w.end)
            if spk:
                votes[spk] = votes.get(spk, 0) + (w.end - w.start)
        spk = max(votes, key=lambda k: votes[k]) if votes else best(seg.start, seg.end)
        seg.speaker = spk or last
        last = seg.speaker


def run_phrases(ctx: JobContext) -> dict[str, Any]:
    tr = Transcript.model_validate_json(ctx.path("transcript_speakers.json").read_text("utf-8"))
    phrases = build_phrases(tr.segments, default_lang=tr.language)
    _save_phrases(ctx, phrases)
    return {"count": len(phrases)}


def _load_phrases(ctx: JobContext, name: str = "phrases.json") -> list[Phrase]:
    return [Phrase.model_validate(p) for p in json.loads(ctx.path(name).read_text("utf-8"))]


def _save_phrases(ctx: JobContext, phrases: list[Phrase], name: str = "phrases.json") -> None:
    ctx.path(name).write_text(
        json.dumps([p.model_dump() for p in phrases], ensure_ascii=False, indent=1), "utf-8"
    )


def _translator(ctx: JobContext) -> Any:
    s = ctx.settings
    return make_translator(
        ctx.options.translator,
        api_key=ctx.rt.secrets("anthropic_api_key"),
        claude_model=ctx.options.claude_model,
        models_dir=ctx.rt.models_dir,
        device=s.translate_offline_device,
        compute_type=s.translate_offline_compute_type,
        threads=s.cpu_threads,
    )


def run_translate(ctx: JobContext) -> dict[str, Any]:
    phrases = _load_phrases(ctx)
    lang = ctx.result("langid")["language"]
    tr = _translator(ctx)
    got = tr.translate(phrases, language=lang, progress=lambda f, m: ctx.progress(f, m))
    for p in phrases:
        p.translation = p.source if p.keep_original else got.get(p.id, "")
    _save_phrases(ctx, phrases, "phrases_ru.json")
    return {"translated": sum(1 for p in phrases if p.translation)}


# --- references -----------------------------------------------------------------------------


def _rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x**2)) + 1e-9))


def pick_reference(
    candidates: list[tuple[Phrase, np.ndarray]], sr: int, *, min_total: float = 6.0, max_total: float = 15.0
) -> list[Phrase]:
    """Best clean phrases of one speaker: long enough, steady loudness, not clipped."""
    scored = []
    for p, clip in candidates:
        d = len(clip) / sr
        if d < 2.0 or d > 12.0:
            continue
        frames = clip[: len(clip) // 2048 * 2048].reshape(-1, 2048)
        levels = 20 * np.log10(np.sqrt((frames**2).mean(axis=1)) + 1e-9)
        voiced = levels[levels > levels.max() - 30]
        steadiness = -float(np.std(voiced)) if len(voiced) else -99
        loud = _rms_db(clip)
        clipped = float(np.mean(np.abs(clip) > 0.99))
        score = steadiness + min(loud + 30, 10) - clipped * 200 + min(d, 8)
        scored.append((score, p))
    scored.sort(key=lambda t: -t[0])
    chosen, total = [], 0.0
    for _score, p in scored:
        if total >= min_total and total + p.duration > max_total:
            continue
        chosen.append(p)
        total += p.duration
        if total >= max_total * 0.8:
            break
    return sorted(chosen, key=lambda p: p.start)


def run_refs(ctx: JobContext) -> dict[str, Any]:
    phrases = _load_phrases(ctx, "phrases_ru.json")
    vocals, sr = sf.read(str(ctx.path("vocals.wav")), dtype="float32", always_2d=True)
    mono = vocals.mean(axis=1)
    refs_dir = ctx.path("refs")
    refs_dir.mkdir(exist_ok=True)

    def clip(p: Phrase, pad: float = 0.05) -> Any:
        a = max(0, int((p.start - pad) * sr))
        return mono[a : int((p.end + pad) * sr)]

    speakers: dict[str, dict[str, Any]] = {}
    for spk in sorted({p.speaker for p in phrases}):
        cands = [(p, clip(p)) for p in phrases if p.speaker == spk]
        chosen = pick_reference(cands, sr) or sorted((p for p, _ in cands), key=lambda p: -p.duration)[:3]
        gap = np.zeros(int(0.25 * sr), dtype=np.float32)
        parts: list[np.ndarray] = []
        for p in chosen:
            parts += [clip(p), gap]
        ref = np.concatenate(parts) if parts else np.zeros(sr, dtype=np.float32)
        ref = ref / (np.abs(ref).max() + 1e-6) * 0.9
        path = refs_dir / f"{spk}.wav"
        sf.write(str(path), ref, sr, subtype="FLOAT")
        speakers[spk] = {
            "wav": str(path.relative_to(ctx.workdir)),
            "text": " ".join(p.source for p in chosen),
            "seconds": round(len(ref) / sr, 1),
        }
    # Per-phrase clips carry the original intonation into the synthesis.
    for p in phrases:
        if p.duration >= 2.0 and not p.keep_original:
            sf.write(str(refs_dir / f"phrase_{p.id}.wav"), clip(p), sr, subtype="FLOAT")
    return {"speakers": speakers}


# --- tts ------------------------------------------------------------------------------------


def _tts_items(
    ctx: JobContext, phrases: list[Phrase], speeds: dict[int, float], out_dir: Path
) -> list[dict[str, Any]]:
    items = []
    for p in phrases:
        if p.keep_original or not p.translation:
            continue
        item: dict[str, Any] = {
            "id": p.id,
            "speaker": p.speaker,
            "text": p.translation,
            "out": str(out_dir / f"{p.id}.wav"),
            "speed": speeds.get(p.id, 1.0),
        }
        prosody = ctx.path("refs") / f"phrase_{p.id}.wav"
        if ctx.options.prosody_from_phrase and prosody.exists():
            item["prosody_ref"] = str(prosody)
            item["prosody_text"] = p.source
        items.append(item)
    return items


def synthesize(
    ctx: JobContext, phrases: list[Phrase], *, speeds: dict[int, float] | None = None, out_dir: Path
) -> dict[int, float]:
    out_dir.mkdir(exist_ok=True)
    s = ctx.settings
    engine_name = ctx.options.tts_engine or s.tts_model
    speakers = ctx.result("refs")["speakers"]
    items = _tts_items(ctx, phrases, speeds or {}, out_dir)
    if not items:
        return {}
    common: dict[str, Any] = {
        "items": items,
        "device": s.tts_device,
        "precision": s.tts_precision,
        "threads": s.cpu_threads,
        "keep_loaded": s.keep_models_loaded,
        "speaker_refs": {k: [str(ctx.workdir / v["wav"])] for k, v in speakers.items()},
    }
    if engine_name == "xtts_v2":
        params = common | {"model_dir": str(ctx.rt.models_dir / "xtts_v2"), "language": "ru"}
        res = _engine_call(ctx, "tts_xtts", params, "tts:xtts_v2", on_gpu=s.tts_device == "cuda")
    elif engine_name == "f5_tts_ru":
        f5 = ctx.rt.models_dir / "f5_tts_ru"
        params = common | {
            "ckpt_file": str(f5 / "F5TTS_v1_Base_v2" / "model_last_inference.safetensors"),
            "vocab_file": str(f5 / "F5TTS_v1_Base" / "vocab.txt"),
            "speaker_refs": {k: str(ctx.workdir / v["wav"]) for k, v in speakers.items()},
            "speaker_texts": {k: v["text"] for k, v in speakers.items()},
            "hf_cache_dir": str(ctx.rt.models_dir / "hf"),
        }
        res = _engine_call(ctx, "tts_f5", params, "tts:f5_tts_ru", on_gpu=s.tts_device == "cuda")
    else:
        raise UserFacingError(f"Неизвестная модель озвучки: {engine_name}")
    return {int(k): float(v) for k, v in res["durations"].items()}


def run_tts(ctx: JobContext) -> dict[str, Any]:
    phrases = _load_phrases(ctx, "phrases_ru.json")
    durations = synthesize(ctx, phrases, out_dir=ctx.path("tts"))
    return {"durations": {str(k): v for k, v in durations.items()}}


# --- fit ------------------------------------------------------------------------------------


def run_fit(ctx: JobContext) -> dict[str, Any]:
    phrases = _load_phrases(ctx, "phrases_ru.json")
    by_id = {p.id: p for p in phrases}
    durations = {int(k): v for k, v in ctx.result("tts")["durations"].items()}
    media_end = ctx.result("audio")["duration"]
    voiced = [p for p in phrases if p.id in durations]
    slots = timing.make_slots([(p.id, p.start, p.end) for p in voiced], media_end)
    clip_dir = ctx.path("tts")
    shortened: list[int] = []

    tr = None
    for round_no in range(2):  # LLM shortening, at most two rounds
        long_ids = timing.over_budget(slots, durations)
        if not long_ids:
            break
        tr = tr or _translator(ctx)
        if not tr.can_shorten:
            break
        ctx.progress(0.1 + round_no * 0.3, f"Сокращаю длинные фразы: {len(long_ids)}")
        rooms = {s.id: s.limit - s.start for s in slots}
        req = [(by_id[i], timing.char_budget(rooms[i] * timing.MAX_SPEEDUP * 0.95)) for i in long_ids]
        new = tr.shorten(req, language=ctx.result("langid")["language"])
        changed = [by_id[i] for i in long_ids if new.get(i)]
        for p in changed:
            p.translation = new[p.id]
        if not changed:
            break
        shortened += [p.id for p in changed]
        fix_dir = ctx.path(f"tts_fix{round_no}")
        durations |= synthesize(ctx, changed, out_dir=fix_dir)
        for p in changed:
            (fix_dir / f"{p.id}.wav").replace(clip_dir / f"{p.id}.wav")
    _save_phrases(ctx, phrases, "phrases_final.json")

    plan = timing.plan(slots, durations)
    fit_dir = ctx.path("fit")
    fit_dir.mkdir(exist_ok=True)
    for i, pl in enumerate(plan.placements):
        ctx.rt.tools.stretch(clip_dir / f"{pl.id}.wav", fit_dir / f"{pl.id}.wav", speed=pl.speed, sr=SR_OUT)
        ctx.progress(0.7 + 0.3 * (i + 1) / len(plan.placements), "Подгоняю тайминг")
    sped = sum(1 for p in plan.placements if p.speed > 1.001)
    if plan.needs_shortening:
        log.warning("phrases cut at the end of their slot: %s", plan.needs_shortening)
    return {
        "placements": [p.__dict__ for p in plan.placements],
        "shortened": shortened,
        "sped_up": sped,
        "truncated": plan.needs_shortening,
    }


# --- mix ------------------------------------------------------------------------------------


def _fade(x: np.ndarray, ms: float = 15) -> np.ndarray:
    n = min(len(x) // 2, int(SR_OUT * ms / 1000))
    if n > 0:
        ramp = np.linspace(0, 1, n, dtype=np.float32)
        x[:n] *= ramp
        x[-n:] *= ramp[::-1]
    return x


def soft_cut(clip: Any, keep_s: float, sr: int = SR_OUT, search_s: float = 1.2) -> Any:
    """Cut a clip that does not fit at the quietest point (a gap between words) before ``keep_s``.

    Used only as the last resort, so speech is never chopped mid-word.
    """
    n = int(keep_s * sr)
    if len(clip) <= n:
        return clip
    win = int(0.03 * sr)
    lo = max(win, n - int(search_s * sr))
    best, best_e = n, float("inf")
    for i in range(lo, n - win, win // 2):
        e = float(np.mean(clip[i : i + win] ** 2))
        if e <= best_e:  # latest quiet point wins: keep as much speech as possible
            best, best_e = i + win // 2, e
    return clip[:best]


def run_mix(ctx: JobContext) -> dict[str, Any]:
    tools = ctx.rt.tools
    duration = ctx.result("audio")["duration"]
    n = int(duration * SR_OUT) + SR_OUT
    voice = np.zeros(n, dtype=np.float32)
    for pl in ctx.result("fit")["placements"]:
        clip, _sr = sf.read(str(ctx.path("fit") / f"{pl['id']}.wav"), dtype="float32")
        clip = _fade(soft_cut(clip, pl["duration"]).copy())
        a = int(pl["start"] * SR_OUT)
        voice[a : a + len(clip)] += clip[: max(0, n - a)]
    # Mixed videos: Russian phrases keep the original voice.
    phrases = _load_phrases(ctx, "phrases_final.json")
    keep = [p for p in phrases if p.keep_original]
    orig_vocals = ctx.path("vocals_48k.wav")
    tools.extract_audio(ctx.path("vocals.wav"), orig_vocals, sr=SR_OUT, channels=1)
    ov, _ = sf.read(str(orig_vocals), dtype="float32")
    for p in keep:
        a, b = int(p.start * SR_OUT), int(p.end * SR_OUT)
        voice[a:b] += ov[a:b]
    sf.write(str(ctx.path("voice_ru.wav")), voice[: int(duration * SR_OUT)], SR_OUT, subtype="FLOAT")

    bg = str(ctx.path("background.wav"))
    out = ctx.path("dub_mix.wav")
    vol = ctx.options.original_volume
    if ctx.options.mode == MixMode.VOICEOVER:
        # Original voice under the translation, ducked further while the dub speaks.
        filt = (
            f"[0:a]aresample={SR_OUT},pan=stereo|c0=c0|c1=c0,asplit=2[v][vsc];"
            f"[2:a]aresample={SR_OUT},volume={vol:.3f}[o];"
            "[o][vsc]sidechaincompress=threshold=0.02:ratio=6:attack=20:release=400[od];"
            f"[1:a]aresample={SR_OUT}[b];"
            "[v][b][od]amix=inputs=3:normalize=0,"
            "loudnorm=I=-16:TP=-1.5:LRA=11[out]"
        )
        inputs = ["-i", str(ctx.path("voice_ru.wav")), "-i", bg, "-i", str(ctx.path("vocals.wav"))]
    else:
        filt = (
            f"[0:a]aresample={SR_OUT},pan=stereo|c0=c0|c1=c0[v];[1:a]aresample={SR_OUT}[b];"
            "[v][b]amix=inputs=2:normalize=0,loudnorm=I=-16:TP=-1.5:LRA=11[out]"
        )
        inputs = ["-i", str(ctx.path("voice_ru.wav")), "-i", bg]
    tools.run(
        [
            *inputs,
            "-filter_complex",
            filt,
            "-map",
            "[out]",
            "-ar",
            str(SR_OUT),
            "-c:a",
            "pcm_f32le",
            str(out),
        ],
        what="mix",
    )
    ctx.progress(1.0, "Свожу звук")
    return {}


# --- mux ------------------------------------------------------------------------------------


def srt_time(t: float) -> str:
    ms = round(t * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(path: Path, rows: list[tuple[float, float, str]]) -> None:
    lines = []
    for i, (a, b, text) in enumerate(rows, 1):
        lines += [str(i), f"{srt_time(a)} --> {srt_time(b)}", text.strip(), ""]
    path.write_text("\n".join(lines), "utf-8")


ISO3 = {
    "en": "eng",
    "ru": "rus",
    "de": "deu",
    "fr": "fra",
    "es": "spa",
    "it": "ita",
    "ja": "jpn",
    "zh": "zho",
    "ko": "kor",
    "uk": "ukr",
    "pt": "por",
    "pl": "pol",
    "tr": "tur",
}


def safe_name(title: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title).strip().rstrip(".")
    return (name or "video")[:120]


def run_mux(ctx: JobContext) -> dict[str, Any]:
    tools = ctx.rt.tools
    dl = ctx.result("download")
    video = ctx.workdir / dl["video"]
    lang = ctx.result("langid")["language"]
    phrases = _load_phrases(ctx, "phrases_final.json")
    placements = {p["id"]: p for p in ctx.result("fit")["placements"]}
    out_dir = Path(ctx.options.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = safe_name(dl["title"]) + " [RU]"
    srt_ru, srt_src = out_dir / f"{base}.ru.srt", out_dir / f"{base}.{lang}.srt"
    write_srt(
        srt_ru,
        [
            (
                placements[p.id]["start"],
                placements[p.id]["start"] + placements[p.id]["duration"],
                p.translation,
            )
            if p.id in placements
            else (p.start, p.end, p.translation)
            for p in phrases
            if p.translation
        ],
    )
    write_srt(srt_src, [(p.start, p.end, p.source) for p in phrases])

    has_video = ctx.result("audio")["has_video"]
    vcodec = next(
        (s.get("codec_name") for s in tools.probe(video)["streams"] if s.get("codec_type") == "video"), None
    )
    mp4_ok = vcodec in ("h264", "hevc", "av1", None)
    ext = ".mp4" if mp4_ok else ".mkv"
    out = out_dir / (base + (ext if has_video else ".m4a"))
    sub_codec = "mov_text" if ext == ".mp4" else "srt"
    lang3 = ISO3.get(lang, "und")
    args = ["-i", str(ctx.path("dub_mix.wav")), "-i", str(video), "-i", str(srt_ru), "-i", str(srt_src)]
    maps = (["-map", "1:v:0?"] if has_video else []) + ["-map", "0:a:0", "-map", "1:a:0"]
    if has_video:
        maps += ["-map", "2:0", "-map", "3:0", "-c:s", sub_codec]
    args += [
        *maps,
        "-c:v",
        "copy",  # never re-encode the picture
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-metadata:s:a:0",
        "language=rus",
        "-metadata:s:a:0",
        "title=Русский (дубляж)",
        "-metadata:s:a:1",
        f"language={lang3}",
        "-metadata:s:a:1",
        "title=Оригинал",
        "-disposition:a:0",
        "default",
        "-disposition:a:1",
        "0",
    ]
    if has_video:
        args += [
            "-metadata:s:s:0",
            "language=rus",
            "-metadata:s:s:1",
            f"language={lang3}",
            "-disposition:s:0",
            "0",
            "-disposition:s:1",
            "0",
        ]
    if ext == ".mp4":
        args += ["-movflags", "+faststart"]
    args.append(str(out))
    tools.run(args, what="mux")
    ctx.progress(1.0, "Собираю видео")
    return {"output": str(out), "srt_ru": str(srt_ru), "srt_src": str(srt_src)}


# --- registry -------------------------------------------------------------------------------


def _profile_key(ctx: JobContext, *fields: str) -> dict[str, Any]:
    return {f: getattr(ctx.settings, f) for f in fields}


STAGES: list[Stage] = [
    Stage(
        "download",
        "Скачивание",
        0.06,
        run_download,
        lambda c: {"url": c.options.url, "q": c.options.quality, "sec": c.options.section},
    ),
    Stage("audio", "Подготовка звука", 0.01, run_audio, lambda c: {}),
    Stage("langid", "Определение языка", 0.02, run_langid, lambda c: {}, heavy=True),
    Stage(
        "separate",
        "Отделение голоса от фона",
        0.14,
        run_separate,
        lambda c: _profile_key(c, "demucs_model", "demucs_segment", "demucs_shifts"),
        heavy=True,
    ),
    Stage(
        "asr",
        "Распознавание речи",
        0.10,
        run_asr,
        lambda c: _profile_key(c, "whisper_model", "whisper_compute_type"),
        heavy=True,
    ),
    Stage("align", "Выравнивание по словам", 0.04, run_align, lambda c: {"v": 2}, heavy=True),
    Stage(
        "diarize",
        "Разделение по спикерам",
        0.04,
        run_diarize,
        lambda c: {"on": c.options.diarize, "n": c.options.num_speakers},
        heavy=True,
    ),
    Stage("phrases", "Разбивка на фразы", 0.001, run_phrases, lambda c: {"v": 2}),
    Stage(
        "translate",
        "Перевод",
        0.05,
        run_translate,
        lambda c: {"t": c.options.translator, "m": c.options.claude_model},
        heavy=True,
    ),
    Stage("refs", "Образцы голоса", 0.01, run_refs, lambda c: {"v": 1}),
    Stage(
        "tts",
        "Озвучка",
        0.45,
        run_tts,
        lambda c: {
            "e": c.options.tts_engine or c.settings.tts_model,
            "p": c.options.prosody_from_phrase,
            "v": 2,
        },
        heavy=True,
    ),
    Stage("fit", "Подгонка тайминга", 0.04, run_fit, lambda c: {"v": 1}),
    Stage(
        "mix",
        "Сведение",
        0.02,
        run_mix,
        lambda c: {"mode": c.options.mode, "vol": c.options.original_volume, "v": 2},
    ),
    Stage("mux", "Сборка видео", 0.02, run_mux, lambda c: {"out": c.options.output_dir}),
]
