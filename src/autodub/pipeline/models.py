"""Data passed between pipeline stages. Everything is JSON-serialisable for the stage cache."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Quality(StrEnum):
    BEST = "best"
    P1080 = "1080"
    P720 = "720"
    AUDIO = "audio"


class MixMode(StrEnum):
    DUB = "dub"  # original voice removed completely
    VOICEOVER = "voiceover"  # original voice quietly under the translation


class Word(BaseModel):
    text: str
    start: float
    end: float
    score: float | None = None


class Segment(BaseModel):
    start: float
    end: float
    text: str
    lang: str | None = None
    speaker: str | None = None
    words: list[Word] = Field(default_factory=list)
    avg_logprob: float | None = None
    no_speech_prob: float | None = None


class Transcript(BaseModel):
    language: str
    segments: list[Segment]


class Phrase(BaseModel):
    """Unit of translation and synthesis: one speaker, one sentence-ish chunk of speech."""

    id: int
    start: float
    end: float
    speaker: str
    source: str
    lang: str
    translation: str = ""
    keep_original: bool = False  # already Russian in a mixed-language video

    @property
    def duration(self) -> float:
        return self.end - self.start


class LanguageVerdict(BaseModel):
    language: str
    confidence: float
    votes: dict[str, float]
    mixed: bool
    skip_translation: bool
