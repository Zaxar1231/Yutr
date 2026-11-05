import itertools

import pytest

from autodub.pipeline.timing import MAX_SPEEDUP, MIN_GAP, char_budget, make_slots, over_budget, plan


def _plan(phrases, durations, media_end=100.0):
    slots = make_slots(phrases, media_end)
    return slots, plan(slots, durations)


def assert_no_overlap(p) -> None:
    ps = sorted(p.placements, key=lambda x: x.start)
    for a, b in itertools.pairwise(ps):
        assert a.start + a.duration + MIN_GAP <= b.start + 1e-6


def test_fits_without_change() -> None:
    _, p = _plan([(0, 1, 3), (1, 5, 7)], {0: 1.8, 1: 2.0})
    assert [x.speed for x in p.placements] == [1.0, 1.0]
    assert [x.start for x in p.placements] == [1, 5]


def test_uses_pause_after_phrase_without_speedup() -> None:
    _, p = _plan([(0, 1, 3), (1, 6, 7)], {0: 4.0, 1: 1.0})
    assert p.placements[0].speed == 1.0  # 1 + 4 = 5 < 6 - gap
    assert_no_overlap(p)


def test_speeds_up_at_most_1_2() -> None:
    _, p = _plan([(0, 0, 2), (1, 2.5, 4)], {0: 2.7, 1: 1.0})
    a = p.placements[0]
    assert 1.0 < a.speed <= MAX_SPEEDUP
    assert a.start + a.duration <= 2.5 - MIN_GAP + 1e-6
    assert_no_overlap(p)


def test_borrows_pause_before_when_speedup_not_enough() -> None:
    _, p = _plan([(0, 0, 1), (1, 3, 4), (2, 4.5, 6)], {0: 1, 1: 1.9, 2: 1})
    b = p.placements[1]
    assert b.speed == pytest.approx(MAX_SPEEDUP)
    assert b.start < 3  # started earlier, inside the pause after phrase 0
    assert_no_overlap(p)
    assert not p.needs_shortening


def test_too_long_is_reported_and_never_overlaps() -> None:
    slots, p = _plan([(0, 0, 1), (1, 1.2, 2), (2, 2.2, 3)], {0: 3, 1: 3, 2: 1})
    assert over_budget(slots, {0: 3, 1: 3, 2: 1}) == [0, 1]
    assert set(p.needs_shortening) >= {0, 1}
    assert all(x.speed <= MAX_SPEEDUP + 1e-9 for x in p.placements)
    assert_no_overlap(p)


def test_last_phrase_can_run_to_media_end() -> None:
    _, p = _plan([(0, 10, 11)], {0: 3}, media_end=20)
    assert p.placements[0].speed == 1.0


def test_char_budget() -> None:
    assert char_budget(2.0) == 28
    assert char_budget(0.1) == 8
