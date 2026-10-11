"""Background runs for the API: extract -> plan -> execute -> summary, as an event stream.

Each run owns an append-only event log. Every event is serialized once and then sent the
same way three times: appended to the in-memory log (so a client that connects late gets
a replay first), pushed to each live SSE subscriber's queue, and written to
`runs/<id>/events.jsonl` with a `t_ms` offset (the replay bundle the static site plays).

The engine is noisy (one ledger callback per decision, one progress callback per item),
so a pacer turns it into a browser-friendly stream: at most one `decision` every 100 ms
(the latest row, a sample for the ticker), `stats` every 250 ms, and node progress at most
every 100 ms per node. Totals always come from `stats`, never from counting decisions.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Literal

from foreman.api.extract import ExtractFailed, load_optional, to_items
from foreman.config import get_settings
from foreman.contracts import ResultsFile, save_results
from foreman.core.context import RunContext
from foreman.core.ledger import Decision
from foreman.core.state import RunState
from foreman.graph.executor import execute, run_dir
from foreman.graph.plan import Plan, PlanError, sample_fields, validate_plan
from foreman.graph.planner import make_plan

log = logging.getLogger(__name__)

EventName = Literal["plan", "node", "decision", "stats", "done", "error"]
ContextFactory = Callable[..., Awaitable[RunContext]]
Status = Literal["queued", "planning", "running", "done", "failed"]

DECISION_EVERY_S = 0.1
STATS_EVERY_S = 0.25
PROGRESS_EVERY_S = 0.1
_END = None  # queue sentinel: the run has finished, close the stream


def compact_decision(d: Decision, node: str | None) -> dict[str, Any]:
    """A ledger row as the `decision` event (MINH_PLAN §D): no probabilities, plus `node`."""
    return {
        "item_id": d.item_id,
        "node": node,
        "tool": d.tool,
        "question_id": d.question_id,
        "qtype": d.qtype,
        "value": d.value,
        "confidence": d.confidence,
        "decided_by": d.decided_by,
        "jev_value": d.jev_value,
        "jev_confidence": d.jev_confidence,
        "low_confidence": d.low_confidence,
        "cost_usd": d.cost_usd,
        "ts": d.ts,
    }


@dataclass
class RunRecord:
    """One API run: its inputs, status, event log and live subscribers."""

    run_id: str
    job: str
    threshold: float | None
    plan_name: str | None
    input_path: Path
    max_items: int
    status: Status = "queued"
    error: str | None = None
    ctx: RunContext | None = None
    task: asyncio.Task[None] | None = None
    started: float = field(default_factory=time.monotonic)
    log: list[tuple[str, str]] = field(default_factory=list)
    finished: bool = False
    _subscribers: set[asyncio.Queue[tuple[str, str] | None]] = field(default_factory=set)
    _events_file: IO[str] | None = None
    _node: str | None = None
    _latest: tuple[Decision, str | None] | None = None
    _progress_at: dict[str, float] = field(default_factory=dict)

    @property
    def dir(self) -> Path:
        return run_dir(self.run_id)

    # ------------------------------------------------------------------ events

    def publish(self, event: EventName, data: dict[str, Any]) -> None:
        """Log, record and fan out one event. Never raises into the engine."""
        payload = json.dumps(data, default=str, ensure_ascii=False)
        self.log.append((event, payload))
        t_ms = int((time.monotonic() - self.started) * 1000)
        try:
            if self._events_file is None:
                self.dir.mkdir(parents=True, exist_ok=True)
                self._events_file = (self.dir / "events.jsonl").open("a", encoding="utf-8")
            self._events_file.write(f'{{"t_ms": {t_ms}, "event": "{event}", "data": {payload}}}\n')
            self._events_file.flush()
        except OSError:
            log.exception("run %s: could not record events.jsonl", self.run_id)
        for q in self._subscribers:
            q.put_nowait((event, payload))

    def error_event(self, message: str, *, node: str | None = None, fatal: bool) -> None:
        self.publish("error", {"message": message, "node": node, "fatal": fatal})

    def close(self) -> None:
        """Mark finished, end every live stream and close the recording."""
        self.finished = True
        for q in self._subscribers:
            q.put_nowait(_END)
        if self._events_file is not None:
            self._events_file.close()
            self._events_file = None

    async def stream(self) -> AsyncIterator[tuple[str, str]]:
        """Everything published so far, then live events until the run ends."""
        q: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()
        backlog, finished = list(self.log), self.finished
        if not finished:
            self._subscribers.add(q)  # same tick as the snapshot: nothing slips between
        try:
            for ev in backlog:
                yield ev
            if finished:
                return
            while (ev := await q.get()) is not _END:
                yield ev
        finally:
            self._subscribers.discard(q)

    # ------------------------------------------------------------------ engine hooks

    def on_decision(self, d: Decision) -> None:
        """Ledger listener: keep only the newest row; the pacer samples it."""
        self._latest = (d, self._node)

    def on_node(self, ev: dict[str, Any]) -> None:
        """Executor listener: pass node events through, throttling per-item progress."""
        node, status = ev["node"], ev["status"]
        if status == "running" and "done" in ev:
            now = time.monotonic()
            last = ev["done"] >= ev.get("items", 0)
            if not last and now - self._progress_at.get(node, 0.0) < PROGRESS_EVERY_S:
                return
            self._progress_at[node] = now
        elif status == "running":
            self._node = node
        self.publish("node", ev)
        if status in ("failed", "skipped"):
            msg = f"{node} ({ev['tool']}) {status}: {ev.get('error', '')}"
            self.error_event(msg, node=node, fatal=False)

    def flush_decision(self) -> None:
        if self._latest is not None:
            d, node = self._latest
            self._latest = None
            self.publish("decision", compact_decision(d, node))

    async def pace(self, ctx: RunContext) -> None:
        """Sampled decisions every 100 ms and the meter every 250 ms, until cancelled."""
        next_stats = 0.0
        while True:
            await asyncio.sleep(DECISION_EVERY_S)
            self.flush_decision()
            now = time.monotonic()
            if now >= next_stats:
                self.publish("stats", ctx.ledger.stats().model_dump())
                next_stats = now + STATS_EVERY_S


class RunRegistry:
    """In-memory runs for this server process (results stay on disk after a restart)."""

    def __init__(self) -> None:
        self.records: dict[str, RunRecord] = {}

    def get(self, run_id: str) -> RunRecord | None:
        return self.records.get(run_id)

    def add(self, rec: RunRecord) -> None:
        self.records[rec.run_id] = rec

    def active(self) -> int:
        return sum(1 for r in self.records.values() if not r.finished)

    async def shutdown(self) -> None:
        tasks = [r.task for r in self.records.values() if r.task and not r.task.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------------- the run


def load_saved_plan(plans_dir: Path, name: str) -> Plan:
    return Plan.model_validate_json((plans_dir / f"{name}.json").read_text(encoding="utf-8"))


async def _resolve_plan(rec: RunRecord, run: RunState, ctx: RunContext, plans_dir: Path) -> Plan:
    """The saved plan named in the request (job text overrides its job), else the planner."""
    if rec.plan_name is None:
        return await make_plan(rec.job, run, ctx)
    plan = load_saved_plan(plans_dir, rec.plan_name)
    if rec.job:
        plan.job = rec.job
    problems = validate_plan(plan, sample_fields(run.items))
    if problems:
        raise PlanError(f"plan {rec.plan_name!r} does not fit these items", problems)
    return plan


async def _add_summary(results: ResultsFile, ctx: RunContext) -> None:
    """Minh's `summarize()` into aggregates["summary"], when it exists. Failures are logged."""
    summarize = load_optional("foreman.format", "summarize")
    if summarize is None:
        return
    try:
        text = summarize(results, ctx)
        if inspect.isawaitable(text):
            text = await text
    except Exception:
        log.exception("summarize failed; results.json is saved without a summary")
        return
    results.aggregates["summary"] = text
    results.stats = ctx.ledger.stats()  # the summary's LLM call is part of the run's cost
    ctx.ledger.dump_jsonl(results.decisions_file)


def _load_run(rec: RunRecord) -> RunState:
    items = to_items(rec.input_path)
    ids = [i.id for i in items.items]
    if len(set(ids)) != len(ids):
        raise ExtractFailed("item ids are not unique")
    return RunState(run_id=rec.run_id, job=rec.job, items=items.items)


async def run_job(rec: RunRecord, make_ctx: ContextFactory, plans_dir: Path) -> None:
    """The background task behind POST /runs. Every failure ends as an `error` event."""
    pacer: asyncio.Task[None] | None = None
    try:
        rec.status = "planning"
        try:
            run = await asyncio.to_thread(_load_run, rec)
        except (ExtractFailed, OSError) as e:
            raise ExtractFailed(f"Could not read the file: {e}") from e
        n_uploaded = len(run.items)
        run.items = run.items[: rec.max_items]
        rec.ctx = ctx = await make_ctx(run_id=rec.run_id, threshold=rec.threshold)
        ctx.ledger.on_event(rec.on_decision)

        plan = await _resolve_plan(rec, run, ctx, plans_dir)
        run.job = plan.job
        rec.publish(
            "plan",
            {
                "run_id": rec.run_id,
                "job": plan.job,
                "items_total": len(run.items),
                "nodes": plan.model_dump(mode="json")["nodes"],
            },
        )
        if n_uploaded > len(run.items):
            rec.error_event(
                f"Only the first {len(run.items)} of {n_uploaded} items are processed "
                "(MAX_ITEMS on this server).",
                fatal=False,
            )
        rec.status = "running"
        pacer = asyncio.create_task(rec.pace(ctx))
        results = await execute(plan, run, ctx, on_node=rec.on_node)
        await _add_summary(results, ctx)
        save_results(results, rec.dir / "results.json")

        pacer.cancel()
        await asyncio.gather(pacer, return_exceptions=True)
        rec.flush_decision()
        rec.publish("stats", results.stats.model_dump())  # ends exactly on results.json
        rec.status = "done"
        rec.publish("done", {"run_id": rec.run_id, "results_url": f"/runs/{rec.run_id}/results"})
    except asyncio.CancelledError:
        rec.status, rec.error = "failed", "the server shut down during the run"
        rec.error_event(rec.error, fatal=True)
        raise
    except PlanError as e:
        detail = "; ".join(e.problems)
        rec.status, rec.error = (
            "failed",
            f"Planning failed: {e}" + (f" ({detail})" if detail else ""),
        )
        rec.error_event(rec.error, fatal=True)
    except ExtractFailed as e:
        rec.status, rec.error = "failed", str(e)
        rec.error_event(rec.error, fatal=True)
    except Exception as e:
        log.exception("run %s failed", rec.run_id)
        rec.status, rec.error = "failed", f"{type(e).__name__}: {e}"
        rec.error_event(rec.error, fatal=True)
    finally:
        if pacer is not None and not pacer.done():
            pacer.cancel()
        if rec.ctx is not None:
            try:
                await rec.ctx.aclose()
            except Exception:
                log.exception("run %s: closing clients failed", rec.run_id)
        rec.close()
        log.info("run %s %s", rec.run_id, rec.status)


def recorded_events(run_id: str) -> list[tuple[str, str]] | None:
    """A finished run's events from `events.jsonl` (for runs from before a restart)."""
    path = Path(get_settings().runs_dir) / run_id / "events.jsonl"
    if not path.exists():
        return None
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out.append((row["event"], json.dumps(row["data"], ensure_ascii=False)))
    return out
