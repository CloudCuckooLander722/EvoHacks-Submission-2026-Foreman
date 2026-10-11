"""The runs API end to end on FakeJev/FakeLLM: upload, SSE stream, results, limits."""

import json
import threading
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import foreman.api.app as api_app
import foreman.api.runs as api_runs
from foreman.api.app import create_app, get_context_factory
from foreman.api.extract import fallback_extract
from foreman.api.settings import ApiSettings
from foreman.contracts import ResultsFile
from foreman.core.context import make_context
from foreman.core.ledger import RunStats
from foreman.testing.fakes import FakeLLM

SAMPLE = Path("data/samples/reviews_small.json").resolve()
PLANS = Path("data/plans").resolve()
THEMES = {
    "themes": [
        {"name": "Camera Crash", "description": "The app crashes when the camera opens."},
        {"name": "Login Trouble", "description": "The user is logged out or cannot log in."},
        {"name": "Slow App", "description": "Something in the app is slow or late."},
    ]
}
DECISION_KEYS = {
    "item_id", "node", "tool", "question_id", "qtype", "value", "confidence", "decided_by",
    "jev_value", "jev_confidence", "low_confidence", "cost_usd", "ts",
}  # fmt: skip
NODE_KEYS = {"node", "tool", "index", "total", "status"}


def _no_minh(module: str, name: str):
    """Pretend foreman.format / extract_files are not built, so tests are deterministic."""
    return


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # runs/ goes to tmp
    monkeypatch.setattr(api_app, "load_optional", _no_minh)
    monkeypatch.setattr(api_runs, "load_optional", _no_minh)
    monkeypatch.setattr("foreman.api.extract.load_optional", _no_minh)


def fake_factory():
    return partial(make_context, fake=True, llm=FakeLLM(json_responses={"propose": THEMES}))


def make_client(factory=fake_factory, **settings) -> TestClient:
    app = create_app(ApiSettings(plans_dir=PLANS, **settings), warm=False)
    app.dependency_overrides[get_context_factory] = factory
    return TestClient(app)


def post_run(client: TestClient, path: Path = SAMPLE, **form) -> str:
    form.setdefault("plan", "reviews_backlog")
    with path.open("rb") as f:
        r = client.post("/runs", files={"file": (path.name, f)}, data=form)
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


def read_events(client: TestClient, run_id: str) -> list[tuple[str, dict]]:
    """Parse the SSE stream until the server closes it (after done or a fatal error)."""
    events, name = [], None
    with client.stream("GET", f"/runs/{run_id}/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                events.append((name, json.loads(line.split(":", 1)[1])))
    return events


def test_run_streams_plan_nodes_done_and_serves_results(tmp_path) -> None:
    with make_client() as client:
        run_id = post_run(client, threshold="0.7", job="Make a bug backlog")
        assert run_id.startswith("r_")
        events = read_events(client, run_id)

        names = [n for n, _ in events]
        assert names[0] == "plan" and names[-1] == "done"
        assert set(names) <= {"plan", "node", "decision", "stats", "error", "done"}
        assert "error" not in names

        plan = events[0][1]
        assert plan["run_id"] == run_id and plan["job"] == "Make a bug backlog"
        assert plan["items_total"] == 25
        assert [n["id"] for n in plan["nodes"]] == [f"n{i}" for i in range(1, 8)]
        assert {"id", "tool", "params", "why"} <= set(plan["nodes"][0])

        nodes = [d for n, d in events if n == "node"]
        assert all(NODE_KEYS <= set(d) for d in nodes)
        finished = [(d["node"], d["status"]) for d in nodes if d["status"] != "running"]
        assert finished == [(f"n{i}", "done") for i in range(1, 8)]
        done_n2 = next(d for d in nodes if d["node"] == "n2" and d["status"] == "done")
        assert done_n2["report"]["tool"] == "classify" and "jev_decisions" in done_n2["report"]
        progress = [d for d in nodes if d["status"] == "running" and "done" in d]
        assert progress and all("items" in d for d in progress)

        decisions = [d for n, d in events if n == "decision"]
        assert decisions  # the final flush always sends the latest row
        for d in decisions:
            assert set(d) == DECISION_KEYS and d["decided_by"] in ("jev", "llm", "code")
        stats = [d for n, d in events if n == "stats"]
        assert stats and all(RunStats.model_validate(s) for s in stats)
        assert events[-1][1] == {"run_id": run_id, "results_url": f"/runs/{run_id}/results"}

        r = client.get(f"/runs/{run_id}/results")
        assert r.status_code == 200
        results = ResultsFile.model_validate(r.json())
        assert results.run_id == run_id and results.threshold == 0.7
        assert stats[-1] == results.stats.model_dump()  # the meter ends on results.json
        assert "escalation_curve" in results.aggregates

        rows = client.get(f"/runs/{run_id}/decisions", params={"item_id": "row-2"}).json()
        assert rows and all(row["item_id"] == "row-2" for row in rows)

        # a client that connects after the run gets the same events again (replay)
        assert read_events(client, run_id) == events

    recorded = (tmp_path / "runs" / run_id / "events.jsonl").read_text().splitlines()
    rows = [json.loads(line) for line in recorded]
    assert [(r["event"], r["data"]) for r in rows] == events
    assert all(isinstance(r["t_ms"], int) for r in rows)
    assert (tmp_path / "runs" / run_id / "input" / SAMPLE.name).exists()


def test_export_is_501_until_the_formatter_exists() -> None:
    with make_client() as client:
        run_id = post_run(client)
        read_events(client, run_id)
        r = client.get(f"/runs/{run_id}/export", params={"format": "xlsx"})
        assert r.status_code == 501 and r.json()["detail"] == "export not built yet"


def test_csv_upload_uses_the_fallback_reader(tmp_path) -> None:
    csv_path = tmp_path / "reviews.csv"
    csv_path.write_text(
        "rating,review,date\n"
        "1,The app crashes every time I open the camera on my Pixel,2026-09-30\n"
        "5,Love this app so much it is a solid app,2026-09-29\n"
        ",,\n"
        "2,I keep getting logged out every single day after the update,2026-09-28\n",
        encoding="utf-8",
    )
    items = fallback_extract(csv_path)
    assert [i.id for i in items.items] == ["row-2", "row-3", "row-5"]
    first = items.items[0]
    assert first.text.startswith("The app crashes")
    assert first.fields == {
        "rating": 1,
        "review": first.text,
        "date": "2026-09-30",
    }
    assert first.meta == {"row": 2}
    assert items.source["text_column"] == "review"

    with make_client() as client:
        run_id = post_run(client, csv_path)
        events = read_events(client, run_id)
        assert events[0][0] == "plan" and events[0][1]["items_total"] == 3
        assert events[-1][0] == "done"


def test_txt_fallback_and_bad_upload_become_a_fatal_error_event(tmp_path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("first line\n\nsecond line\n", encoding="utf-8")
    assert [(i.id, i.text) for i in fallback_extract(txt).items] == [
        ("line-1", "first line"),
        ("line-3", "second line"),
    ]

    bad = tmp_path / "data.json"
    bad.write_text('{"not": "items"}', encoding="utf-8")
    with make_client() as client:
        run_id = post_run(client, bad)
        events = read_events(client, run_id)
        assert [n for n, _ in events] == ["error"]
        err = events[0][1]
        assert err["fatal"] is True and err["node"] is None
        assert err["message"].startswith("Could not read the file")
        r = client.get(f"/runs/{run_id}/results")
        assert r.status_code == 404 and r.json()["detail"]["status"] == "failed"
        assert client.get("/health").json()["active_runs"] == 0


def test_max_active_runs_returns_429_and_results_404_while_running() -> None:
    release = threading.Event()

    async def slow_context(**kwargs):
        import asyncio

        await asyncio.to_thread(release.wait, 10)
        return await fake_factory()(**kwargs)

    with make_client(lambda: slow_context, max_active_runs=1) as client:
        run_id = post_run(client)
        r = client.get(f"/runs/{run_id}/results")
        assert r.status_code == 404 and r.json()["detail"]["status"] == "planning"

        with SAMPLE.open("rb") as f:
            r = client.post(
                "/runs", files={"file": ("x.json", f)}, data={"plan": "reviews_backlog"}
            )
        assert r.status_code == 429 and "limit is 1" in r.json()["detail"]
        assert client.get("/health").json()["active_runs"] == 1

        release.set()
        assert read_events(client, run_id)[-1][0] == "done"
        assert client.get("/health").json()["active_runs"] == 0


def test_bad_requests_are_rejected_before_a_run_starts() -> None:
    with make_client() as client:
        with SAMPLE.open("rb") as f:
            files = {"file": ("s.json", f.read())}
        assert client.post("/runs", files=files, data={"plan": "nope"}).status_code == 400
        assert client.post("/runs", files=files, data={"plan": "../x"}).status_code == 400
        assert client.post("/runs", files=files, data={}).status_code == 400  # no job, no plan
        r = client.post("/runs", files=files, data={"plan": "reviews_backlog", "threshold": "2"})
        assert r.status_code == 400
        assert client.get("/runs/r_missing/events").status_code == 404
        assert client.get("/runs/r_missing/results").status_code == 404
        health = client.get("/health").json()
        assert set(health["keys"]) == {"jev", "llm"}
        assert all(isinstance(v, bool) for v in health["keys"].values())
