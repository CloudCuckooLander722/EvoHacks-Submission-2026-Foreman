"""Rank tool: Jev scores, score_field reuse, both-order tie-breaks, top_k and budget."""

import pytest
from pydantic import ValidationError

from foreman.core.context import make_context
from foreman.core.questions import Choice, Score
from foreman.core.state import ItemState, RunState
from foreman.testing.fakes import FakeAnswer, FakeJev, FakeLLM
from foreman.tools import catalog, get_tool
from foreman.tools.rank import RankParams, RankTool, default_levels

CRITERION = "urgency of fixing this issue"


def _run(texts: dict[str, str], fields: dict[str, dict] | None = None) -> RunState:
    fields = fields or {}
    return RunState(
        run_id="r",
        job="j",
        items=[ItemState(id=k, text=v, fields=dict(fields.get(k, {}))) for k, v in texts.items()],
    )


def _rule(levels: dict[str, float], pick=None):
    """Score by item text; pairwise answers from `pick(state, qid)` (default: 'equal')."""

    def rule(state, qid, q):
        if isinstance(q, Score):
            return FakeAnswer(levels[state], confidence=0.9)
        return FakeAnswer(pick(state, qid) if pick else "equal", confidence=0.9)

    return rule


def _pair_calls(jev: FakeJev) -> list[tuple]:
    return [(s, qs) for s, qs in jev.calls if "ab" in qs]


async def test_orders_by_jev_score() -> None:
    jev = FakeJev(_rule({"crash": 4, "typo": 0, "slow": 2}))
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    run = _run({"a": "typo", "b": "crash", "c": "slow"})
    report = await RankTool().run(run, RankParams(criterion=CRITERION), ctx)

    f = {i.id: i.fields for i in run.items}
    assert run.aggregates["rank_order"] == ["b", "c", "a"]
    assert (f["b"]["rank"], f["c"]["rank"], f["a"]["rank"]) == (1, 2, 3)
    assert (f["b"]["rank_score"], f["c"]["rank_score"], f["a"]["rank_score"]) == (1.0, 0.5, 0.0)
    assert f["b"]["rank__by"] == "jev" and f["b"]["rank__confidence"] == 0.9
    assert not _pair_calls(jev)  # no near-ties
    assert report.jev_decisions == 3
    assert "tie-breaks: 0 run, 0 swapped" in report.notes

    q = jev.calls[0][1]["score"]
    assert q.instructions == f"How much {CRITERION} does the text show?"
    assert q.levels == default_levels(CRITERION)


async def test_ascending() -> None:
    jev = FakeJev(_rule({"crash": 4, "typo": 0, "slow": 2}))
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    run = _run({"a": "typo", "b": "crash", "c": "slow"})
    await RankTool().run(run, RankParams(criterion=CRITERION, descending=False), ctx)
    assert run.aggregates["rank_order"] == ["a", "c", "b"]


async def test_score_field_reuse_is_code_only() -> None:
    jev = FakeJev()
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    run = _run(
        {"a": "x", "b": "y", "c": "z"},
        {"a": {"urgency": 20.0}, "b": {"urgency": 90.0}, "c": {"urgency": 50.0}},
    )
    report = await RankTool().run(run, RankParams(criterion=CRITERION, score_field="urgency"), ctx)

    f = {i.id: i.fields for i in run.items}
    assert jev.calls == []
    assert run.aggregates["rank_order"] == ["b", "c", "a"]
    assert f["b"]["rank"] == 1 and f["b"]["rank__by"] == "code"
    assert f["b"]["rank__confidence"] == 1.0
    assert f["c"]["rank_score"] == round(30 / 70, 3)
    assert report.jev_decisions == 0 and report.llm_decisions == 0


async def test_non_numeric_score_field_goes_last() -> None:
    ctx = await make_context(threshold=0.7, jev=FakeJev(), llm=FakeLLM())
    run = _run(
        {"a": "x", "b": "y", "c": "z", "d": "w"},
        {"a": {"u": "n/a"}, "b": {"u": "3"}, "c": {}, "d": {"u": 7}},
    )
    report = await RankTool().run(run, RankParams(criterion=CRITERION, score_field="u"), ctx)

    assert run.aggregates["rank_order"] == ["d", "b", "a", "c"]
    f = {i.id: i.fields for i in run.items}
    assert f["a"]["rank"] == 3 and f["a"]["rank_score"] is None
    assert any("2 items had no numeric" in n for n in report.notes)


async def test_tie_break_swaps_when_both_orders_agree() -> None:
    jev = FakeJev(_rule({"first": 3, "second": 3}, pick=lambda _s, _q: "item_b"))
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    run = _run({"a": "first", "b": "second"})
    report = await RankTool().run(run, RankParams(criterion=CRITERION), ctx)

    assert run.aggregates["rank_order"] == ["b", "a"]
    pairs = _pair_calls(jev)
    assert len(pairs) == 1
    state, qs = pairs[0]
    assert state == {"item_a": "first", "item_b": "second"}
    assert isinstance(qs["ab"], Choice) and not qs["ab"].fallback and not qs["ba"].fallback
    assert list(qs["ab"].options) == ["item_a", "item_b", "equal"]
    assert list(qs["ba"].options) == ["item_b", "item_a", "equal"]
    assert "tie-breaks: 1 run, 1 swapped" in report.notes
    pair_ids = {d.item_id for d in ctx.ledger.decisions if d.question_id in ("ab", "ba")}
    assert pair_ids == {"a|b"}


async def test_no_swap_when_orders_disagree() -> None:
    # Positional bias: each question picks whichever item is listed first.
    first = {"ab": "item_a", "ba": "item_b"}
    jev = FakeJev(_rule({"first": 3, "second": 3}, pick=lambda _s, qid: first[qid]))
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    run = _run({"a": "first", "b": "second"})
    report = await RankTool().run(run, RankParams(criterion=CRITERION), ctx)

    assert run.aggregates["rank_order"] == ["a", "b"]
    assert "tie-breaks: 1 run, 0 swapped" in report.notes


async def test_top_k_limits_tie_breaks() -> None:
    texts = {k: k for k in "abcdef"}
    jev = FakeJev(_rule(dict.fromkeys(texts, 2.0)))
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    await RankTool().run(_run(texts), RankParams(criterion=CRITERION, top_k=3), ctx)

    pairs = _pair_calls(jev)
    assert [s for s, _ in pairs] == [
        {"item_a": "a", "item_b": "b"},
        {"item_a": "b", "item_b": "c"},
    ]


async def test_max_tiebreaks_respected() -> None:
    texts = {k: k for k in "abcdef"}
    jev = FakeJev(_rule(dict.fromkeys(texts, 2.0)))
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    report = await RankTool().run(
        _run(texts), RankParams(criterion=CRITERION, max_tiebreaks=2), ctx
    )
    assert len(_pair_calls(jev)) == 2
    assert "tie-breaks: 2 run, 0 swapped" in report.notes


async def test_tie_break_off_and_margin() -> None:
    jev = FakeJev(_rule({"a": 2, "b": 2, "c": 1.9}))
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    await RankTool().run(
        _run({"a": "a", "b": "b"}), RankParams(criterion=CRITERION, tie_break=False), ctx
    )
    assert not _pair_calls(jev)

    # 2.0 vs 1.9 of 4 -> gap 0.025: a tie at the default margin, not at 0.01.
    jev = FakeJev(_rule({"a": 2, "c": 1.9}))
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    await RankTool().run(
        _run({"a": "a", "c": "c"}), RankParams(criterion=CRITERION, tie_margin=0.01), ctx
    )
    assert not _pair_calls(jev)


async def test_skipped_items_are_not_ranked() -> None:
    run = _run({"a": "short", "b": "x" * 400, "c": "tiny"})
    jev = FakeJev(_rule({"short": 4, "tiny": 1}), max_state_tokens=50)
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    await RankTool().run(run, RankParams(criterion=CRITERION), ctx)

    assert run.aggregates["rank_order"] == ["a", "c"]
    assert "rank__error" in run.items[1].fields and "rank" not in run.items[1].fields


async def test_where_limits_ranked_items() -> None:
    run = _run(
        {"a": "x", "b": "y", "c": "z"},
        {"a": {"u": 1}, "b": {"u": 2, "keep": True}, "c": {"u": 3, "keep": True}},
    )
    ctx = await make_context(threshold=0.7, jev=FakeJev(), llm=FakeLLM())
    params = RankParams(
        criterion=CRITERION, score_field="u", where={"field": "keep", "op": "truthy"}
    )
    await RankTool().run(run, params, ctx)
    assert run.aggregates["rank_order"] == ["c", "b"]
    assert "rank" not in run.items[0].fields


@pytest.mark.parametrize(
    "kw",
    [
        {"criterion": "  "},
        {"criterion": CRITERION, "levels": ["only one"]},
        {"criterion": CRITERION, "levels": [f"l{i}" for i in range(11)]},
        {"criterion": CRITERION, "levels": ["low", " "]},
        {"criterion": CRITERION, "tie_margin": 1.5},
        {"criterion": CRITERION, "top_k": 0},
    ],
)
def test_validation_rejects(kw) -> None:
    with pytest.raises(ValidationError):
        RankParams(**kw)


def test_registered_in_catalog() -> None:
    assert isinstance(get_tool("rank"), RankTool)
    entry = next(e for e in catalog() if e["name"] == "rank")
    assert entry["uses"] == "jev"
    assert "score_field" in entry["params_schema"]["properties"]
    assert "score_field" in entry["description"]
