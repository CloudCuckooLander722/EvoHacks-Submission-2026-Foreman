"""Decision records, run stats, event hook for the live meter.

Every model decision is written here with who made it and how sure it was. That record
is the product's audit trail ("why is this cell `bug`?"), the source of the live meter,
and what lets the confidence bar show the escalation curve without re-running anything.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

Provider = Literal["jev", "llm"]


class Decision(BaseModel):
    """One answer to one question about one item (or one proposal/computation)."""

    run_id: str
    item_id: str
    tool: str
    question_id: str
    qtype: str
    value: Any
    confidence: float | None
    probabilities: dict[str, float] | None
    decided_by: Literal["jev", "llm", "code"]
    kind: Literal["decision", "proposal", "compute"] = "decision"
    jev_value: Any = None
    jev_confidence: float | None = None
    low_confidence: bool = False
    latency_ms: int | None = None
    cost_usd: float = 0.0
    ts: float = Field(default_factory=time.time)


class RunStats(BaseModel):
    """Run-wide totals for the meter and results.json."""

    decisions_total: int = 0
    by: dict[str, int] = Field(default_factory=lambda: {"jev": 0, "llm": 0, "code": 0})
    low_confidence: int = 0
    jev_cost_usd: float = 0.0
    llm_cost_usd: float = 0.0
    wall_time_s: float = 0.0
    jev_calls: int = 0
    llm_calls: int = 0


def jev_confidence_of(d: Decision) -> float | None:
    """Jev's own confidence for a decision, if Jev was asked at all."""
    if d.decided_by == "jev":
        return d.confidence
    if d.decided_by == "llm" and d.jev_confidence is not None:
        return d.jev_confidence
    return None


class Ledger:
    """Append-only decision log for one run, with listeners for live updates.

    Call counts and costs are recorded per provider call (`record_call`) rather than
    derived from decisions, because one Jev call covers several decisions and a failed
    LLM fallback costs money without producing a decision of its own.
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.decisions: list[Decision] = []
        self._callbacks: list[Callable[[Decision], Any]] = []
        self._started = time.monotonic()
        self._calls: dict[str, int] = {"jev": 0, "llm": 0}
        self._cost: dict[str, float] = {"jev": 0.0, "llm": 0.0}

    def on_event(self, callback: Callable[[Decision], Any]) -> None:
        """Register a listener called with every new Decision (live meter, SSE)."""
        self._callbacks.append(callback)

    def add(self, d: Decision) -> None:
        """Record one decision and notify listeners. A failing listener never breaks a run."""
        self.decisions.append(d)
        for cb in self._callbacks:
            try:
                cb(d)
            except Exception:
                log.exception("ledger listener failed")

    def extend(self, ds: Iterable[Decision]) -> None:
        """Record several decisions in order."""
        for d in ds:
            self.add(d)

    def record_call(self, provider: Provider, cost_usd: float = 0.0, n: int = 1) -> None:
        """Count `n` provider calls and their total cost toward the run stats."""
        self._calls[provider] += n
        self._cost[provider] += cost_usd

    def stats(self) -> RunStats:
        """Totals so far, with wall time measured from ledger creation."""
        st = RunStats(
            jev_cost_usd=self._cost["jev"],
            llm_cost_usd=self._cost["llm"],
            jev_calls=self._calls["jev"],
            llm_calls=self._calls["llm"],
            wall_time_s=round(time.monotonic() - self._started, 3),
        )
        for d in self.decisions:
            st.decisions_total += 1
            st.by[d.decided_by] = st.by.get(d.decided_by, 0) + 1
            st.low_confidence += int(d.low_confidence)
        return st

    def escalation_curve(self, steps: int = 21) -> list[tuple[float, int]]:
        """For thresholds 0.0..1.0, how many Jev decisions would fall back to the LLM."""
        confs = [c for d in self.decisions if (c := jev_confidence_of(d)) is not None]
        out: list[tuple[float, int]] = []
        for i in range(steps):
            t = round(i / (steps - 1), 4) if steps > 1 else 1.0
            out.append((t, sum(1 for c in confs if c < t)))
        return out

    def dump_jsonl(self, path: str | Path) -> Path:
        """Write one Decision per line; this is the `decisions_file` in results.json."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", encoding="utf-8") as f:
            for d in self.decisions:
                f.write(d.model_dump_json() + "\n")
        return p
