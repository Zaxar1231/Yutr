from types import SimpleNamespace

import pytest

from autodub.core.errors import UserFacingError
from autodub.pipeline.models import Phrase
from autodub.pipeline.translate import (
    ClaudeTranslator,
    Line,
    Lines,
    build_request,
    build_shorten_request,
    format_transcript,
)


def ph(i, text, start=0.0, end=2.0, keep=False):
    return Phrase(id=i, start=start, end=end, speaker="S", source=text, lang="en", keep_original=keep)


class FakeMessages:
    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def parse(self, **kw):
        self.calls.append(kw)
        ans = self.answers.pop(0)
        return SimpleNamespace(
            stop_reason="end_turn", parsed_output=Lines(lines=[Line(id=i, text=t) for i, t in ans])
        )


def client(answers):
    msgs = FakeMessages(answers)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def test_translate_sends_whole_transcript_cached_and_budgets() -> None:
    c, msgs = client([[(0, " Привет. "), (1, "Пока")]])
    tr = ClaudeTranslator(None, client=c)
    out = tr.translate(
        [ph(0, "Hello."), ph(1, "Bye", 2, 3), ph(2, "Да", keep=True)],
        language="en",
        progress=lambda f, m: None,
    )
    assert out == {0: "Привет.", 1: "Пока"}
    call = msgs.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "[2] (S, 2.0s) Да" in call["system"][0]["text"]  # full context incl. kept lines
    assert '"id": 2' not in call["messages"][0]["content"]  # but not asked to translate it
    assert '"budget_chars": 28' in call["messages"][0]["content"]
    assert call["fallbacks"] == "default"


def test_missing_ids_are_retried_once() -> None:
    c, msgs = client([[(0, "А")], [(1, "Б")]])
    out = ClaudeTranslator(None, client=c).translate(
        [ph(0, "a"), ph(1, "b")], language="en", progress=lambda f, m: None
    )
    assert out == {0: "А", 1: "Б"} and len(msgs.calls) == 2


def test_refusal_is_a_user_error() -> None:
    msgs = SimpleNamespace(parse=lambda **kw: SimpleNamespace(stop_reason="refusal", parsed_output=None))
    tr = ClaudeTranslator(None, client=SimpleNamespace(beta=SimpleNamespace(messages=msgs)))
    with pytest.raises(UserFacingError):
        tr.translate([ph(0, "x")], language="en", progress=lambda f, m: None)


def test_no_key_is_explained() -> None:
    with pytest.raises(UserFacingError) as ei:
        ClaudeTranslator(None)
    assert "ключ" in ei.value.message.lower()


def test_prompt_builders() -> None:
    p = ph(3, "Long sentence", 0, 1.5)
    p.translation = "Длинное предложение"
    assert '"current": "Длинное предложение"' in build_shorten_request([(p, 12)])
    assert "английский" in build_request([p], "en")
    assert format_transcript([p]) == "[3] (S, 1.5s) Long sentence"
