from autodub.engines.tts_f5 import normalize_ru
from autodub.engines.tts_xtts import split_text


def test_split_text_respects_limit_and_keeps_words() -> None:
    text = "Первое предложение довольно длинное. " * 6 + "А это, наконец, финал без точки"
    parts = split_text(text, 80)
    assert all(len(p) <= 80 for p in parts)
    assert " ".join(parts).split() == text.split()
    assert split_text("коротко") == ["коротко"]


def test_normalize_ru_numbers() -> None:
    import pytest

    pytest.importorskip("num2words")  # present in the engine environment only
    out = normalize_ru("В 2024 году было 3,5 миллиона")
    assert "2024" not in out and "3,5" not in out
