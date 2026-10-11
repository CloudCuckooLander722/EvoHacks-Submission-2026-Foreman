"""Streamlit chat window over the Foreman pipeline: type a job, watch it plan and run.

Run from the repo root (so `.env` is found):

    streamlit run foreman/chat/app.py

Each message is a job ("Turn these reviews into a ranked bug backlog"). The reply shows the
plan, live progress per node, then the results: the Jev / LLM / code split, cost, time, the
top-ranked items, counts and the full results table, plus a results.json download.

Three sources, picked in the sidebar and always labelled in the reply:
- **Live models:** Jev + the light LLM, planner included. Needs both keys in `.env`.
- **Recorded live run:** replays the saved 1,000-review run from `web/public/replays/`.
- **Offline stand-ins:** FakeJev / FakeLLM keyword heuristics with the saved plan. Shows
  the pipeline working end to end without keys, but its labels are rough.

There is no access key. Anyone who can open a live deployment can spend the API credit,
so only share a live URL with people you trust.

Streamlit re-runs this script on every interaction, so the async run happens in a worker
thread and reports to this thread through a queue; the script polls it to draw progress.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from foreman.chat.pipeline import (
    DATASETS,
    ChatRunError,
    Event,
    JobRequest,
    Mode,
    PlanSource,
    build_view,
    live_keys_missing,
    load_replay,
    run_job,
)

log = logging.getLogger(__name__)

MODE_LABELS: dict[Mode, str] = {
    "live": "Live models (Jev + LLM)",
    "replay": "Recorded live run (1,000 reviews)",
    "offline": "Offline stand-ins (no keys, rough labels)",
}
DEFAULT_JOB = "Turn these app reviews into a ranked bug backlog"
POLL_S = 0.1
RUN_TIMEOUT_S = 30 * 60


@dataclass
class ChatOptions:
    """The sidebar's choices for the next job."""

    mode: Mode
    items_path: Path | None = None
    plan_source: PlanSource = "planner"
    threshold: float = 0.7
    max_items: int = 200


# ------------------------------------------------------------------------------ rendering


def render_view(view: dict[str, Any]) -> None:
    """Draw one finished run: plan, numbers, results. Used live and on every rerun."""
    st.markdown(f"**Plan** for _{view['job']}_ ({len(view['plan'])} steps)")
    st.dataframe(pd.DataFrame(view["plan"]), hide_index=True, use_container_width=True)

    d = view["decisions"]
    cols = st.columns(5)
    cols[0].metric("Items", f"{view['items_in']} → {view['items_out']}")
    cols[1].metric("Decisions", f"{d['total']:,}")
    cols[2].metric("By Jev", f"{100 * view['jev_share_of_model']:.1f}%")
    cols[3].metric("Cost", f"${view['cost_usd']:.4f}")
    cols[4].metric("Time", f"{view['wall_time_s']:.1f}s")
    st.caption(
        f"Jev {d['jev']:,} · LLM {d['llm']:,} · code {d['code']:,} · "
        f"low confidence {view['low_confidence']} · dropped {view['dropped']} · "
        f"threshold {view['threshold']} · run `{view['run_id']}`"
    )

    for line in view["failed_nodes"]:
        st.warning(line)
    if view["summary"]:
        st.markdown(view["summary"])

    ranked = view["ranked"]
    if ranked and ranked["rows"]:
        st.markdown(f"**{ranked['title']}**")
        st.dataframe(pd.DataFrame(ranked["rows"]), hide_index=True, use_container_width=True)

    for name, counts in view["counts"].items():
        st.markdown(f"**{name}**")
        st.bar_chart(pd.Series(counts, name="count"), horizontal=True)

    with st.expander("Per-step report"):
        st.dataframe(pd.DataFrame(view["node_reports"]), hide_index=True)
    shown = len(view["table"])
    with st.expander(f"Results table ({shown} of {view['table_total']} items)"):
        st.dataframe(pd.DataFrame(view["table"]), hide_index=True)
    st.download_button(
        "Download results.json",
        view["results_json"],
        file_name=f"{view['run_id']}_results.json",
        mime="application/json",
        key=f"dl-{view['run_id']}",
    )


def render_message(msg: dict[str, Any]) -> None:
    """Draw a stored chat message (user text, assistant run, or assistant error)."""
    if "content" in msg:
        st.markdown(msg["content"])
    if msg.get("source"):
        st.caption(msg["source"])
    if msg.get("log"):
        with st.expander("Run log"):
            st.text("\n".join(msg["log"]))
    if msg.get("error"):
        st.error(msg["error"])
    if msg.get("view"):
        render_view(msg["view"])


# ------------------------------------------------------------------------------ running


def _event_line(ev: Event) -> str | None:
    """One log line for a pipeline event, or None for per-item progress ticks."""
    kind = ev["type"]
    if kind == "loaded":
        cut = f" (first {ev['items']} of {ev['total']})" if ev["total"] > ev["items"] else ""
        return f"Loaded {ev['items']} items{cut}"
    if kind == "plan":
        steps = ", ".join(f"{n['id']} {n['tool']}" for n in ev["plan"]["nodes"])
        return f"Plan: {steps}"
    tag = f"{ev['node']} {ev['tool']}"
    status = ev["status"]
    if status == "running":
        return None if "done" in ev else f"▶ {tag}: {ev.get('why', '')}"
    if status == "done":
        r = ev["report"]
        return (
            f"✓ {tag}: {r['items_in']} → {r['items_out']} items · jev {r['jev_decisions']} · "
            f"llm {r['llm_decisions']} · code {r['code_ops']} · {r['duration_ms'] / 1000:.1f}s"
        )
    return f"✗ {tag} {status}: {ev.get('error')}"


def _start_run(req: JobRequest) -> tuple[queue.Queue[Event], dict[str, Any], threading.Thread]:
    """Run the job in a worker thread with its own event loop.

    Each run builds and closes its own clients, so a fresh loop per run is safe.
    """
    events: queue.Queue[Event] = queue.Queue()
    outcome: dict[str, Any] = {}

    def work() -> None:
        try:
            outcome["result"] = asyncio.run(run_job(req, events.put))
        except BaseException as e:  # noqa: BLE001 - handed back to the script thread
            outcome["error"] = e

    thread = threading.Thread(target=work, name=f"chat-run-{req.mode}", daemon=True)
    thread.start()
    return events, outcome, thread


def _run_with_progress(req: JobRequest) -> dict[str, Any]:
    """Run one job while drawing its progress; returns the message to store."""
    lines: list[str] = []
    with st.status("Starting…", expanded=True) as status:
        bar = st.empty()
        events, outcome, thread = _start_run(req)
        started = time.monotonic()

        def drain() -> None:
            while True:
                try:
                    ev = events.get_nowait()
                except queue.Empty:
                    return
                line = _event_line(ev)
                if line is not None:
                    lines.append(line)
                    st.write(line)
                if ev["type"] == "plan":
                    status.update(label="Running the plan…")
                elif ev["type"] == "node" and ev["status"] == "running" and "done" in ev:
                    n = max(ev["items"], 1)
                    bar.progress(min(ev["done"] / n, 1.0), text=f"{ev['node']} {ev['tool']}")
                elif ev["type"] == "loaded":
                    status.update(label="Planning…")

        while thread.is_alive():
            drain()
            if time.monotonic() - started > RUN_TIMEOUT_S:
                status.update(label="Timed out", state="error")
                return {"role": "assistant", "error": "The run took too long.", "log": lines}
            time.sleep(POLL_S)
        drain()
        bar.empty()

        error = outcome.get("error")
        if error is not None:
            status.update(label="Run failed", state="error", expanded=False)
            if isinstance(error, ChatRunError):
                message = str(error)
            else:
                log.error("chat run failed", exc_info=error)
                message = f"Run failed: {type(error).__name__}: {error}"
            return {"role": "assistant", "error": message, "log": lines}
        results, plan = outcome["result"]
        status.update(
            label=f"Done in {time.monotonic() - started:.1f}s", state="complete", expanded=False
        )
    items_in = len(results.items) + len(results.dropped)
    return {"role": "assistant", "view": build_view(results, plan, items_in), "log": lines}


def _replay_message() -> dict[str, Any]:
    try:
        results, plan, items_in = load_replay()
    except ChatRunError as e:
        return {"role": "assistant", "error": str(e)}
    return {
        "role": "assistant",
        "source": (
            f"Recorded live run from {results.finished_at}. Your message was not sent to a "
            f"model; this is the saved output for the job below."
        ),
        "view": build_view(results, plan, items_in),
    }


def run_chat_interface(options: ChatOptions) -> None:
    """Render the chat for one script run; a new message runs one job with `options`."""
    history: list[dict[str, Any]] = st.session_state.setdefault("messages", [])
    for msg in history:
        with st.chat_message(msg["role"]):
            render_message(msg)

    prompt = st.chat_input("Describe the job, e.g. “" + DEFAULT_JOB + "”")
    if not prompt:
        return
    history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        if options.mode == "replay":
            msg = _replay_message()
        elif options.mode == "live" and (missing := live_keys_missing()):
            msg = {
                "role": "assistant",
                "error": f"Live mode needs {', '.join(missing)} in `.env`. "
                "Pick the recorded run or offline stand-ins in the sidebar.",
            }
        elif options.items_path is None:
            msg = {"role": "assistant", "error": "Pick a dataset or upload a file first."}
        else:
            source = MODE_LABELS[options.mode]
            if options.mode == "offline":
                source += ". The saved review-backlog plan is used, since no model can plan."
            st.caption(source)
            req = JobRequest(
                job=prompt,
                items_path=options.items_path,
                mode=options.mode,
                plan_source=options.plan_source,
                threshold=options.threshold,
                max_items=options.max_items,
            )
            msg = {**_run_with_progress(req), "source": source}
        if "source" in msg and options.mode == "replay":
            st.caption(msg["source"])
        if msg.get("error"):
            st.error(msg["error"])
        if msg.get("view"):
            render_view(msg["view"])
    history.append(msg)


# ------------------------------------------------------------------------------ sidebar


def _uploaded_path(upload: Any) -> Path:
    """Save an upload once per session; the reader picks the format from the suffix."""
    saved: dict[str, str] = st.session_state.setdefault("uploads", {})
    key = upload.file_id
    if key not in saved:
        folder = Path(tempfile.mkdtemp(prefix="foreman-chat-"))
        path = folder / Path(upload.name).name
        path.write_bytes(upload.getvalue())
        saved[key] = str(path)
    return Path(saved[key])


def sidebar() -> ChatOptions:
    """Source, data, plan and threshold for the next job."""
    with st.sidebar:
        st.header("Foreman")
        keys_missing = live_keys_missing()
        modes: list[Mode] = ["live", "replay", "offline"]
        mode: Mode = st.radio(
            "Run with",
            modes,
            index=1 if keys_missing else 0,
            format_func=MODE_LABELS.__getitem__,
        )
        if mode == "live" and keys_missing:
            st.warning(f"Missing in `.env`: {', '.join(keys_missing)}")
        options = ChatOptions(mode=mode)
        if mode != "replay":
            dataset = st.selectbox("Data", [*DATASETS, "Upload a file"])
            if dataset == "Upload a file":
                upload = st.file_uploader("items.json, .csv or .txt", type=["json", "csv", "txt"])
                options.items_path = _uploaded_path(upload) if upload else None
            else:
                options.items_path = DATASETS[dataset]
            options.max_items = int(
                st.number_input("Max items", min_value=1, max_value=2000, value=200, step=50)
            )
            options.threshold = st.slider("Confidence threshold", 0.0, 1.0, 0.7, 0.05)
            if mode == "live":
                options.plan_source = st.radio(
                    "Plan",
                    ["planner", "saved"],
                    format_func={
                        "planner": "Let the planner write it",
                        "saved": "Saved review-backlog plan",
                    }.__getitem__,
                )
        if st.button("Clear chat", use_container_width=True):
            st.session_state["messages"] = []
            st.rerun()
    return options


def main() -> None:
    """Entry point for `streamlit run foreman/chat/app.py`."""
    st.set_page_config(page_title="Foreman chat", page_icon="🧰", layout="wide")
    st.title("Foreman")
    st.caption("Describe a bulk job. Foreman plans it once, then Jev decides item by item.")
    run_chat_interface(sidebar())


if __name__ == "__main__":
    main()
