"""Executor: run a plan's nodes in order over one RunState and write results.json.

A failing node does not end the run: on stage a partial result beats no result. The
executor records the failure, skips only the later nodes that read a field the failed
node would have written (the same field tracking `validate_plan` uses), and runs the rest.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from foreman.config import get_settings
from foreman.contracts import NodeReport, ResultsFile, build_results, save_results
from foreman.core.state import RunState
from foreman.graph.plan import (
    FieldScope,
    Plan,
    dump_params,
    format_validation_error,
    missing_fields,
    sample_fields,
)
from foreman.tools import get_tool

if TYPE_CHECKING:
    from foreman.core.context import RunContext

log = logging.getLogger(__name__)

NodeEvent = dict[str, Any]


def _emit(on_node: Callable[[NodeEvent], None] | None, event: NodeEvent) -> None:
    """Send a node event; a broken listener (UI, SSE) must never break the run."""
    if on_node is None:
        return
    try:
        on_node(event)
    except Exception:
        log.exception("on_node listener failed")


def run_dir(run_id: str) -> Path:
    """`runs/<run_id>/`, where results.json, plan.json and decisions.jsonl live."""
    return Path(get_settings().runs_dir) / run_id


async def execute(
    plan: Plan,
    run: RunState,
    ctx: RunContext,
    on_node: Callable[[NodeEvent], None] | None = None,
) -> ResultsFile:
    """Run every node in order, then build and save `runs/<run_id>/results.json`.

    `on_node` gets `{"node", "tool", "index", "total", "status", ...}` events: "running"
    (with "why"; repeated with "done"/"items" as items finish), then "done" (with "report",
    a NodeReport dict), "failed" (with "error") or "skipped" (with "error" naming the
    missing fields).
    """
    started = datetime.now(UTC)
    run.job = plan.job or run.job
    scope = FieldScope(sample_fields(run.items))
    reports: list[NodeReport] = []
    failed: list[dict[str, Any]] = []
    total = len(plan.nodes)
    outer_progress = ctx.progress

    for index, node in enumerate(plan.nodes):
        base = {"node": node.id, "tool": node.tool, "index": index, "total": total}

        def fail(status: str, error: str, base: dict[str, Any] = base) -> None:
            failed.append({**base, "status": status, "error": error})
            log.warning("node %s (%s) %s: %s", base["node"], base["tool"], status, error)
            _emit(on_node, {**base, "status": status, "error": error})

        try:
            tool = get_tool(node.tool)
            params = tool.Params.model_validate(node.params)
        except KeyError as e:
            fail("failed", str(e.args[0]))
            continue
        except ValidationError as e:
            fail("failed", "invalid params: " + "; ".join(format_validation_error(e)))
            continue

        dumped = dump_params(params)
        missing = missing_fields(node.tool, dumped, scope)
        if missing:
            names = ", ".join(sorted({f for _, f in missing}))
            fail("skipped", f"needs field(s) that do not exist: {names}")
            continue

        _emit(on_node, {**base, "status": "running", "why": node.why})

        def progress(tool_name: str, done: int, n: int, base: dict[str, Any] = base) -> None:
            _emit(on_node, {**base, "status": "running", "done": done, "items": n})
            if outer_progress is not None:
                outer_progress(tool_name, done, n)

        ctx.progress = progress
        try:
            report = await tool.run(run, params, ctx)
        except Exception as e:  # any tool failure becomes a partial result
            log.exception("node %s (%s) failed", node.id, node.tool)
            fail("failed", f"{type(e).__name__}: {e}")
            continue
        finally:
            ctx.progress = outer_progress

        scope.add_node(node.tool, dumped)
        node_report = NodeReport(node=node.id, **report.model_dump())
        reports.append(node_report)
        _emit(on_node, {**base, "status": "done", "report": node_report.model_dump()})

    if failed:
        run.aggregates["__failed_nodes"] = failed
    run.aggregates["escalation_curve"] = [list(p) for p in ctx.ledger.escalation_curve()]

    results = build_results(
        run,
        ctx.ledger,
        plan=plan.model_dump(mode="json"),
        threshold=ctx.threshold,
        started_at=started,
        finished_at=datetime.now(UTC),
        node_reports=reports,
        runs_dir=get_settings().runs_dir,
    )
    out = run_dir(run.run_id)
    save_results(results, out / "results.json")
    (out / "plan.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    return results
