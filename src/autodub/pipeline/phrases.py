"""Group recognised words into phrases: the unit that is translated and voiced.

A phrase ends at a speaker or language change, at a long pause, or at the end of a sentence
once it is long enough. Whisper sometimes returns long unpunctuated runs, so phrases above a
soft length are cut at the best available boundary (clause punctuation or the widest pause),
and nothing exceeds the hard maximum.
"""

from __future__ import annotations

from autodub.pipeline.models import Phrase, Segment, Word

SENTENCE_END = (".", "!", "?", "…", "。", "！", "？")
CLAUSE_END = (",", ";", ":", "—", "–")
SOFT_MAX = 8.0  # seconds; above this, cut at the next decent boundary
HARD_MAX = 12.0  # never longer: long phrases are hard to fit and clone well
MIN_PHRASE = 1.2  # do not cut on punctuation before this
PAUSE_SPLIT = 0.7  # a pause this long always ends a phrase
SOFT_PAUSE = 0.25  # past SOFT_MAX, a pause this long is enough


def _words(seg: Segment) -> list[Word]:
    if seg.words:
        return seg.words
    # No word timing: spread the words evenly over the segment.
    toks = seg.text.split()
    if not toks:
        return []
    step = (seg.end - seg.start) / len(toks)
    return [
        Word(text=t, start=seg.start + i * step, end=seg.start + (i + 1) * step) for i, t in enumerate(toks)
    ]


def _best_cut(words: list[Word]) -> int:
    """Index after which to cut an overlong run: punctuation first, else the widest pause."""
    n = len(words)
    best, best_score = n // 2, -1.0
    for i in range(1, n - 1):
        length = words[i].end - words[0].start
        if length < MIN_PHRASE * 2 or words[-1].end - words[i + 1].start < MIN_PHRASE:
            continue
        gap = words[i + 1].start - words[i].end
        text = words[i].text.strip()
        score = gap + (2.0 if text.endswith(SENTENCE_END) else 1.0 if text.endswith(CLAUSE_END) else 0)
        score -= abs(length - SOFT_MAX * 0.7) * 0.02  # prefer balanced pieces
        if score > best_score:
            best, best_score = i, score
    return best


def build_phrases(segments: list[Segment], *, default_lang: str) -> list[Phrase]:
    phrases: list[Phrase] = []
    cur: list[Word] = []
    cur_speaker = ""
    cur_lang = default_lang

    def emit(words: list[Word]) -> None:
        text = " ".join(" ".join(w.text.strip() for w in words).split())
        if text:
            phrases.append(
                Phrase(
                    id=len(phrases),
                    start=round(words[0].start, 3),
                    end=round(words[-1].end, 3),
                    speaker=cur_speaker,
                    source=text,
                    lang=cur_lang,
                    keep_original=cur_lang == "ru" and default_lang != "ru",
                )
            )

    def flush() -> None:
        nonlocal cur
        while cur and cur[-1].end - cur[0].start > HARD_MAX and len(cur) > 2:
            cut = _best_cut(cur)
            emit(cur[: cut + 1])
            cur = cur[cut + 1 :]
        if cur:
            emit(cur)
        cur = []

    for seg in segments:
        speaker = seg.speaker or "SPEAKER_00"
        lang = seg.lang or default_lang
        for w in _words(seg):
            if cur:
                pause = w.start - cur[-1].end
                length = cur[-1].end - cur[0].start
                prev = cur[-1].text.strip()
                if (
                    speaker != cur_speaker
                    or lang != cur_lang
                    or pause >= PAUSE_SPLIT
                    or (prev.endswith(SENTENCE_END) and length >= MIN_PHRASE)
                    or (length >= SOFT_MAX and (pause >= SOFT_PAUSE or prev.endswith(CLAUSE_END)))
                ):
                    flush()
                elif w.end - cur[0].start > HARD_MAX and len(cur) > 2:
                    # No boundary found in time: cut the run at its best point, keep the rest.
                    cut = _best_cut(cur)
                    emit(cur[: cut + 1])
                    cur = cur[cut + 1 :]
            if not cur:
                cur_speaker, cur_lang = speaker, lang
            cur.append(w)
    flush()
    return phrases
