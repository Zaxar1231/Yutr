from autodub.pipeline.models import Segment, Word
from autodub.pipeline.phrases import build_phrases


def seg(words, speaker="A", lang=None):
    ws = [Word(text=t, start=s, end=e) for t, s, e in words]
    return Segment(
        start=ws[0].start,
        end=ws[-1].end,
        text=" ".join(t for t, *_ in words),
        speaker=speaker,
        lang=lang,
        words=ws,
    )


def test_split_on_sentence_and_pause_and_speaker() -> None:
    segs = [
        seg([("Hello", 0, 0.5), ("there.", 0.5, 1.4), ("How", 1.5, 1.7), ("are", 1.7, 1.9)]),
        seg([("you?", 1.9, 2.2)]),
        seg([("Fine", 3.5, 3.9)], speaker="B"),
        seg([("thanks", 3.9, 4.3)], speaker="B"),
    ]
    ph = build_phrases(segs, default_lang="en")
    assert [p.source for p in ph] == ["Hello there.", "How are you?", "Fine thanks"]
    assert [p.speaker for p in ph] == ["A", "A", "B"]
    assert ph[0].start == 0 and ph[1].end == 2.2


def test_mixed_language_russian_kept() -> None:
    segs = [seg([("Hi", 0, 0.5)], lang="en"), seg([("Привет", 0.6, 1.0)], lang="ru")]
    ph = build_phrases(segs, default_lang="en")
    assert [p.keep_original for p in ph] == [False, True]


def test_long_speech_is_capped() -> None:
    words = [(f"w{i}", i * 0.5, i * 0.5 + 0.45) for i in range(60)]
    ph = build_phrases([seg(words)], default_lang="en")
    assert all(p.duration <= 12.0 for p in ph) and len(ph) >= 3


def test_unpunctuated_run_is_cut_at_widest_pause() -> None:
    words = []
    t = 0.0
    for i in range(45):
        gap = 0.2 if i == 20 else 0.05  # below the soft-pause threshold
        words.append((f"w{i}", t, t + 0.3))
        t += 0.3 + gap
    ph = build_phrases([seg(words)], default_lang="en")
    assert len(ph) >= 2
    assert ph[0].source.endswith("w20")  # hard max reached: cut at the widest pause
    assert all(p.duration <= 12.0 for p in ph)
