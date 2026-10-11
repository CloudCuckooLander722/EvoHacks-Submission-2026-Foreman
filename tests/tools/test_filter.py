"""Filter: drop/keep by a Noul, `where` routing, fallback paths and the empty-text code path."""

import pytest
from pydantic import ValidationError

from foreman.core.context import make_context
from foreman.core.state import Condition, ItemState, RunState
from foreman.testing.fakes import FakeAnswer, FakeJev, FakeLLM
from foreman.tools import get_tool
from foreman.tools.filter import FilterParams, FilterTool, about_the_text

CONDITION = "Is this a genuine review of the app (not spam or an ad)?"


def _run() -> RunState:
    texts = {
        "a": "Camera crashes on launch.",
        "b": "buy followers cheap at example.biz",
        "c": "Love the new editor.",
        "d": "Want to buy followers? DM me",
    }
    items = [
        ItemState(id=k, text=v, fields={"rating": i}) for i, (k, v) in enumerate(texts.items())
    ]
    return RunState(run_id="r", job="j", items=items)


def _spam_rule(state, _qid, _q):
    return FakeAnswer(0.05 if "buy followers" in state else 0.95)


async def test_drops_spam_and_records_reason() -> None:
    ctx = await make_context(threshold=0.7, jev=FakeJev(_spam_rule), llm=FakeLLM())
    run = _run()
    report = await FilterTool().run(run, FilterParams(condition=CONDITION), ctx)

    assert [i.id for i in run.items] == ["a", "c"]
    assert [i.id for i in run.dropped] == ["b", "d"]
    assert all(i.meta["drop_reason"] == f"filter: {CONDITION}" for i in run.dropped)
    assert run.items[0].fields["keep"] is True and run.items[0].fields["keep__p"] == 0.95
    assert run.dropped[0].fields["keep"] is False and run.dropped[0].fields["keep__by"] == "jev"
    assert report.items_in == 4 and report.items_out == 2
    assert report.jev_decisions == 4 and report.llm_decisions == 0


async def test_drop_false_keeps_everything() -> None:
    ctx = await make_context(jev=FakeJev(_spam_rule), llm=FakeLLM())
    run = _run()
    report = await FilterTool().run(run, FilterParams(condition=CONDITION, drop=False), ctx)
    assert len(run.items) == 4 and run.dropped == []
    assert [i.fields["keep"] for i in run.items] == [True, False, True, False]
    assert report.items_out == 4


async def test_where_is_respected() -> None:
    ctx = await make_context(jev=FakeJev(_spam_rule), llm=FakeLLM())
    run = _run()
    params = FilterParams(condition=CONDITION, where=Condition(field="rating", op="lte", value=1))
    report = await FilterTool().run(run, params, ctx)

    assert [i.id for i in run.items] == ["a", "c", "d"]  # only b matched and was spam
    assert [i.id for i in run.dropped] == ["b"]
    by_id = {i.id: i for i in run.items}
    assert "keep" not in by_id["c"].fields and "keep" not in by_id["d"].fields
    assert report.items_out == 3


async def test_low_confidence_goes_to_llm() -> None:
    ctx = await make_context(threshold=0.7, jev=FakeJev(lambda *_: FakeAnswer(0.55)), llm=FakeLLM())
    run = _run()
    report = await FilterTool().run(run, FilterParams(condition=CONDITION, drop=False), ctx)
    assert all(i.fields["keep__by"] == "llm" for i in run.items)
    assert report.llm_decisions == 4


async def test_failed_fallback_marks_low_confidence() -> None:
    ctx = await make_context(
        threshold=0.7, jev=FakeJev(lambda *_: FakeAnswer(0.55)), llm=FakeLLM(fail=True)
    )
    run = _run()
    report = await FilterTool().run(run, FilterParams(condition=CONDITION), ctx)
    assert len(run.items) == 4  # 0.55 >= 0.5, Jev's answer stands
    assert all(i.fields["keep__low_confidence"] is True for i in run.items)
    assert all(i.fields["keep__by"] == "jev" for i in run.items)
    assert report.low_confidence == 4


async def test_empty_text_decided_in_code() -> None:
    jev = FakeJev(_spam_rule)
    ctx = await make_context(jev=jev, llm=FakeLLM())
    run = RunState(
        run_id="r",
        job="j",
        items=[ItemState(id="e", text="   "), ItemState(id="ok", text="Nice app")],
    )
    report = await FilterTool().run(run, FilterParams(condition=CONDITION), ctx)

    assert len(jev.calls) == 1
    (e,) = run.dropped
    assert e.fields["keep"] is False and e.fields["keep__by"] == "code"
    assert e.fields["keep__confidence"] == 1.0
    assert report.code_ops == 1 and report.jev_decisions == 1
    assert ctx.ledger.stats().by["code"] == 1


async def test_errored_items_are_never_dropped() -> None:
    ctx = await make_context(jev=FakeJev(fail=True), llm=FakeLLM(fail=True))
    run = _run()
    report = await FilterTool().run(run, FilterParams(condition=CONDITION), ctx)
    assert len(run.items) == 4 and run.dropped == []
    assert all("keep__error" in i.fields for i in run.items)
    assert report.items_out == 4


def test_params_and_question_wording() -> None:
    with pytest.raises(ValidationError):
        FilterParams(condition="   ")
    with pytest.raises(ValidationError):
        FilterParams(condition="Is it spam?", cutoff=1.5)
    assert about_the_text("Is it spam?") == "About the text: Is it spam?"
    assert about_the_text("Does the text mention a bug?") == "Does the text mention a bug?"
    assert isinstance(get_tool("filter"), FilterTool)
