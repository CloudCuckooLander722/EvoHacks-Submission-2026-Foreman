"""The fakes are part of the test contract: every tool test depends on these behaviours."""

import pytest

from foreman.core.jev import JevError, StateTooLarge
from foreman.core.llm import LLMError
from foreman.core.questions import Choice, Noul, Score
from foreman.testing.fakes import FakeAnswer, FakeJev, FakeLLM

TYPE = Choice(
    "What kind of review?",
    {
        "bug": "The app crashes or something is broken",
        "feature_request": "Asks for something new",
        "other": None,
    },
)
LEVELS = Score("How urgent?", ["trivial cosmetic", "annoying", "crashes data loss"])


async def test_fakejev_choice_keyword_overlap() -> None:
    jev = FakeJev()
    res = await jev.ask("The app CRASHES on launch", {"t": TYPE})
    a = res.answers["t"]
    assert a.value == "bug"
    assert a.confidence == 0.9
    assert a.probabilities == pytest.approx({"bug": 0.8, "feature_request": 0.1, "other": 0.1})
    assert a.decided_by == "jev"


async def test_fakejev_choice_no_overlap_picks_first_low_confidence() -> None:
    res = await FakeJev().ask("lorem ipsum", {"t": TYPE})
    assert res.answers["t"].value == "bug"
    assert res.answers["t"].confidence == 0.3


async def test_fakejev_choice_uses_key_words() -> None:
    res = await FakeJev().ask({"text": "please add a dark mode feature"}, {"t": TYPE})
    assert res.answers["t"].value == "feature_request"


async def test_fakejev_score() -> None:
    res = await FakeJev().ask("it crashes and I lost data", {"u": LEVELS})
    a = res.answers["u"]
    assert a.value == 2.0 and a.confidence == 0.8
    assert a.probabilities == pytest.approx({"0": 0.15, "1": 0.15, "2": 0.7})
    res = await FakeJev().ask("meh", {"u": LEVELS})
    assert res.answers["u"].value == 1.0  # middle level


async def test_fakejev_noul() -> None:
    q = Noul("Is it spam?", true="advertising or promo links")
    res = await FakeJev().ask("Buy now! Great PROMO at example.com", {"s": q})
    assert res.answers["s"].value == 0.9
    assert res.answers["s"].confidence == pytest.approx(0.8)
    res = await FakeJev().ask("the camera is broken", {"s": q})
    assert res.answers["s"].value == 0.1
    # Without `true`, the instructions' words are used.
    res = await FakeJev().ask("this is spam", {"s": Noul("Is it spam?")})
    assert res.answers["s"].value == 0.9


async def test_fakejev_rule_and_records_calls() -> None:
    def rule(_state, qid, _q):
        return FakeAnswer("other", confidence=0.42) if qid == "t" else None

    jev = FakeJev(rule)
    res = await jev.ask("crash", {"t": TYPE, "s": Noul("Is it spam?")})
    assert res.answers["t"].value == "other" and res.answers["t"].confidence == 0.42
    assert res.answers["s"].value == 0.1
    assert len(jev.calls) == 1 and jev.calls[0][0] == "crash"
    assert res.input_tokens > 0
    assert res.cost_usd == pytest.approx(res.input_tokens * 0.042 / 1e6)


async def test_fakejev_limits_and_failure() -> None:
    with pytest.raises(ValueError):
        await FakeJev().ask("x", {"t": Choice("x", {})})
    with pytest.raises(StateTooLarge):
        await FakeJev(max_state_tokens=1).ask("x" * 50, {"t": TYPE})
    with pytest.raises(JevError):
        await FakeJev(fail=True).ask("x", {"t": TYPE})


async def test_fakellm_decide() -> None:
    llm = FakeLLM()
    a = await llm.decide("it crashes", "t", TYPE)
    assert a is not None
    assert (a.value, a.confidence, a.probabilities, a.decided_by) == (
        "bug",
        1.0,
        {"bug": 1.0},
        "llm",
    )
    n = await llm.decide("promo", "s", Noul("Spam?", true="promo"))
    assert n is not None and n.value == 1.0
    s = await llm.decide("lost data", "u", LEVELS)
    assert s is not None and s.value == 2.0
    assert [c[0] for c in llm.calls] == ["decide", "decide", "decide"]


async def test_fakellm_rule_fail_and_raising_rule() -> None:
    llm = FakeLLM(rule=lambda *_: FakeAnswer("other"))
    a = await llm.decide("crash", "t", TYPE)
    assert a is not None and a.value == "other"
    assert await FakeLLM(fail=True).decide("crash", "t", TYPE) is None

    def boom(*_):
        raise RuntimeError("x")

    assert await FakeLLM(rule=boom).decide("crash", "t", TYPE) is None


async def test_fakellm_json() -> None:
    llm = FakeLLM(json_responses={"themes": {"themes": ["Camera"]}, "plan": {"nodes": []}})
    out = await llm.json("Propose themes", "reviews...")
    assert out == {"themes": ["Camera"]}
    out["themes"].append("mutated")
    assert (await llm.json("sys", "make a plan")) == {"nodes": []}
    assert (await llm.json("Propose themes", ""))["themes"] == ["Camera"]  # deep-copied
    with pytest.raises(LLMError):
        await llm.json("unrelated", "prompt")


async def test_fake_llm_chat_echoes_last_user_message() -> None:
    msgs = [
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "x"},
        {"role": "user", "content": "two"},
    ]
    assert await FakeLLM().chat(msgs) == "(FakeLLM) You said: two"
    with pytest.raises(LLMError):
        await FakeLLM(fail=True).chat(msgs)
