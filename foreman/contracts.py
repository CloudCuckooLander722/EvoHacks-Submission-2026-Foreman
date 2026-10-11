"""items.json / results.json models (shared with Minh).

These two files are the only interface between extraction/formatting and the engine.
The shapes follow docs/CONTRACTS.md exactly; change them only with both owners.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from foreman.core.context import new_run_id
from foreman.core.ledger import Ledger, RunStats
from foreman.core.state import ItemState, RunState


class ItemsFile(BaseModel):
    """items.json: extraction output (Minh -> Nathan)."""

    source: dict[str, Any] = Field(default_factory=dict)
    items: list[ItemState]


class DroppedItem(BaseModel):
    """An item a tool removed (e.g. Filter), with the reason for the report. No `meta`."""

    id: str
    text: str
    fields: dict[str, Any] = Field(default_factory=dict)
    reason: str


class NodeReport(BaseModel):
    """A ToolReport tagged with its plan node id."""

    node: str
    tool: str
    items_in: int
    items_out: int
    jev_decisions: int = 0
    llm_decisions: int = 0
    code_ops: int = 0
    low_confidence: int = 0
    duration_ms: int = 0
    notes: list[str] = Field(default_factory=list)


class ResultsFile(BaseModel):
    """results.json: executor output (Nathan -> Minh)."""

    run_id: str
    job: str
    threshold: float
    started_at: str
    finished_at: str
    plan: dict[str, Any]
    items: list[ItemState]
    dropped: list[DroppedItem] = Field(default_factory=list)
    aggregates: dict[str, Any] = Field(default_factory=dict)
    node_reports: list[NodeReport] = Field(default_factory=list)
    stats: RunStats
    decisions_file: str


def iso_utc(t: datetime | str) -> str:
    """`2026-10-11T08:01:12Z`; strings are passed through unchanged."""
    if isinstance(t, str):
        return t
    if t.tzinfo is None:
        t = t.replace(tzinfo=UTC)
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_items(path: str | Path, *, run_id: str | None = None, job: str = "") -> RunState:
    """Read items.json into a fresh RunState."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items_file = ItemsFile.model_validate(data)
    ids = [i.id for i in items_file.items]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: item ids are not unique")
    return RunState(run_id=run_id or new_run_id(), job=job, items=items_file.items)


def build_results(
    run: RunState,
    ledger: Ledger,
    *,
    plan: dict[str, Any],
    threshold: float,
    started_at: datetime | str,
    finished_at: datetime | str,
    node_reports: list[NodeReport | dict[str, Any]],
    runs_dir: str | Path,
) -> ResultsFile:
    """Assemble results.json and write the decision log to `runs/<run_id>/decisions.jsonl`."""
    decisions_path = Path(runs_dir) / run.run_id / "decisions.jsonl"
    ledger.dump_jsonl(decisions_path)
    dropped = [
        DroppedItem(
            id=i.id,
            text=i.text,
            fields=i.fields,
            reason=str(i.meta.get("drop_reason") or "dropped"),
        )
        for i in run.dropped
    ]
    return ResultsFile(
        run_id=run.run_id,
        job=run.job,
        threshold=threshold,
        started_at=iso_utc(started_at),
        finished_at=iso_utc(finished_at),
        plan=plan,
        items=run.items,
        dropped=dropped,
        aggregates=run.aggregates,
        node_reports=[NodeReport.model_validate(r) for r in node_reports],
        stats=ledger.stats(),
        decisions_file=decisions_path.as_posix(),
    )


def save_results(results: ResultsFile, path: str | Path) -> Path:
    """Write results.json (pretty-printed, UTF-8)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(results.model_dump_json(indent=2), encoding="utf-8")
    return p
