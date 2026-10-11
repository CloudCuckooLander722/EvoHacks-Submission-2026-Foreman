"""Streamlit chat UI over Foreman's light LLM (DeepSeek V4 Flash on Fireworks).

Run from the repo root (so `.env` is found):

    streamlit run my-adk-agent/web/chat_interface.py

Set `CHAT_FAKE=1` to chat with `FakeLLM` offline, without a `FIREWORKS_API_KEY`.

Live mode also needs `CHAT_ACCESS_KEY`: every visitor must type it before chatting, so a
public URL cannot spend the Fireworks credit. Both keys can live in `.env` or the env.

Streamlit re-runs this whole script on every interaction, but `LLMClient` is async and its
SDK client binds to the event loop it first runs on. A fresh `asyncio.run()` per message
would hand it a new loop each time and fail from the second message on, so one loop lives
in a background thread for the life of the server and every call is submitted to it.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import threading
from collections.abc import Coroutine
from typing import Any, Protocol

import streamlit as st
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from foreman.core.llm import LLMClient, LLMError
from foreman.testing.fakes import FakeLLM

log = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = "You are a helpful, concise assistant."
# Only the most recent turns are sent, so a long chat cannot outgrow the context window.
MAX_HISTORY_MESSAGES = 20
CALL_TIMEOUT_S = 120


class ChatSettings(BaseSettings):
    """The chat app's own settings; Foreman's `Settings` still holds the Fireworks key."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    chat_fake: bool = False
    chat_access_key: SecretStr | None = None


class ChatLLM(Protocol):
    """What the UI needs: `LLMClient` and `FakeLLM` both satisfy it."""

    async def chat(self, messages: list[dict[str, str]]) -> str: ...


@st.cache_resource
def _event_loop() -> asyncio.AbstractEventLoop:
    """One loop per server process, shared by every session (see module docstring)."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, name="chat-llm-loop", daemon=True).start()
    return loop


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine on the shared loop and block this script run until it finishes."""
    return asyncio.run_coroutine_threadsafe(coro, _event_loop()).result(CALL_TIMEOUT_S)


def _payload(system_prompt: str, history: list[dict[str, str]]) -> list[dict[str, str]]:
    """System prompt plus the recent turns, in the role/content shape `chat()` takes."""
    return [{"role": "system", "content": system_prompt}, *history[-MAX_HISTORY_MESSAGES:]]


def run_chat_interface(llm: ChatLLM, system_prompt: str = DEFAULT_SYSTEM_PROMPT) -> None:
    """Render the chat page for one script run, sending new user messages to `llm`.

    The caller owns `llm` (normally cached with `st.cache_resource`), so tests and other
    pages can pass in a `FakeLLM` or a differently configured `LLMClient`.
    """
    st.title("Chat")
    model = getattr(llm, "model", type(llm).__name__)
    st.caption(f"Model: `{model}`")

    history: list[dict[str, str]] = st.session_state.setdefault("messages", [])

    with st.sidebar:
        if st.button("Clear chat", use_container_width=True):
            history.clear()
            st.rerun()

    for msg in history:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    prompt = st.chat_input("Ask anything")
    if not prompt:
        return

    history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        try:
            with st.spinner("Thinking..."):
                reply = _run(llm.chat(_payload(system_prompt, history)))
        except (LLMError, TimeoutError) as e:
            log.warning("chat call failed: %s", e)
            # Drop the unanswered turn so the user can simply resend it.
            history.pop()
            st.error(f"The model call failed: {e}")
            return
        st.markdown(reply or "_(empty reply)_")
    history.append({"role": "assistant", "content": reply})


@st.cache_resource
def _default_llm(fake: bool) -> ChatLLM:
    """The app's client: a live `LLMClient`, or `FakeLLM` when `CHAT_FAKE=1`."""
    return FakeLLM() if fake else LLMClient()


def _access_granted(expected: SecretStr) -> bool:
    """Ask for the access key once per browser session; True once it has been entered."""
    if st.session_state.get("access_granted"):
        return True
    st.title("Chat")
    with st.form("access"):
        given = st.text_input("Access key", type="password")
        submitted = st.form_submit_button("Enter")
    if submitted:
        # Constant-time compare, so response timing does not leak the key.
        if hmac.compare_digest(given.encode(), expected.get_secret_value().encode()):
            st.session_state["access_granted"] = True
            st.rerun()
        st.error("Wrong access key.")
    return False


def main() -> None:
    """Entry point for `streamlit run`."""
    st.set_page_config(page_title="Chat", page_icon="💬")
    settings = ChatSettings()
    llm = _default_llm(settings.chat_fake)
    if isinstance(llm, LLMClient):
        if llm.settings.fireworks_api_key is None:
            st.error("FIREWORKS_API_KEY is not set. Add it to `.env`, or run with CHAT_FAKE=1.")
            st.stop()
        if settings.chat_access_key is None:
            st.error("CHAT_ACCESS_KEY is not set. Live chat refuses to run without one.")
            st.stop()
    if settings.chat_access_key is not None and not _access_granted(settings.chat_access_key):
        st.stop()
    run_chat_interface(llm)


if __name__ == "__main__":
    main()
