"""validate_plan: tools, params, ids and field references, offline."""

from foreman.contracts import load_items
from foreman.graph.plan import Plan, PlanNode, sample_fields, validate_plan
from foreman.graph.planner import HERO_PLAN_PATH, hero_plan

SAMPLE = "data/samples/reviews_small.json"
FIELDS = {"rating", "date", "author"}

CLASSIFY = {"options": {"bug": "Something broken", "praise": "Positive"}, "output_field": "type"}
SCORE = {
    "dimensions": [
        {"name": "severity", "instructions": "How severe?", "levels": ["Minor", "Severe"]}
    ],
    "output_field": "urgency",
}


def _plan(*nodes: tuple[str, str, dict]) -> Plan:
    return Plan(job="j", nodes=[PlanNode(id=i, tool=t, params=p) for i, t, p in nodes])


def test_hero_plan_is_valid_for_reviews_small() -> None:
    plan = Plan.model_validate_json(HERO_PLAN_PATH.read_text())
    assert plan == hero_plan()
    assert [n.tool for n in plan.nodes] == [
        "filter",
        "classify",
        "group",
        "dedupe",
        "score",
        "rank",
        "calculate",
    ]
    assert validate_plan(plan, sample_fields(load_items(SAMPLE).items)) == []


def test_unknown_tool() -> None:
    problems = validate_plan(_plan(("n1", "teleport", {})), FIELDS)
    assert len(problems) == 1
    assert "unknown tool 'teleport'" in problems[0]


def test_bad_params_are_readable() -> None:
    problems = validate_plan(_plan(("n1", "classify", {"output_field": "type"})), FIELDS)
    assert problems and all(p.startswith("n1 (classify): params.") for p in problems)
    assert any("options" in p or "taxonomy" in p for p in problems)


def test_duplicate_id() -> None:
    problems = validate_plan(_plan(("n1", "classify", CLASSIFY), ("n1", "score", SCORE)), FIELDS)
    assert problems == ["n1 (score): duplicate node id 'n1'"]


def test_field_referenced_before_it_is_produced() -> None:
    where = {"field": "type", "op": "eq", "value": "bug"}
    plan = _plan(("n1", "score", {**SCORE, "where": where}), ("n2", "classify", CLASSIFY))
    problems = validate_plan(plan, FIELDS)
    assert len(problems) == 1
    assert "n1 (score): where.field refers to field 'type'" in problems[0]


def test_sample_fields_and_text_exist() -> None:
    plan = _plan(
        ("n1", "dedupe", {"key_fields": ["author", "text"], "mode": "records"}),
        ("n2", "calculate", {"operations": [{"op": "mean", "field": "rating", "as": "avg"}]}),
    )
    assert validate_plan(plan, FIELDS) == []
    assert validate_plan(plan, set()) != []


def test_prefixed_outputs_are_accepted() -> None:
    plan = _plan(
        ("n1", "score", SCORE),
        ("n2", "rank", {"criterion": "severity", "score_field": "urgency_severity", "top_k": 5}),
        ("n3", "calculate", {"operations": [{"op": "mean", "field": "rank_score", "as": "m"}]}),
    )
    assert validate_plan(plan, FIELDS) == []


def test_calculate_ops_see_earlier_row_ops_and_literals() -> None:
    ops = [
        {"op": "parse_number", "field": "rating", "as": "stars"},
        {"op": "mul", "a": "stars", "b": "2", "as": "doubled"},
        {"op": "group_count", "by": "doubled", "as": "counts"},
    ]
    assert validate_plan(_plan(("n1", "calculate", {"operations": ops})), FIELDS) == []
    bad = [{"op": "sum", "field": "doubled", "as": "s"}, *ops]
    problems = validate_plan(_plan(("n1", "calculate", {"operations": bad})), FIELDS)
    assert problems == [
        (
            "n1 (calculate): operations[0].field refers to field 'doubled', which does not "
            "exist at this point (not in the items and not written by an earlier node)"
        )
    ]


def test_where_value_must_be_a_classify_option() -> None:
    plan = _plan(
        ("n1", "classify", CLASSIFY),
        ("n2", "score", {**SCORE, "where": {"field": "type", "op": "in", "value": ["Bug"]}}),
    )
    problems = validate_plan(plan, FIELDS)
    assert len(problems) == 1
    assert "['Bug'] is not a value of 'type'" in problems[0]
