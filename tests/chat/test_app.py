"""The chat page end to end through Streamlit's AppTest: the real script, real pipeline."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from foreman.chat import pipeline
from foreman.config import get_settings
from foreman.contracts import ResultsFile

APP = str(Path(pipeline.__file__).with_name("app.py"))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # no .env here, so no keys; runs/ lands in tmp
    for var in ("AI_GATEWAY_API_KEY", "FIREWORKS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _app() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    return at


def _send(at: AppTest, text: str) -> None:
    at.chat_input[0].set_value(text).run()
    assert not at.exception, at.exception


def _metric(at: AppTest, label: str) -> str:
    return next(m.value for m in at.metric if m.label == label)


def test_no_access_key_prompt() -> None:
    at = _app()
    assert len(at.text_input) == 0
    assert len(at.chat_input) == 1


def test_without_keys_defaults_to_the_recorded_run() -> None:
    at = _app()
    assert at.sidebar.radio[0].value == "replay"
    _send(at, "Turn these app reviews into a ranked bug backlog")

    index = json.loads((pipeline.REPLAY_DIR.parent / "index.json").read_text())["replays"][0]
    assert _metric(at, "Decisions") == f"{index['decisions']['total']:,}"
    assert _metric(at, "Cost") == f"${index['cost_usd']:.4f}"
    assert any("Recorded live run" in c.value for c in at.caption)
    assert len(at.get("download_button")) == 1
    # The replay's ranked table and charts render from real aggregates.
    assert any("Top 10" in m.value for m in at.markdown)


def test_offline_run_shows_the_pipeline_output() -> None:
    at = _app()
    at.sidebar.radio[0].set_value("offline").run()
    _send(at, "Make a bug backlog")

    msg = at.session_state["messages"][-1]
    assert "error" not in msg, msg.get("error")
    view = msg["view"]
    disk = ResultsFile.model_validate_json(
        (Path("runs") / view["run_id"] / "results.json").read_text()
    )
    assert _metric(at, "Decisions") == f"{disk.stats.decisions_total:,}"
    assert _metric(at, "Items") == f"25 → {len(disk.items)}"
    assert [p["node"] for p in view["plan"]] == [n["id"] for n in disk.plan["nodes"]]
    assert any(line.startswith("✓ n1 filter") for line in msg["log"])
    assert any("Offline stand-ins" in c.value for c in at.caption)


def test_two_jobs_then_clear() -> None:
    at = _app()
    at.sidebar.radio[0].set_value("offline").run()
    _send(at, "first")
    _send(at, "second")
    roles = [m["role"] for m in at.session_state["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    ids = [m["view"]["run_id"] for m in at.session_state["messages"] if "view" in m]
    assert len(set(ids)) == 2  # two real runs, not one reused
    at.sidebar.button[0].click().run()
    assert at.session_state["messages"] == []
    assert len(at.chat_message) == 0


def test_live_without_keys_explains_what_is_missing() -> None:
    at = _app()
    at.sidebar.radio[0].set_value("live").run()
    assert "AI_GATEWAY_API_KEY" in at.sidebar.warning[0].value
    _send(at, "anything")
    assert "Live mode needs AI_GATEWAY_API_KEY, FIREWORKS_API_KEY" in at.error[0].value


def test_unreadable_file_shows_error(monkeypatch, tmp_path) -> None:
    bad = tmp_path / "broken.json"
    bad.write_text("{ not json")
    monkeypatch.setitem(pipeline.DATASETS, "broken file", bad)
    at = _app()
    at.sidebar.radio[0].set_value("offline").run()
    at.sidebar.selectbox[0].set_value("broken file").run()
    _send(at, "anything")
    assert "Could not read the file" in at.error[0].value
    assert "view" not in at.session_state["messages"][-1]
