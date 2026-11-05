"""Language decision from several probes taken across the video.

Each probe is faster-whisper's ``detect_language`` on a short window. Probes are combined by
summing probabilities (soft voting), so one noisy window cannot flip the result.
"""

from __future__ import annotations

from collections.abc import Sequence

from autodub.pipeline.models import LanguageVerdict

RUSSIAN = "ru"


def probe_offsets(duration: float, *, count: int = 5, window: float = 30.0) -> list[float]:
    """Start times of ``count`` windows spread over the video, skipping intro and outro."""
    if duration <= window:
        return [0.0]
    count = max(1, min(count, int(duration // window)))
    usable_start = min(duration * 0.05, 20.0)
    usable_end = duration - window
    if count == 1 or usable_end <= usable_start:
        return [max(0.0, (duration - window) / 2)]
    step = (usable_end - usable_start) / (count - 1)
    return [round(usable_start + i * step, 2) for i in range(count)]


def decide(
    probes: Sequence[Sequence[tuple[str, float]]],
    *,
    skip_threshold: float = 0.8,
    mixed_threshold: float = 0.2,
) -> LanguageVerdict:
    """Combine per-window language probabilities.

    ``probes``: for each window, a list of (language, probability) as returned by Whisper.
    A video is *mixed* when a second language holds at least ``mixed_threshold`` of the votes;
    then languages are decided per segment later. Translation is skipped only when the video
    is Russian with confidence above ``skip_threshold`` and not mixed.
    """
    votes: dict[str, float] = {}
    winners: dict[str, int] = {}
    valid = [p for p in probes if p]
    for probe in valid:
        for lang, prob in probe:
            votes[lang] = votes.get(lang, 0.0) + float(prob)
        top = max(probe, key=lambda lp: lp[1])[0]
        winners[top] = winners.get(top, 0) + 1
    if not votes:
        return LanguageVerdict(language="en", confidence=0.0, votes={}, mixed=False, skip_translation=False)
    total = sum(votes.values())
    shares = {k: v / total for k, v in sorted(votes.items(), key=lambda kv: -kv[1])}
    language, confidence = next(iter(shares.items()))
    # Mixed if another language wins whole windows, not just leaks a bit of probability.
    second_windows = sorted(winners.values(), reverse=True)[1:2]
    mixed = bool(second_windows) and second_windows[0] / len(valid) >= mixed_threshold
    skip = language == RUSSIAN and confidence >= skip_threshold and not mixed
    return LanguageVerdict(
        language=language,
        confidence=round(confidence, 3),
        votes={k: round(v, 3) for k, v in list(shares.items())[:5]},
        mixed=mixed,
        skip_translation=skip,
    )
