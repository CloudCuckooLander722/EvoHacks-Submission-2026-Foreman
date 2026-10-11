"""Everything the chat UI needs from the pipeline, with no Streamlit in it.

One chat message is one job. `run_job` loads the items, gets a plan (the LLM planner, or
the saved hero plan), runs the executor and returns the results. `build_view` turns any
results.json, live or recorded, into the plain dict the chat renders, so a live run, an
offline run and the recorded replay all look the same in the window. Keeping this apart
from the UI is what lets the tests check the pipeline output without a browser.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from foreman.api.extract import ExtractFailed, to_items
from foreman.config import Settings, get_settings
from foreman.contracts import ResultsFile
from foreman.core.context import make_context, new_run_id
from foreman.core.state import RunState
from foreman.graph.executor import execute
from foreman.graph.plan import Plan, PlanError, sample_fields, validate_plan
from foreman.graph.planner import hero_plan, make_plan

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
REPLAY_DIR = REPO_ROOT / "web" / "public" / "replays" / "reviews-1k"

Mode = Literal["live", "offline", "replay"]
PlanSource = Literal["planner", "saved"]
Event = dict[str, Any]

# Bundled inputs a visitor can pick without uploading anything.
DATASETS: dict[str, Path] = {
    "25 sample app reviews": REPO_ROOT / "data" / "samples" / "reviews_small.json",
    "1,000 Medium app reviews": REPO_ROOT / "data" / "hero" / "reviews_1k.json",
}

TOP_K = 10
TABLE_ROWS = 200


class ChatRunError(Exception):
    """A run could not finish; the message is shown to the user as is."""


@dataclass
class JobRequest:
    """One chat message turned into a run."""

    job: str
    items_path: Path
    mode: Mode
    plan_source: PlanSource = "planner"
    threshold: float = 0.7
    max_items: int = 200


def live_keys_missing(settings: Settings | None = None) -> list[str]:
    """Names of the keys a live run needs but `.env` does not have."""
    s = settings or get_settings()
    missing = []
    if s.ai_gateway_api_key is None:
        missing.append("AI_GATEWAY_API_KEY")
    if s.fireworks_api_key is None:
        missing.append("FIREWORKS_API_KEY")
    return missing


def load_run(path: Path, job: str, max_items: int) -> tuple[RunState, int]:
    """Items from items.json / csv / txt (the API's reader), capped at `max_items`.

    Returns the run and how many items the file held before the cap, so the chat can say
    when it cut the file short.
    """
    try:
        items = to_items(path).items
    except (ExtractFailed, OSError) as e:
        raise ChatRunError(f"Could not read the file: {e}") from e
    ids = [i.id for i in items]
    if len(set(ids)) != len(ids):
        raise ChatRunError("Could not read the file: item ids are not unique")
    return RunState(run_id=new_run_id(), job=job, items=items[:max_items]), len(items)


def saved_plan(job: str, run: RunState) -> Plan:
    """The hand-tuned hero plan, checked against these items' fields."""
    plan = hero_plan()
    plan.job = job or plan.job
    problems = validate_plan(plan, sample_fields(run.items))
    if problems:
        raise ChatRunError(
            "The saved review-backlog plan does not fit this file: " + "; ".join(problems)
        )
    return plan


async def run_job(req: JobRequest, on_event: Callable[[Event], None]) -> tuple[ResultsFile, Plan]:
    """Plan and execute one job, reporting progress through `on_event`.

    Events: `{"type": "loaded", items, total}`, `{"type": "plan", "plan": {...}}`, then
    the executor's node events with `"type": "node"`. Offline mode has no model that can
    write a plan, so it always uses the saved plan.
    """
    if req.mode == "replay":
        raise ValueError("replays are loaded with load_replay(), not run")
    run, total = load_run(req.items_path, req.job, req.max_items)
    on_event({"type": "loaded", "items": len(run.items), "total": total})
    ctx = await make_context(run_id=run.run_id, threshold=req.threshold, fake=req.mode == "offline")
    try:
        if req.plan_source == "saved" or req.mode == "offline":
            plan = saved_plan(req.job, run)
        else:
            try:
                plan = await make_plan(req.job, run, ctx)
            except PlanError as e:
                detail = "; ".join(e.problems)
                raise ChatRunError(
                    f"Planning failed: {e}" + (f" ({detail})" if detail else "")
                ) from e
        on_event({"type": "plan", "plan": plan.model_dump(mode="json")})
        results = await execute(plan, run, ctx, on_node=lambda ev: on_event({"type": "node", **ev}))
    finally:
        await ctx.aclose()
    return results, plan


def load_replay(replay_dir: Path = REPLAY_DIR) -> tuple[ResultsFile, Plan, int]:
    """The recorded live run: its results, its plan and how many items went in."""
    path = replay_dir / "results.json"
    try:
        results = ResultsFile.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ChatRunError(f"The recorded run is missing: {e}") from e
    plan = Plan.model_validate(results.plan)
    return results, plan, len(results.items) + len(results.dropped)


# ------------------------------------------------------------------------------ the view


def _output_fields(results: ResultsFile) -> list[str]:
    """Field names the plan's nodes wrote, in plan order (no `__` bookkeeping fields)."""
    names: list[str] = []
    for item in results.items:
        for k in item.fields:
            if "__" not in k and k not in names:
                names.append(k)
    return names


def _ranked(results: ResultsFile, plan: Plan) -> dict[str, Any] | None:
    """Top items of the plan's last rank node, in rank order."""
    ranks = [n for n in plan.nodes if n.tool == "rank"]
    if not ranks:
        return None
    out = ranks[-1].params.get("output_field", "rank")
    score_field = ranks[-1].params.get("score_field")
    order = results.aggregates.get(f"{out}_order") or []
    by_id = {i.id: i for i in results.items}
    rows = []
    for item_id in order[:TOP_K]:
        item = by_id.get(item_id)
        if item is None:
            continue
        row = {"rank": item.fields.get(out), "id": item.id}
        if score_field:
            row[score_field] = item.fields.get(score_field)
        row["text"] = item.text
        rows.append(row)
    return {"title": f"Top {len(rows)} by {score_field or out}", "rows": rows}


def _counts(results: ResultsFile) -> dict[str, dict[str, int]]:
    """Every `dict[str, int]` aggregate, e.g. type_counts and theme_counts, chartable as is."""
    out = {}
    for key, v in results.aggregates.items():
        if (
            isinstance(v, dict)
            and v
            and all(isinstance(x, int) and not isinstance(x, bool) for x in v.values())
        ):
            out[key] = v
    return out


def _cell(v: Any) -> Any:
    """Table cells must be scalars; lists and dicts are shown as compact JSON."""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v


def build_view(results: ResultsFile, plan: Plan, items_in: int) -> dict[str, Any]:
    """The JSON-safe summary of a run that one assistant message shows.

    Every number here is copied or counted from results.json; nothing is estimated.
    """
    st = results.stats
    jev, llm, code = (st.by.get(k, 0) for k in ("jev", "llm", "code"))
    model = jev + llm
    fields = _output_fields(results)
    table = [
        {"id": i.id, "text": i.text, **{f: _cell(i.fields.get(f)) for f in fields}}
        for i in results.items[:TABLE_ROWS]
    ]
    return {
        "run_id": results.run_id,
        "job": results.job,
        "threshold": results.threshold,
        "plan": [{"node": n.id, "tool": n.tool, "why": n.why} for n in plan.nodes],
        "items_in": items_in,
        "items_out": len(results.items),
        "dropped": len(results.dropped),
        "decisions": {"total": st.decisions_total, "jev": jev, "llm": llm, "code": code},
        "jev_share_of_model": jev / model if model else 0.0,
        "low_confidence": st.low_confidence,
        "cost_usd": st.jev_cost_usd + st.llm_cost_usd,
        "wall_time_s": st.wall_time_s,
        "node_reports": [
            {
                "node": r.node,
                "tool": r.tool,
                "items": f"{r.items_in} → {r.items_out}",
                "jev": r.jev_decisions,
                "llm": r.llm_decisions,
                "code": r.code_ops,
                "low conf.": r.low_confidence,
                "seconds": round(r.duration_ms / 1000, 1),
            }
            for r in results.node_reports
        ],
        "failed_nodes": [
            f"{f['node']} ({f['tool']}) {f['status']}: {f['error']}"
            for f in results.aggregates.get("__failed_nodes", [])
        ],
        "summary": results.aggregates.get("summary"),
        "counts": _counts(results),
        "ranked": _ranked(results, plan),
        "table": table,
        "table_total": len(results.items),
        "results_json": results.model_dump_json(indent=2),
    }
