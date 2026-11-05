import numpy as np

from autodub.pipeline.models import Phrase, Segment, Word
from autodub.pipeline.stages import assign_speakers, pick_reference, safe_name, srt_time


def test_srt_time() -> None:
    assert srt_time(3725.5) == "01:02:05,500"
    assert srt_time(0.0004) == "00:00:00,000"


def test_safe_name() -> None:
    assert safe_name('a/b:c*d?"e<f>|g.') == "abcdefg"
    assert safe_name("   ") == "video"


def test_assign_speakers_by_overlap() -> None:
    segs = [
        Segment(start=0, end=2, text="a", words=[Word(text="a", start=0, end=2)]),
        Segment(start=3, end=4, text="b"),
    ]
    turns = [{"start": 0, "end": 2.5, "speaker": "A"}, {"start": 2.8, "end": 5, "speaker": "B"}]
    assign_speakers(segs, turns)
    assert [s.speaker for s in segs] == ["A", "B"]


def test_pick_reference_prefers_steady_clean_and_bounds_length() -> None:
    sr = 16000
    rng = np.random.default_rng(0)
    steady = (0.3 * np.sin(np.linspace(0, 2000, sr * 5))).astype(np.float32)
    noisy = (rng.standard_normal(sr * 5) * np.linspace(0, 1, sr * 5)).astype(np.float32)
    clipped = np.clip(steady * 10, -1, 1)
    short = steady[:sr]
    cands = [
        (Phrase(id=i, start=i * 10, end=i * 10 + len(c) / sr, speaker="S", source="x", lang="en"), c)
        for i, c in enumerate([noisy, steady, clipped, short, steady.copy(), steady.copy()])
    ]
    chosen = pick_reference(cands, sr)
    ids = [p.id for p in chosen]
    assert 1 in ids and 3 not in ids and 2 not in ids
    assert 6 <= sum(p.duration for p in chosen) <= 15


def test_soft_cut_prefers_the_gap_between_words() -> None:
    from autodub.pipeline.stages import SR_OUT, soft_cut

    sr = SR_OUT
    word = np.full(int(0.4 * sr), 0.5, dtype=np.float32)
    gap = np.zeros(int(0.15 * sr), dtype=np.float32)
    clip = np.concatenate([word, gap, word, gap, word])  # ~1.5 s
    out = soft_cut(clip, 1.2)
    assert len(out) <= int(1.2 * sr)
    # the cut lands inside the second gap, not in the middle of the third word
    assert int(0.95 * sr) <= len(out) <= int(1.1 * sr)
