"""Score tool: per-dimension math, one Jev call per item, provenance, validation, summary."""

import pytest
from pydantic import ValidationError

from foreman.core.context import make_context
from foreman.core.questions import Score
from foreman.core.state import ItemState, RunState
from foreman.testing.fakes import FakeAnswer, FakeJev, FakeLLM
from foreman.tools import catalog, get_tool
from foreman.tools.score import ScoreParams, ScoreTool

SEVERITY = {
    "name": "severity",
    "instructions": "How severe is the problem described in the text for the user?",
    "levels": ["No problem", "Cosmetic", "Feature broken", "App unusable or data lost"],
    "weight": 3.0,
}
REACH = {
    "name": "reach",
    "instructions": "How many users does the problem in the text likely affect?",
    "levels": ["Only this user", "Some users", "Most users"],
    "weight": 1.0,
}

# Expected level per item for (severity, reach). Normalized: severity /3, reach /2.
LEVELS = {"a": (3.0, 1.0), "b": (0.0, 0.0), "c": (1.5, 2.0)}


def _params(**kw) -> ScoreParams:
    return ScoreParams(dimensions=[SEVERITY, REACH], **kw)


def _run() -> RunState:
    texts = {"a": "crash", "b": "love it", "c": "button slightly off"}
    return RunState(run_id="r", job="j", items=[ItemState(id=k, text=v) for k, v in texts.items()])


def _rule(confidence: float = 0.9, low_on: tuple[str, str] | None = None):
    texts = {"crash": "a", "love it": "b", "button slightly off": "c"}

    def rule(state, qid, q):
        item = texts[state]
        sev, reach = LEVELS[item]
        value = sev if qid == "dim_severity" else reach
        conf = 0.2 if low_on == (item, qid) else confidence
        return FakeAnswer(value, confidence=conf)

    return rule


async def test_composite_math_exact() -> None:
    jev = FakeJev(_rule())
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    run = _run()
    report = await ScoreTool().run(run, _params(), ctx)

    f = {i.id: i.fields for i in run.items}
    # a: severity 1.0, reach 0.5 -> (3*1 + 1*0.5)/4 = 0.875
    assert f["a"]["score_severity"] == 100.0 and f["a"]["score_reach"] == 50.0
    assert f["a"]["score"] == 87.5
    # b: all zero
    assert f["b"]["score"] == 0.0
    # c: severity 0.5, reach 1.0 -> (1.5 + 1)/4 = 0.625
    assert f["c"]["score_severity"] == 50.0 and f["c"]["score_reach"] == 100.0
    assert f["c"]["score"] == 62.5
    assert f["a"]["score__by"] == "jev" and f["a"]["score__confidence"] == 0.9
    assert f["a"]["score_severity__confidence"] == 0.9
    assert "score__low_confidence" not in f["a"]
    assert report.jev_decisions == 6 and report.llm_decisions == 0


async def test_scale_and_clamp() -> None:
    def rule(_state, qid, _q):
        return FakeAnswer(5.0 if qid == "dim_severity" else -1.0, confidence=0.9)

    ctx = await make_context(threshold=0.7, jev=FakeJev(rule), llm=FakeLLM())
    run = _run()
    await ScoreTool().run(run, _params(scale=10, output_field="urgency"), ctx)
    f = run.items[0].fields
    assert f["urgency_severity"] == 10.0 and f["urgency_reach"] == 0.0
    assert f["urgency"] == 7.5


async def test_one_jev_call_per_item_with_all_dimensions() -> None:
    jev = FakeJev(_rule())
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    await ScoreTool().run(_run(), _params(), ctx)

    assert len(jev.calls) == 3
    for _state, questions in jev.calls:
        assert set(questions) == {"dim_severity", "dim_reach"}
        assert all(isinstance(q, Score) for q in questions.values())
        assert questions["dim_severity"].levels == SEVERITY["levels"]


async def test_by_llm_when_one_dimension_falls_back() -> None:
    llm = FakeLLM(lambda *_: FakeAnswer(2))
    jev = FakeJev(_rule(low_on=("a", "dim_reach")))
    ctx = await make_context(threshold=0.7, jev=jev, llm=llm)
    run = _run()
    report = await ScoreTool().run(run, _params(), ctx)

    f = {i.id: i.fields for i in run.items}
    assert f["a"]["score__by"] == "llm"
    assert f["a"]["score_reach"] == 100.0  # LLM said level 2 of 0..2
    assert f["a"]["score__confidence"] == 0.9  # min(jev 0.9, llm 1.0)
    assert f["b"]["score__by"] == "jev"
    assert report.llm_decisions == 1 and report.jev_decisions == 5


async def test_low_confidence_when_fallback_fails() -> None:
    jev = FakeJev(_rule(low_on=("a", "dim_reach")))
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM(fail=True))
    run = _run()
    await ScoreTool().run(run, _params(), ctx)

    f = run.items[0].fields
    assert f["score__by"] == "jev"
    assert f["score__low_confidence"] is True
    assert f["score__confidence"] == 0.2


async def test_summary_aggregates() -> None:
    ctx = await make_context(threshold=0.7, jev=FakeJev(_rule()), llm=FakeLLM())
    run = _run()
    await ScoreTool().run(run, _params(), ctx)

    s = run.aggregates["score_summary"]
    assert s["n"] == 3
    assert s["mean"] == round((87.5 + 0.0 + 62.5) / 3, 1)
    assert s["median"] == 62.5
    assert s["min"] == 0.0 and s["max"] == 87.5
    assert s["per_dimension_mean"] == {"severity": 50.0, "reach": 50.0}


async def test_summary_excludes_skipped_items() -> None:
    run = _run()
    run.items[1].text = "x" * 400  # over the tiny state limit -> skipped
    jev = FakeJev(_rule(), max_state_tokens=50)
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    report = await ScoreTool().run(run, _params(), ctx)

    assert "score__error" in run.items[1].fields and "score" not in run.items[1].fields
    s = run.aggregates["score_summary"]
    assert s["n"] == 2 and s["min"] == 62.5 and s["max"] == 87.5
    assert any("skipped" in n for n in report.notes)


@pytest.mark.parametrize(
    "dims",
    [
        [SEVERITY, {**REACH, "name": "severity"}],  # duplicate names
        [{**SEVERITY, "levels": ["only one"]}],  # 1 level
        [{**SEVERITY, "levels": [f"level {i}" for i in range(11)]}],  # 11 levels
        [{**SEVERITY, "weight": 0}],  # zero weight
        [{**SEVERITY, "name": "Bad Name"}],  # not a slug
        [],  # no dimensions
        [{**SEVERITY, "name": f"d{i}"} for i in range(9)],  # too many dimensions
    ],
)
def test_validation_rejects(dims) -> None:
    with pytest.raises(ValidationError):
        ScoreParams(dimensions=dims)


def test_registered_in_catalog() -> None:
    assert isinstance(get_tool("score"), ScoreTool)
    entry = next(e for e in catalog() if e["name"] == "score")
    assert entry["uses"] == "jev"
    assert "dimensions" in entry["params_schema"]["properties"]
