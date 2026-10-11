"""Offline tests for the Streamlit chat UI, driven through Streamlit's AppTest."""

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from foreman.config import get_settings
from foreman.core.llm import LLMError
from foreman.testing.fakes import FakeLLM

APP = str(Path(__file__).parents[1] / "web" / "chat_interface.py")


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch, tmp_path):
    """The cached client would otherwise leak from one test into the next."""
    monkeypatch.chdir(tmp_path)  # no .env here, so only the test's env vars count
    for var in ("CHAT_FAKE", "CHAT_ACCESS_KEY", "FIREWORKS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    st.cache_resource.clear()
    get_settings.cache_clear()
    yield
    st.cache_resource.clear()
    get_settings.cache_clear()


@pytest.fixture
def app(monkeypatch) -> AppTest:
    monkeypatch.setenv("CHAT_FAKE", "1")
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    return at


def _send(at: AppTest, text: str) -> None:
    at.chat_input[0].set_value(text).run()
    assert not at.exception, at.exception


def test_several_turns_reuse_one_loop(app) -> None:
    _send(app, "hello")
    _send(app, "second")
    _send(app, "third")
    texts = [m.markdown[0].value for m in app.chat_message]
    assert texts == [
        "hello",
        "(FakeLLM) You said: hello",
        "second",
        "(FakeLLM) You said: second",
        "third",
        "(FakeLLM) You said: third",
    ]


def test_clear_chat(app) -> None:
    _send(app, "hello")
    app.sidebar.button[0].click().run()
    assert len(app.chat_message) == 0
    assert app.session_state["messages"] == []


def test_failed_call_shows_error_and_drops_turn(monkeypatch) -> None:
    async def boom(self, messages):
        raise LLMError("provider down")

    monkeypatch.setenv("CHAT_FAKE", "1")
    monkeypatch.setattr(FakeLLM, "chat", boom)
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.chat_input[0].set_value("hi").run()
    assert not at.exception
    assert "model call failed" in at.error[0].value
    assert at.session_state["messages"] == []


def test_missing_key_stops_with_message() -> None:
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    assert "FIREWORKS_API_KEY is not set" in at.error[0].value
    assert len(at.chat_input) == 0


def test_live_mode_refuses_without_access_key(monkeypatch) -> None:
    monkeypatch.setenv("FIREWORKS_API_KEY", "dummy")
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception
    assert "CHAT_ACCESS_KEY is not set" in at.error[0].value
    assert len(at.chat_input) == 0


def _gated_app(monkeypatch) -> AppTest:
    monkeypatch.setenv("CHAT_FAKE", "1")
    monkeypatch.setenv("CHAT_ACCESS_KEY", "s3cret")
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert len(at.chat_input) == 0  # nothing to chat with until the key is entered
    return at


def test_wrong_access_key_is_rejected(monkeypatch) -> None:
    at = _gated_app(monkeypatch)
    at.text_input[0].input("nope")
    at.button[0].click().run()
    assert "Wrong access key" in at.error[0].value
    assert len(at.chat_input) == 0


def test_right_access_key_opens_chat(monkeypatch) -> None:
    at = _gated_app(monkeypatch)
    at.text_input[0].input("s3cret")
    at.button[0].click().run()
    assert not at.exception
    _send(at, "hello")
    assert at.chat_message[1].markdown[0].value == "(FakeLLM) You said: hello"
