"""Decider: the confidence gate, fallback, ledger writes and the Jev-down path."""

import pytest

from foreman.core.context import make_context
from foreman.core.decide import DecisionError
from foreman.core.jev import StateTooLarge
from foreman.core.questions import Choice, Noul
from foreman.testing.fakes import FakeAnswer, FakeJev, FakeLLM

Q = {
    "type": Choice("What kind of review?", {"bug": "Something broken", "praise": "Positive"}),
    "spam": Noul("Is it spam?", true="advertising"),
}


def _jev(conf: dict[str, float]) -> FakeJev:
    """Jev that answers `bug`/0.2 with the given confidence per question."""

    def rule(_state, qid, _q):
        if qid == "type":
            return FakeAnswer("bug", confidence=conf["type"])
        return FakeAnswer(0.2, confidence=conf["spam"])

    return FakeJev(rule)


def _llm_says_praise() -> FakeLLM:
    return FakeLLM(rule=lambda _s, qid, _q: FakeAnswer("praise" if qid == "type" else 1.0))


async def test_above_threshold_accepted() -> None:
    llm = _llm_says_praise()
    ctx = await make_context(threshold=0.7, jev=_jev({"type": 0.9, "spam": 0.8}), llm=llm)
    ans = await ctx.decider.decide(item_id="a", tool="t", state="crash", questions=Q)
    assert ans["type"].value == "bug" and ans["type"].decided_by == "jev"
    assert ans["spam"].decided_by == "jev"
    assert llm.calls == []
    st = ctx.ledger.stats()
    assert st.by == {"jev": 2, "llm": 0, "code": 0}
    assert st.jev_calls == 1 and st.llm_calls == 0


async def test_below_threshold_falls_back() -> None:
    jev = _jev({"type": 0.5, "spam": 0.95})
    ctx = await make_context(threshold=0.7, jev=jev, llm=_llm_says_praise())
    ans = await ctx.decider.decide(item_id="a", tool="t", state="crash", questions=Q)
    assert ans["type"].value == "praise"
    assert ans["type"].decided_by == "llm"
    assert ans["type"].jev_value == "bug"
    assert ans["spam"].decided_by == "jev"
    assert len(jev.calls) == 1  # one Jev call for all questions

    led = {d.question_id: d for d in ctx.ledger.decisions}
    assert led["type"].decided_by == "llm"
    assert led["type"].jev_value == "bug"
    assert led["type"].jev_confidence == 0.5
    assert led["spam"].jev_confidence is None
    # Jev cost is split evenly between the two decisions of the call.
    assert led["type"].cost_usd == pytest.approx(led["spam"].cost_usd)
    st = ctx.ledger.stats()
    assert st.by["llm"] == 1 and st.by["jev"] == 1 and st.llm_calls == 1


async def test_fallback_failure_flags_low_confidence() -> None:
    ctx = await make_context(
        threshold=0.7, jev=_jev({"type": 0.4, "spam": 0.9}), llm=FakeLLM(fail=True)
    )
    ans = await ctx.decider.decide(item_id="a", tool="t", state="crash", questions=Q)
    assert ans["type"].value == "bug"
    assert ans["type"].decided_by == "jev"
    assert ans["type"].low_confidence is True
    assert ans["spam"].low_confidence is False
    assert ctx.ledger.stats().low_confidence == 1


async def test_no_fallback_questions_never_escalate() -> None:
    q = {"spam": Noul("Is it spam?", fallback=False)}
    llm = _llm_says_praise()
    jev = FakeJev(lambda *_: FakeAnswer(0.5))  # confidence 0
    ctx = await make_context(threshold=0.7, jev=jev, llm=llm)
    ans = await ctx.decider.decide(item_id="a", tool="t", state="x", questions=q)
    assert ans["spam"].decided_by == "jev"
    assert ans["spam"].low_confidence is False
    assert llm.calls == []


async def test_jev_down_uses_llm_for_everything() -> None:
    ctx = await make_context(jev=FakeJev(fail=True), llm=_llm_says_praise())
    ans = await ctx.decider.decide(item_id="a", tool="t", state="x", questions=Q)
    assert {a.decided_by for a in ans.values()} == {"llm"}
    assert ans["type"].value == "praise"
    assert ctx.ledger.stats().by["llm"] == 2


async def test_jev_down_and_llm_fails_raises() -> None:
    def rule(_s, qid, _q):
        if qid == "spam":
            raise RuntimeError("no")
        return FakeAnswer("bug")

    ctx = await make_context(jev=FakeJev(fail=True), llm=FakeLLM(rule=rule))
    with pytest.raises(DecisionError) as ei:
        await ctx.decider.decide(item_id="a", tool="t", state="x", questions=Q)
    assert ei.value.failed == ["spam"]
    assert ctx.ledger.decisions == []


async def test_state_too_large_propagates() -> None:
    ctx = await make_context(jev=FakeJev(max_state_tokens=5), llm=FakeLLM())
    with pytest.raises(StateTooLarge):
        await ctx.decider.decide(item_id="a", tool="t", state="x" * 100, questions=Q)


async def test_ledger_events_fire() -> None:
    ctx = await make_context(fake=True)
    seen = []
    ctx.ledger.on_event(seen.append)
    await ctx.decider.decide(item_id="a", tool="t", state="crash", questions=Q)
    assert [d.question_id for d in seen] == ["type", "spam"]
    assert all(d.run_id == ctx.run_id for d in seen)
    await ctx.aclose()
