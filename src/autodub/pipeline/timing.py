"""Fitting synthesized phrases into the original timing.

Rules (from the spec):
1. a phrase longer than its slot is first shortened by the translator (LLM);
2. then sped up with rubberband, at most 1.2x — no "chipmunk" voice;
3. phrases never overlap.

A phrase's *slot* runs from its own start to the start of the next phrase (minus a small gap),
so a phrase may use the pause after it. If even 1.2x does not fit, the phrase is allowed to
start a little earlier into the pause before it; anything still left over is resolved by
pushing the next phrases later only within their own pauses. As a last resort the clip is
cut at a word-safe fade-out — this is reported so it can be seen in the log.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_SPEEDUP = 1.2
MIN_GAP = 0.08  # seconds of silence kept between two phrases
# Russian speech rate used to budget translations: ~14 characters per second is natural,
# 16-17 is fast but still comfortable.
CHARS_PER_SECOND = 14.0


@dataclass
class Slot:
    id: int
    start: float  # original phrase start
    end: float  # original phrase end
    limit: float  # latest allowed end (next phrase start - gap, or media end)
    earliest: float  # earliest allowed start (previous phrase end + gap)


@dataclass
class Placement:
    id: int
    start: float
    speed: float  # 1.0 = unchanged, >1 faster
    duration: float  # after speed change
    truncated: float = 0.0  # seconds cut off as a last resort


@dataclass
class FitPlan:
    placements: list[Placement]
    needs_shortening: list[int] = field(default_factory=list)


def make_slots(phrases: list[tuple[int, float, float]], media_end: float) -> list[Slot]:
    """``phrases``: (id, start, end) sorted by start."""
    slots = []
    for i, (pid, start, end) in enumerate(phrases):
        nxt = phrases[i + 1][1] if i + 1 < len(phrases) else media_end
        prev_end = phrases[i - 1][2] if i > 0 else 0.0
        limit = max(end, nxt - MIN_GAP) if i + 1 < len(phrases) else max(end, media_end)
        earliest = min(start, prev_end + MIN_GAP) if i > 0 else max(0.0, start - 0.3)
        slots.append(Slot(pid, start, end, limit, earliest))
    return slots


def char_budget(duration: float, *, rate: float = CHARS_PER_SECOND) -> int:
    """How many characters of Russian fit in ``duration`` seconds at a natural pace."""
    return max(8, int(duration * rate))


def over_budget(slots: list[Slot], durations: dict[int, float]) -> list[int]:
    """Phrases that would need more than MAX_SPEEDUP to fit their slot: ask for a shorter text."""
    out = []
    for s in slots:
        room = s.limit - s.start
        if durations[s.id] > room * MAX_SPEEDUP + 1e-6:
            out.append(s.id)
    return out


def plan(slots: list[Slot], durations: dict[int, float]) -> FitPlan:
    """Place every clip without overlaps. ``durations``: synthesized length at speed 1.0."""
    placements: list[Placement] = []
    needs: list[int] = []
    cursor = 0.0  # end of the previous placed clip + gap
    for s in slots:
        dur = durations[s.id]
        start = max(s.start, cursor)
        room = s.limit - start
        speed = 1.0
        if dur > room:
            speed = min(MAX_SPEEDUP, dur / max(room, 1e-6))
        new_dur = dur / speed
        if start + new_dur > s.limit:
            # Use the pause before the phrase, but never overlap the previous clip.
            earliest = max(s.earliest, cursor)
            start = max(earliest, s.limit - new_dur)
        truncated = 0.0
        if start + new_dur > s.limit:
            needs.append(s.id)
            truncated = start + new_dur - s.limit
            new_dur = s.limit - start
        placements.append(
            Placement(s.id, round(start, 3), round(speed, 3), round(new_dur, 3), round(truncated, 3))
        )
        cursor = start + new_dur + MIN_GAP
    return FitPlan(placements, needs)
