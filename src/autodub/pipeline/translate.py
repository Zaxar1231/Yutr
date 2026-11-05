"""Translation engines.

* ``claude`` — main mode. The model sees the whole transcript (cached prompt prefix), keeps
  terminology and the speaker's register, and fits each line into a character budget derived
  from the phrase duration. It can also shorten lines that still do not fit after synthesis.
* ``madlad`` — offline mode: MADLAD-400-3B through CTranslate2 (Apache-2.0). No length control,
  so timing relies on the speed-up step only.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel

from autodub.core.errors import UserFacingError
from autodub.pipeline.models import Phrase
from autodub.pipeline.timing import char_budget

log = logging.getLogger(__name__)

LANG_NAMES = {
    "en": "английский",
    "de": "немецкий",
    "fr": "французский",
    "es": "испанский",
    "it": "итальянский",
    "pt": "португальский",
    "ja": "японский",
    "zh": "китайский",
    "ko": "корейский",
    "uk": "украинский",
    "pl": "польский",
    "tr": "турецкий",
}


class Translator(Protocol):
    can_shorten: bool

    def translate(
        self, phrases: list[Phrase], *, language: str, progress: Callable[[float, str], None]
    ) -> dict[int, str]: ...

    def shorten(self, items: list[tuple[Phrase, int]], *, language: str) -> dict[int, str]: ...


# --- Claude ---------------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a professional dubbing translator. You translate video speech into \
natural spoken Russian that will be voiced by a TTS model in the original speaker's voice and \
laid over the original timing.

How to translate:
- Write the way a Russian speaker would actually say it out loud, not a literal rendering. \
Keep the speaker's register (casual, technical, ironic), their emotion and emphasis.
- Keep terminology, names and recurring phrases consistent across the whole video. Brand \
names, product names and code identifiers stay as they are.
- Each line has a character budget derived from its duration. Stay within it: a line that \
runs long will be sped up or cut, which sounds bad. If needed, drop filler words and \
rephrase rather than squeezing in every detail.
- Spell out numbers, units and abbreviations the way they should be pronounced \
(e.g. "2024" -> "две тысячи двадцать четвёртый", "km/h" -> "километров в час").
- Use «ё» where it is pronounced. No stage directions, notes or quotes around lines.
- Every id in the request must be answered exactly once.

The full transcript of the video is below so you can keep context and terminology. Lines \
are given as [id] (speaker, seconds) text.

<transcript>
{transcript}
</transcript>"""


class Line(BaseModel):
    id: int
    text: str


class Lines(BaseModel):
    lines: list[Line]


def format_transcript(phrases: list[Phrase]) -> str:
    return "\n".join(f"[{p.id}] ({p.speaker}, {p.duration:.1f}s) {p.source}" for p in phrases)


def build_request(chunk: list[Phrase], language: str) -> str:
    lang = LANG_NAMES.get(language, language)
    rows = [{"id": p.id, "budget_chars": char_budget(p.duration), "text": p.source} for p in chunk]
    return f"Translate these lines from {lang} into Russian. Return one item per id.\n" + json.dumps(
        rows, ensure_ascii=False
    )


def build_shorten_request(items: list[tuple[Phrase, int]]) -> str:
    rows = [
        {"id": p.id, "budget_chars": budget, "current": p.translation, "original": p.source}
        for p, budget in items
    ]
    return (
        "These Russian lines are too long to fit their timing even when sped up. Rewrite each "
        "one shorter so it fits budget_chars, keeping the meaning and tone. Return one item "
        "per id.\n" + json.dumps(rows, ensure_ascii=False)
    )


class ClaudeTranslator:
    can_shorten = True
    CHUNK = 40

    def __init__(self, api_key: str | None, model: str = "claude-opus-5-5", *, client: Any = None):
        if client is None:
            import anthropic

            if not api_key:
                raise UserFacingError(
                    "Не задан ключ Claude API.",
                    hint="Добавьте его в «Настройки → Перевод» или выберите офлайн-перевод.",
                )
            client = anthropic.Anthropic(api_key=api_key, max_retries=4)
        self.client = client
        self.model = model
        self.system: list[dict[str, Any]] = []

    def _ask(self, prompt: str) -> dict[int, str]:
        import anthropic

        try:
            resp = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                # Whole transcript is a stable prefix: cache it across the chunk requests.
                system=self.system,
                messages=[{"role": "user", "content": prompt}],
                output_format=Lines,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError as exc:
            raise UserFacingError("Ключ Claude API не подошёл.", hint="Проверьте ключ в настройках.") from exc
        except anthropic.RateLimitError as exc:
            raise UserFacingError(
                "Превышен лимит запросов к Claude API.", hint="Подождите минуту и продолжите задачу."
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise UserFacingError(
                "Нет связи с Claude API.", hint="Проверьте интернет или включите офлайн-перевод."
            ) from exc
        if getattr(resp, "stop_reason", None) == "refusal":
            raise UserFacingError(
                "Claude отказался переводить этот фрагмент.",
                hint="Попробуйте офлайн-перевод для этого видео.",
            )
        parsed: Lines | None = getattr(resp, "parsed_output", None)
        if parsed is None:
            raise UserFacingError("Не удалось разобрать ответ переводчика.")
        return {line.id: line.text.strip() for line in parsed.lines}

    def translate(
        self, phrases: list[Phrase], *, language: str, progress: Callable[[float, str], None]
    ) -> dict[int, str]:
        self.system = [
            {
                "type": "text",
                "text": SYSTEM_PROMPT.format(transcript=format_transcript(phrases)),
                "cache_control": {"type": "ephemeral"},
            }
        ]
        out: dict[int, str] = {}
        todo = [p for p in phrases if not p.keep_original]
        for i in range(0, len(todo), self.CHUNK):
            chunk = todo[i : i + self.CHUNK]
            got = self._ask(build_request(chunk, language))
            missing = [p for p in chunk if p.id not in got]
            if missing:  # one retry for skipped ids
                got |= self._ask(build_request(missing, language))
            out |= {p.id: got[p.id] for p in chunk if p.id in got}
            progress(min(1.0, (i + len(chunk)) / max(len(todo), 1)), "Перевожу")
        return out

    def shorten(self, items: list[tuple[Phrase, int]], *, language: str) -> dict[int, str]:
        if not items:
            return {}
        return self._ask(build_shorten_request(items))


# --- MADLAD offline ---------------------------------------------------------------------------


class MadladTranslator:
    can_shorten = False

    def __init__(self, model_dir: Path, *, device: str, compute_type: str, threads: int):
        import ctranslate2
        import sentencepiece as spm

        if not (model_dir / "model.bin").exists():
            raise UserFacingError("Модель офлайн-перевода не скачана.", hint="Скачайте её в «Компоненты».")
        self.tr = ctranslate2.Translator(
            str(model_dir), device=device, compute_type=compute_type, intra_threads=threads
        )
        self.sp = spm.SentencePieceProcessor(model_file=str(model_dir / "spiece.model"))

    def translate(
        self, phrases: list[Phrase], *, language: str, progress: Callable[[float, str], None]
    ) -> dict[int, str]:
        todo = [p for p in phrases if not p.keep_original]
        out: dict[int, str] = {}
        batch = 8
        for i in range(0, len(todo), batch):
            chunk = todo[i : i + batch]
            toks = [self.sp.encode("<2ru> " + p.source, out_type=str) for p in chunk]
            res = self.tr.translate_batch(toks, beam_size=2, max_decoding_length=256)
            for p, r in zip(chunk, res, strict=True):
                out[p.id] = self.sp.decode(r.hypotheses[0]).strip()
            progress((i + len(chunk)) / max(len(todo), 1), "Перевожу офлайн")
        return out

    def shorten(self, items: list[tuple[Phrase, int]], *, language: str) -> dict[int, str]:
        return {}


def make_translator(
    name: str,
    *,
    api_key: str | None,
    claude_model: str,
    models_dir: Path,
    device: str,
    compute_type: str,
    threads: int,
) -> Translator:
    if name == "claude":
        return ClaudeTranslator(api_key, claude_model)
    if name == "madlad":
        return MadladTranslator(
            models_dir / "madlad400-3b-ct2-int8", device=device, compute_type=compute_type, threads=threads
        )
    raise ValueError(f"unknown translator {name}")
