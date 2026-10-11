"""make_plan: prompt contents, validation, one repair call, PlanError; offline with FakeLLM."""

import pytest

from foreman.contracts import load_items
from foreman.core.context import make_context
from foreman.graph.plan import PlanError
from foreman.graph.planner import build_prompts, compact_catalog, hero_plan, make_plan
from foreman.testing.fakes import FakeLLM

SAMPLE = "data/samples/reviews_small.json"
FIRST = "JOB:"  # in every planning prompt
REPAIR = "PROBLEMS WITH IT"  # only in the repair prompt
JOB = "Turn these reviews into a ranked bug backlog"

VALID = {"nodes": [n.model_dump() for n in hero_plan().nodes]}
INVALID = {
    "nodes": [
        {"id": "n1", "tool": "score", "params": {"output_field": "urgency"}, "why": "rate"},
        {"id": "n2", "tool": "teleport", "params": {}, "why": "?"},
    ]
}


async def _ctx(responses: dict) -> tuple:
    llm = FakeLLM(json_responses=responses)
    return await make_context(run_id="r_test", fake=True, llm=llm), llm


def _json_calls(llm: FakeLLM) -> list[tuple]:
    return [c for c in llm.calls if c[0] == "json"]


def test_prompts_are_compact_and_complete() -> None:
    run = load_items(SAMPLE)
    system, user = build_prompts(JOB, run)
    assert "Output JSON only" in system and '"is_feedback"' in system  # rules + hero example
    assert REPAIR not in system and REPAIR not in user
    assert f"JOB: {JOB}" in user
    assert "- rating: e.g. 1, 2\n" in user  # fields with up to 2 distinct example values
    assert user.count("\n- [row-") == 15  # 15 samples spread over the file
    assert "$defs" not in user and "$ref" not in user
    cat = compact_catalog()
    assert "- dimensions: list[{name: str, instructions: str, levels: list[str]" in cat
    assert "- operations: list[{op: parse_number|" in cat


async def test_valid_plan_needs_one_call() -> None:
    ctx, llm = await _ctx({FIRST: VALID})
    plan = await make_plan(JOB, load_items(SAMPLE), ctx)
    assert plan.job == JOB
    assert [n.model_dump() for n in plan.nodes] == VALID["nodes"]
    assert len(_json_calls(llm)) == 1
    [d] = ctx.ledger.decisions
    assert (d.item_id, d.tool, d.kind, d.decided_by) == ("__run__", "planner", "proposal", "llm")
    assert d.value == VALID["nodes"]
    assert ctx.ledger.stats().llm_calls == 1


async def test_invalid_plan_gets_exactly_one_repair() -> None:
    ctx, llm = await _ctx({REPAIR: VALID, FIRST: INVALID})  # first match wins
    plan = await make_plan(JOB, load_items(SAMPLE), ctx)
    assert len(plan.nodes) == 7
    calls = _json_calls(llm)
    assert len(calls) == 2
    repair_user = calls[1][2]
    assert "unknown tool 'teleport'" in repair_user
    assert '"teleport"' in repair_user  # the previous JSON is included
    assert [d.question_id for d in ctx.ledger.decisions] == ["plan", "plan_repair_1"]


async def test_two_invalid_plans_raise() -> None:
    ctx, llm = await _ctx({FIRST: INVALID})
    with pytest.raises(PlanError) as err:
        await make_plan(JOB, load_items(SAMPLE), ctx)
    assert len(_json_calls(llm)) == 2
    assert any("teleport" in p for p in err.value.problems)


async def test_missing_nodes_and_llm_failure_are_plan_errors() -> None:
    ctx, _ = await _ctx({FIRST: {"plan": []}})
    with pytest.raises(PlanError) as err:
        await make_plan(JOB, load_items(SAMPLE), ctx, max_repairs=0)
    assert err.value.problems == ['the reply must be a JSON object with a "nodes" list']

    ctx, _ = await _ctx({})
    with pytest.raises(PlanError, match="planner LLM call failed"):
        await make_plan(JOB, load_items(SAMPLE), ctx)
