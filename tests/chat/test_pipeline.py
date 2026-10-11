"""The chat's pipeline layer: real planner/executor/tools on fakes, checked against disk."""

import json
from pathlib import Path

import pytest

from foreman.chat import pipeline
from foreman.chat.pipeline import (
    DATASETS,
    ChatRunError,
    JobRequest,
    build_view,
    live_keys_missing,
    load_replay,
    load_run,
    run_job,
)
from foreman.config import get_settings
from foreman.contracts import ResultsFile
from foreman.core.context import make_context
from foreman.testing.fakes import FakeAnswer, FakeJev, FakeLLM

SMALL = DATASETS["25 sample app reviews"]

PLAN = {
    "nodes": [
        {
            "id": "n1",
            "tool": "classify",
            "why": "Split bugs from the rest.",
            "params": {
                "options": {
                    "bug": "Something is broken, crashes or fails",
                    "praise": "Positive feedback, great, love",
                    "other": None,
                },
                "output_field": "type",
            },
        },
        {
            "id": "n2",
            "tool": "calculate",
            "why": "Count each type.",
            "params": {"operations": [{"op": "group_count", "by": "type", "as": "type_counts"}]},
        },
    ]
}


@pytest.fixture(autouse=True)
def _runs_in_tmp(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here; runs/ lands in tmp
    for var in ("AI_GATEWAY_API_KEY", "FIREWORKS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _on_disk(results: ResultsFile) -> ResultsFile:
    path = Path("runs") / results.run_id / "results.json"
    return ResultsFile.model_validate_json(path.read_text(encoding="utf-8"))


async def test_offline_run_uses_saved_plan_and_matches_results_on_disk() -> None:
    events: list[dict] = []
    req = JobRequest(job="Make a backlog", items_path=SMALL, mode="offline", max_items=200)
    results, plan = await run_job(req, events.append)

    assert [n.tool for n in plan.nodes][:2] == ["filter", "classify"]
    assert plan.job == "Make a backlog"
    assert events[0] == {"type": "loaded", "items": 25, "total": 25}
    assert events[1]["type"] == "plan"
    finished = [e["node"] for e in events if e["type"] == "node" and e["status"] == "done"]
    assert finished == [r.node for r in results.node_reports]

    disk = _on_disk(results)
    view = build_view(disk, plan, 25)
    assert view["items_in"] == 25
    assert view["items_out"] + view["dropped"] == 25
    assert view["decisions"]["total"] == disk.stats.decisions_total > 0
    assert sum(view["decisions"][k] for k in ("jev", "llm", "code")) == view["decisions"]["total"]
    assert json.loads(view["results_json"])["run_id"] == results.run_id


async def test_live_mode_runs_the_llm_planner(monkeypatch) -> None:
    """The planner path, with the LLM's plan coming from FakeLLM instead of Fireworks."""

    def bug_or_praise(state, qid, q):
        text = str(state).lower()
        return FakeAnswer("bug" if "crash" in text else "praise", confidence=0.95)

    async def fake_ctx(**kw):
        llm = FakeLLM(json_responses={"You are planning a bulk job": PLAN})
        return await make_context(jev=FakeJev(rule=bug_or_praise), llm=llm, **kw)

    monkeypatch.setattr(pipeline, "make_context", lambda **kw: fake_ctx(**kw))
    req = JobRequest(job="Count bugs", items_path=SMALL, mode="live", plan_source="planner")
    results, plan = await run_job(req, lambda ev: None)

    assert [n.id for n in plan.nodes] == ["n1", "n2"]
    counts = results.aggregates["type_counts"]
    crashes = sum("crash" in i.text.lower() for i in results.items)
    assert crashes > 0
    assert counts == {"bug": crashes, "praise": 25 - crashes}
    view = build_view(_on_disk(results), plan, 25)
    assert view["counts"]["type_counts"] == counts
    assert view["decisions"]["jev"] == 25
    assert view["table"][0].keys() >= {"id", "text", "type"}


async def test_planner_failure_is_a_readable_error(monkeypatch) -> None:
    async def fake_ctx(**kw):
        return await make_context(jev=FakeJev(), llm=FakeLLM(), **kw)  # no plan to give

    monkeypatch.setattr(pipeline, "make_context", lambda **kw: fake_ctx(**kw))
    req = JobRequest(job="anything", items_path=SMALL, mode="live")
    with pytest.raises(ChatRunError, match="Planning failed"):
        await run_job(req, lambda ev: None)


def test_max_items_caps_the_run() -> None:
    run, total = load_run(SMALL, "job", max_items=5)
    assert len(run.items) == 5 and total == 25


def test_csv_upload_is_read(tmp_path) -> None:
    f = tmp_path / "r.csv"
    f.write_text("review,stars\nThe app crashes on launch every time,1\nLove it,5\n")
    run, total = load_run(f, "job", max_items=10)
    assert total == 2
    assert run.items[0].text == "The app crashes on launch every time"
    assert run.items[0].fields["stars"] == 1


@pytest.mark.parametrize(
    ("name", "body"),
    [("x.json", "not json"), ("x.pdf", "%PDF"), ("x.json", '{"items": []}')],
)
def test_bad_files_raise_chat_errors(tmp_path, name, body) -> None:
    f = tmp_path / name
    f.write_text(body)
    with pytest.raises(ChatRunError, match="Could not read the file"):
        load_run(f, "job", 10)


def test_replay_view_matches_the_recorded_index() -> None:
    index = json.loads((pipeline.REPLAY_DIR.parent / "index.json").read_text())["replays"][0]
    results, plan, items_in = load_replay()
    view = build_view(results, plan, items_in)

    assert items_in == index["items"]
    assert view["decisions"]["total"] == index["decisions"]["total"]
    assert view["decisions"]["jev"] == index["decisions"]["jev"]
    assert view["cost_usd"] == pytest.approx(index["cost_usd"], abs=1e-6)
    assert view["jev_share_of_model"] == pytest.approx(index["jev_share_of_model"], abs=1e-4)
    order = results.aggregates["rank_order"]
    assert [r["id"] for r in view["ranked"]["rows"]] == order[: len(view["ranked"]["rows"])]
    assert "theme_counts" in view["counts"] and "type_counts" in view["counts"]
    json.dumps({k: v for k, v in view.items() if k != "results_json"})  # JSON-safe


def test_missing_replay_is_a_chat_error(tmp_path) -> None:
    with pytest.raises(ChatRunError, match="missing"):
        load_replay(tmp_path)


def test_live_keys_missing_names_both_keys(monkeypatch) -> None:
    assert live_keys_missing() == ["AI_GATEWAY_API_KEY", "FIREWORKS_API_KEY"]
    monkeypatch.setenv("FIREWORKS_API_KEY", "x")
    get_settings.cache_clear()
    assert live_keys_missing() == ["AI_GATEWAY_API_KEY"]
