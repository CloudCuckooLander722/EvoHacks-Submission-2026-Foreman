"""Group: theme proposal, assignment, one refinement round, all offline on FakeJev/FakeLLM."""

import pytest
from pydantic import ValidationError

from foreman.core.context import make_context
from foreman.core.questions import Choice
from foreman.core.state import ItemState, RunState
from foreman.testing.fakes import FakeAnswer, FakeJev, FakeLLM
from foreman.tools import catalog, get_tool
from foreman.tools.group import (
    NONE,
    Group,
    GroupError,
    GroupParams,
    Theme,
    slugify,
    validate_themes,
)

PROPOSE = "propose exactly"  # substring of the first-round prompt
REFINE = "NEW themes"  # substring of the refinement prompt

THEMES = {
    "themes": [
        {"name": "Camera Crash", "description": "The app crashes when the camera opens."},
        {"name": "Battery Drain", "description": "The battery runs down fast."},
        {"name": "Login Trouble", "description": "The user cannot sign in."},
    ]
}
NEW_THEMES = {
    "themes": [
        {"name": "Shipping Delay", "description": "The order arrived late."},
        {"name": "camera crash", "description": "Duplicate of an existing theme."},
    ]
}

# keyword in the text -> option slug
KEYWORDS = {
    "camera": "camera_crash",
    "battery": "battery_drain",
    "login": "login_trouble",
    "shipping": "shipping_delay",
}


def keyword_rule(state, qid, q):
    """Pick the slug for the first keyword found; none_of_these when nothing matches."""
    assert isinstance(q, Choice)
    for word, slug in KEYWORDS.items():
        if word in state and slug in q.options:
            return FakeAnswer(slug, confidence=0.9)
    return FakeAnswer(NONE, confidence=0.9)


def _run(texts: list[str]) -> RunState:
    items = [ItemState(id=f"i{n}", text=t) for n, t in enumerate(texts)]
    return RunState(run_id="r", job="j", items=items)


async def test_propose_then_assign() -> None:
    jev = FakeJev(keyword_rule)
    llm = FakeLLM(json_responses={PROPOSE: THEMES})
    ctx = await make_context(threshold=0.7, jev=jev, llm=llm)
    run = _run(["camera broke"] * 4 + ["battery dies"] * 2 + ["login fails"] + ["misc stuff"])
    report = await Group().run(run, GroupParams(n_themes=3, focus="the problem"), ctx)

    assert len(llm.calls) == 1 and llm.calls[0][0] == "json"
    groups = run.aggregates["theme_groups"]
    assert [(g["theme"], g["count"]) for g in groups] == [
        ("Camera Crash", 4),
        ("Battery Drain", 2),
        ("Login Trouble", 1),
        (NONE, 1),
    ]
    assert groups[0]["example_ids"] == ["i0", "i1", "i2"]
    assert groups[1]["description"] == "The battery runs down fast."
    f = run.items[0].fields
    assert f["theme"] == "Camera Crash" and f["theme__by"] == "jev"
    assert f["theme__confidence"] == 0.9
    assert run.items[-1].fields["theme"] == NONE

    q = jev.calls[0][1]["theme"]
    assert list(q.options) == ["camera_crash", "battery_drain", "login_trouble", NONE]
    assert q.instructions == "Which theme best describes the problem in the text?"

    proposals = [d for d in ctx.ledger.decisions if d.kind == "proposal"]
    assert len(proposals) == 1
    p = proposals[0]
    assert p.item_id == "__run__" and p.question_id == "themes" and p.decided_by == "llm"
    assert [t["name"] for t in p.value] == ["Camera Crash", "Battery Drain", "Login Trouble"]
    assert ctx.ledger.stats().llm_calls == 1
    assert report.jev_decisions == 8
    assert any("proposed 3 themes" in n for n in report.notes)
    assert any("refinement: skipped" in n for n in report.notes)


async def test_sample_is_seeded_and_truncated() -> None:
    llm = FakeLLM(json_responses={PROPOSE: THEMES})
    ctx = await make_context(jev=FakeJev(keyword_rule), llm=llm)
    run = _run([f"text {n} " + "x" * 500 for n in range(100)])
    await Group().run(run, GroupParams(sample_size=10, seed=3), ctx)
    user = llm.calls[0][2]
    lines = [ln for ln in user.splitlines() if ln[:1].isdigit()]
    assert len(lines) == 10
    assert all(len(ln.split(". ", 1)[1]) <= 300 for ln in lines)

    llm2 = FakeLLM(json_responses={PROPOSE: THEMES})
    ctx2 = await make_context(jev=FakeJev(keyword_rule), llm=llm2)
    await Group().run(
        _run([f"text {n} " + "x" * 500 for n in range(100)]),
        GroupParams(sample_size=10, seed=3),
        ctx2,
    )
    assert llm2.calls[0][2] == user


async def test_given_themes_skip_llm() -> None:
    llm = FakeLLM()
    ctx = await make_context(jev=FakeJev(keyword_rule), llm=llm)
    themes = [Theme(**t) for t in THEMES["themes"]]
    run = _run(["camera broke", "login fails"])
    report = await Group().run(run, GroupParams(themes=themes), ctx)
    assert not [c for c in llm.calls if c[0] == "json"]
    assert [i.fields["theme"] for i in run.items] == ["Camera Crash", "Login Trouble"]
    assert [g["count"] for g in run.aggregates["theme_groups"]] == [1, 1, 0]
    assert any("discovery skipped" in n for n in report.notes)


async def test_refinement_reasks_only_outliers() -> None:
    jev = FakeJev(keyword_rule)
    llm = FakeLLM(json_responses={PROPOSE: THEMES, REFINE: NEW_THEMES})
    ctx = await make_context(jev=jev, llm=llm)
    texts = ["camera broke"] * 20 + ["shipping was slow"] * 10 + ["random noise"] * 2
    run = _run(texts)
    report = await Group().run(run, GroupParams(outlier_min=10), ctx)

    assert len(llm.calls) == 2
    assert len(jev.calls) == len(texts) + 12  # every item once, then only the 12 outliers
    second = [state for state, _ in jev.calls[len(texts) :]]
    assert sorted(second) == sorted(["shipping was slow"] * 10 + ["random noise"] * 2)
    q = jev.calls[-1][1]["theme"]
    assert list(q.options)[-2:] == ["shipping_delay", NONE]

    groups = {g["theme"]: g["count"] for g in run.aggregates["theme_groups"]}
    assert groups["Camera Crash"] == 20 and groups["Shipping Delay"] == 10
    assert groups[NONE] == 2
    assert run.aggregates["theme_groups"][-1]["theme"] == NONE
    # The duplicate "camera crash" was rejected by the validator.
    assert (
        sum(1 for g in run.aggregates["theme_groups"] if g["theme"].lower() == "camera crash") == 1
    )

    proposals = [d for d in ctx.ledger.decisions if d.kind == "proposal"]
    assert [d.question_id for d in proposals] == ["themes", "new_themes"]
    refine_user = llm.calls[1][2]
    assert "- Camera Crash" in refine_user and refine_user.count("shipping was slow") == 10
    assert any("refinement: ran on 12 outliers; 1 new themes" in n for n in report.notes)
    # 32 first-round answers + 12 re-asks + 2 proposals
    assert report.jev_decisions == 44 and report.llm_decisions == 2


async def test_refinement_below_minimum() -> None:
    llm = FakeLLM(json_responses={PROPOSE: THEMES, REFINE: NEW_THEMES})
    jev = FakeJev(keyword_rule)
    ctx = await make_context(jev=jev, llm=llm)
    texts = ["camera broke"] * 20 + ["shipping was slow"] * 9
    run = _run(texts)
    report = await Group().run(run, GroupParams(outlier_min=10), ctx)
    assert len(llm.calls) == 1 and len(jev.calls) == len(texts)
    assert run.aggregates["theme_groups"][-1] == {
        "theme": NONE,
        "description": "The text fits none of the themes above",
        "count": 9,
        "example_ids": ["i20", "i21", "i22"],
    }
    assert any("refinement: skipped (9 outliers, needs 10)" in n for n in report.notes)


async def test_refinement_needs_three_percent() -> None:
    # 400 items: 3% is 12, above outlier_min=10, so 11 outliers is not enough.
    llm = FakeLLM(json_responses={PROPOSE: THEMES, REFINE: NEW_THEMES})
    ctx = await make_context(jev=FakeJev(keyword_rule, latency_ms=0), llm=llm)
    run = _run(["camera broke"] * 389 + ["shipping was slow"] * 11)
    await Group().run(run, GroupParams(outlier_min=10), ctx)
    assert len(llm.calls) == 1


async def test_refinement_off() -> None:
    llm = FakeLLM(json_responses={PROPOSE: THEMES, REFINE: NEW_THEMES})
    ctx = await make_context(jev=FakeJev(keyword_rule), llm=llm)
    run = _run(["shipping was slow"] * 20)
    report = await Group().run(run, GroupParams(allow_new_themes=False), ctx)
    assert len(llm.calls) == 1 and "refinement: off" in report.notes


async def test_failed_refinement_keeps_outliers() -> None:
    llm = FakeLLM(json_responses={PROPOSE: THEMES})  # nothing for the refinement prompt
    ctx = await make_context(jev=FakeJev(keyword_rule), llm=llm)
    run = _run(["shipping was slow"] * 12)
    report = await Group().run(run, GroupParams(), ctx)
    assert all(i.fields["theme"] == NONE for i in run.items)
    assert any("LLM failed" in n for n in report.notes)


async def test_too_few_themes_raises() -> None:
    llm = FakeLLM(json_responses={PROPOSE: {"themes": [{"name": "Only", "description": "x"}]}})
    ctx = await make_context(jev=FakeJev(), llm=llm)
    with pytest.raises(GroupError, match="at least 2"):
        await Group().run(_run(["a", "b"]), GroupParams(), ctx)


async def test_failed_items_get_error_field() -> None:
    llm = FakeLLM()
    ctx = await make_context(jev=FakeJev(keyword_rule, max_state_tokens=5), llm=llm)
    themes = [Theme(**t) for t in THEMES["themes"]]
    run = _run(["camera", "camera " + "x" * 400])
    report = await Group().run(run, GroupParams(themes=themes), ctx)
    a, b = run.items
    assert a.fields["theme"] == "Camera Crash"
    assert "theme" not in b.fields and "theme__error" in b.fields
    assert any(n.startswith("i1: skipped (StateTooLarge") for n in report.notes)


def test_validator_dedupes_and_trims() -> None:
    raw = {
        "themes": [
            {"name": "Camera Crash", "description": "a"},
            {"name": " camera crash ", "description": "dup"},
            {"name": "", "description": "blank"},
            {"name": 3, "description": "not a string"},
            "junk",
            {"name": "None_of_these", "description": "reserved"},
            {"name": "Battery", "description": "b"},
            {"name": "Login", "description": "c"},
        ]
    }
    assert [t.name for t in validate_themes(raw, 5)] == ["Camera Crash", "Battery", "Login"]
    assert [t.name for t in validate_themes(raw, 2)] == ["Camera Crash", "Battery"]
    existing = [Theme(name="battery", description="")]
    assert [t.name for t in validate_themes(raw, 5, existing)] == ["Camera Crash", "Login"]
    assert validate_themes({"nope": []}, 5) == []
    assert validate_themes(["not", "a", "dict"], 5) == []


def test_params_validation() -> None:
    with pytest.raises(ValidationError):
        GroupParams(n_themes=1)
    with pytest.raises(ValidationError):
        GroupParams(n_themes=31)
    with pytest.raises(ValidationError, match="repeated"):
        GroupParams(themes=[Theme(name="A"), Theme(name="a")])
    with pytest.raises(ValidationError, match="2 to"):
        GroupParams(themes=[Theme(name="A")])
    assert GroupParams().instructions == "Which theme best describes the text?"
    p = GroupParams(focus="the complaint in the text")
    assert p.instructions == "Which theme best describes the complaint in the text?"


def test_slugify() -> None:
    used: set[str] = set()
    assert slugify("Camera Crash!", used) == "camera_crash"
    assert slugify("camera  crash", used) == "camera_crash_2"
    assert slugify("None of these", used) == "none_of_these_2"
    assert slugify("???", used) == "theme"


def test_registered() -> None:
    tool = get_tool("group")
    assert isinstance(tool, Group) and tool.uses == "jev+llm"
    entry = next(e for e in catalog() if e["name"] == "group")
    assert "Use Group when the categories aren't known in advance" in entry["description"]
    assert "use Classify when they are" in entry["description"]
