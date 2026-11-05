from autodub.pipeline.langvote import decide, probe_offsets


def test_offsets_spread_and_inside_video() -> None:
    offs = probe_offsets(600, count=5, window=30)
    assert len(offs) == 5 and offs == sorted(offs)
    assert offs[0] > 0 and offs[-1] <= 570
    assert probe_offsets(20) == [0.0]
    assert len(probe_offsets(70, count=5)) == 2


def test_english_video() -> None:
    v = decide([[("en", 0.97), ("de", 0.01)]] * 5)
    assert v.language == "en" and not v.skip_translation and not v.mixed


def test_russian_video_is_skipped() -> None:
    v = decide([[("ru", 0.95), ("uk", 0.03)]] * 4 + [[("ru", 0.7), ("uk", 0.25)]])
    assert v.language == "ru" and v.skip_translation and v.confidence > 0.8


def test_uncertain_russian_is_not_skipped() -> None:
    v = decide([[("ru", 0.55), ("uk", 0.4)]] * 5)
    assert v.language == "ru" and not v.skip_translation


def test_one_noisy_window_does_not_flip() -> None:
    v = decide([[("en", 0.9)]] * 4 + [[("ru", 0.99)]])
    assert v.language == "en" and v.mixed  # 1 of 5 windows Russian = 20 %


def test_mixed_russian_english() -> None:
    v = decide([[("ru", 0.9)], [("en", 0.9)], [("ru", 0.9)], [("en", 0.95)]])
    assert v.mixed and not v.skip_translation


def test_empty_probes() -> None:
    assert decide([]).confidence == 0.0
