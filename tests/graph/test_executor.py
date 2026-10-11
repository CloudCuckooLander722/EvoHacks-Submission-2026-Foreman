"""execute: the hero plan end to end on FakeJev/FakeLLM, and partial results on failure."""

import json
from pathlib import Path

import pytest

from foreman.contracts import ResultsFile, load_items
from foreman.core.context import make_context
from foreman.core.questions import Choice, Noul, Score
from foreman.graph.executor import execute
from foreman.graph.planner import hero_plan
from foreman.testing.fakes import FakeAnswer, FakeJev, FakeLLM
from foreman.tools.dedupe import ISSUE_LEVELS
from foreman.tools.group import NONE

SAMPLE = Path("data/samples/reviews_small.json").resolve()
SPAM = ("cheap-bags", "crypto")
FEATURE = ("add ", "would love", "would be", "option", "widgets")
BILLING = ("charged", "billing")
PRAISE = ("love this", "solid app", "five stars", "nice update")
THEMES = {
    "themes": [
        {"name": "Camera Crash", "description": "The app crashes when the camera opens."},
        {"name": "Login Trouble", "description": "The user is logged out or cannot log in."},
        {"name": "Slow App", "description": "Something in the app is slow or late."},
        {"name": "Feature Ideas", "description": "A request for something new."},
    ]
}


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text.lower() for w in words)


def rule(state, qid, q):
    """Deterministic answers for every question the hero plan asks."""
    text = state if isinstance(state, str) else json.dumps(state).lower()
    if isinstance(q, Noul):  # filter keep / dedupe "is a problem"
        return FakeAnswer(0.05 if _has(text, SPAM) else 0.95)
    if isinstance(q, Choice) and "bug" in q.options:  # classify
        for key, words in (("billing", BILLING), ("praise", PRAISE), ("feature_request", FEATURE)):
            if _has(text, words):
                return FakeAnswer(key, confidence=0.9)
        return FakeAnswer("bug", confidence=0.55)  # below 0.7: escalates to the LLM
    if isinstance(q, Choice) and NONE in q.options:  # group assignment
        for slug, words in (
            ("camera_crash", ("camera",)),
            ("login_trouble", ("log",)),
            ("feature_ideas", FEATURE),
        ):
            if _has(text, words):
                return FakeAnswer(slug, confidence=0.9)
        return FakeAnswer("slow_app", confidence=0.8)
    if isinstance(q, Score) and q.levels == ISSUE_LEVELS:  # dedupe pair
        same = "camera" in text and text.count("camera") >= 2
        return FakeAnswer(3.0 if same else 0.0, confidence=0.9)
    if isinstance(q, Score) and qid == "dim_severity":
        return FakeAnswer(3.0 if "crash" in text else 2.0, confidence=0.85)
    return None


async def _run(llm: FakeLLM, events: list):
    run = load_items(SAMPLE)
    ctx = await make_context(run_id=run.run_id, fake=True, jev=FakeJev(rule), llm=llm)
    results = await execute(hero_plan(), run, ctx, on_node=events.append)
    return results, run


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


async def test_hero_plan_runs_every_node(tmp_path) -> None:
    events: list[dict] = []
    llm = FakeLLM(rule=rule, json_responses={"propose exactly": THEMES})
    results, run = await _run(llm, events)

    assert [r.node for r in results.node_reports] == [f"n{i}" for i in range(1, 8)]
    assert "__failed_nodes" not in results.aggregates
    statuses = [(e["node"], e["status"]) for e in events if "done" not in e]
    assert statuses == [(f"n{i}", s) for i in range(1, 8) for s in ("running", "done")]
    assert events[0]["why"] and events[0]["index"] == 0 and events[0]["total"] == 7

    # results.json on disk matches CONTRACTS.md (round-trips through the model)
    path = tmp_path / "runs" / run.run_id / "results.json"
    on_disk = ResultsFile.model_validate_json(path.read_text())
    assert on_disk == results
    assert (tmp_path / "runs" / run.run_id / "plan.json").exists()
    assert (tmp_path / results.decisions_file).exists()
    assert results.plan == hero_plan().model_dump(mode="json")
    assert results.job == "Turn these reviews into a ranked bug backlog"

    assert {d.id for d in results.dropped} == {"row-10", "row-16"}
    assert all(d.reason.startswith("filter:") for d in results.dropped)
    by_id = {i.id: i for i in results.items}
    cam = by_id["row-2"].fields
    assert cam["type"] == "bug" and cam["type__by"] == "llm"  # escalated at 0.55
    assert cam["theme"] == "Camera Crash" and cam["dup_group"] is not None
    assert cam["dup_group_canonical"] is True and cam["dup_group_count"] == 3
    assert cam["urgency"] == 100.0 and cam["rank"] == 1
    for copy in ("row-3", "row-4"):  # duplicates are labelled but not ranked
        f = by_id[copy].fields
        assert f["dup_group"] == cam["dup_group"] and f["dup_group_canonical"] is False
        assert "rank" not in f
    assert by_id["row-2"].meta == {"row": 2}

    agg = results.aggregates
    assert agg["theme_counts"]["Camera Crash"] == 3
    assert agg["n_items"] == len(results.items) == 23
    assert agg["rank_order"][0] == "row-2"
    assert not {"row-3", "row-4"} & set(agg["rank_order"])
    bugs = [i for i in results.items if i.fields.get("type") == "bug"]
    assert len(agg["rank_order"]) == len(bugs) - 2  # one entry per duplicate group
    curve = agg["escalation_curve"]
    assert len(curve) == 21 and curve[0] == [0.0, 0]
    assert curve[-1][1] > 0 and any(0 < n < curve[-1][1] for _, n in curve)
    st = results.stats
    assert st.by["jev"] > 0 and st.by["llm"] > 0 and st.by["code"] > 0


async def test_failed_node_skips_only_its_dependents() -> None:
    events: list[dict] = []
    llm = FakeLLM(rule=rule)  # no theme proposal -> group raises GroupError
    results, _ = await _run(llm, events)

    final = {e["node"]: e["status"] for e in events if e["status"] != "running"}
    assert final == {
        "n1": "done",
        "n2": "done",
        "n3": "failed",  # group
        "n4": "skipped",  # dedupe blocks by theme
        "n5": "done",  # score: independent of theme
        "n6": "skipped",  # rank keeps one copy per dedupe group
        "n7": "skipped",  # calculate groups by theme
    }
    failed = results.aggregates["__failed_nodes"]
    assert [(f["node"], f["status"]) for f in failed] == [
        ("n3", "failed"),
        ("n4", "skipped"),
        ("n6", "skipped"),
        ("n7", "skipped"),
    ]
    assert "GroupError" in failed[0]["error"] and "theme" in failed[1]["error"]
    assert "dup_group_canonical" in failed[2]["error"]
    assert [r.node for r in results.node_reports] == ["n1", "n2", "n5"]
    assert results.aggregates["urgency_summary"]["n"] > 0
    assert "escalation_curve" in results.aggregates
