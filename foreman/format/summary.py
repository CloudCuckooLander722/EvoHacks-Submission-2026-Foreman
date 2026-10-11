"""A plain-English summary of a run for a manager (MINH_PLAN §B1).

One LLM call writes the prose from run-level numbers only (never per-item data). The
model is not allowed to compute: every number it writes must appear in its input, which
is checked in code. Any invented number, or any failure, gives a template summary built
in code instead, so a summary always comes back.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from foreman.contracts import ResultsFile
from foreman.core.context import RunContext
from foreman.core.ledger import Decision
from foreman.core.llm import track_usage
from foreman.format.charts import bars_of, title_of

log = logging.getLogger(__name__)

SYSTEM = (
    "You write the summary of a finished bulk-processing run for a busy manager. "
    "Write 3 to 5 plain sentences: what was done, the main results, and who made the "
    "decisions (Jev is a fast decision model; the LLM is the fallback when Jev was unsure). "
    "Use only numbers that appear in the input, copied exactly; do no arithmetic, no "
    "rounding and no percentages that are not in the input. No lists, no markdown. "
    'Reply with JSON: {"summary": "..."}'
)
_NUMBER = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?%?")
SMALL_INT_OK = 10  # "the top 5", "three themes": counting words are not computed numbers
BIG_LIST = 12  # aggregate lists longer than this are id lists, not summary material


def _compact_aggregates(aggregates: dict[str, Any]) -> dict[str, Any]:
    """Aggregates without the curve, the old summary and long id lists."""
    out: dict[str, Any] = {}
    for key, value in aggregates.items():
        if key in {"summary", "escalation_curve"}:
            continue
        if isinstance(value, list) and len(value) > BIG_LIST and bars_of(value) is None:
            continue
        if isinstance(value, list):
            value = [
                {k: v for k, v in r.items() if k not in {"example_ids", "member_ids"}}
                if isinstance(r, dict)
                else r
                for r in value
            ]
        out[key] = value
    return out


def summary_input(results: ResultsFile) -> dict[str, Any]:
    """Everything the LLM may see: run-level numbers, plus a few computed in code."""
    st = results.stats
    model = st.by.get("jev", 0) + st.by.get("llm", 0)
    items_in = results.node_reports[0].items_in if results.node_reports else len(results.items)
    return {
        "job": results.job,
        "tools": [n.get("tool") for n in results.plan.get("nodes", [])],
        "items_in": items_in,
        "items_kept": len(results.items),
        "items_dropped": len(results.dropped),
        "aggregates": _compact_aggregates(results.aggregates),
        "stats": st.model_dump(),
        "computed": {
            "jev_share_of_model_decisions_pct": round(100 * st.by.get("jev", 0) / model, 1)
            if model
            else 0,
            "total_cost_usd": round(st.jev_cost_usd + st.llm_cost_usd, 4),
            "wall_time_s": round(st.wall_time_s, 1),
        },
        "node_reports": [
            {
                "tool": r.tool,
                "items_in": r.items_in,
                "items_out": r.items_out,
                "jev_decisions": r.jev_decisions,
                "llm_decisions": r.llm_decisions,
            }
            for r in results.node_reports
        ],
    }


def _numbers_in(obj: Any) -> set[float]:
    found: set[float] = set()
    if isinstance(obj, bool):
        return found
    if isinstance(obj, (int, float)):
        found.add(float(obj))
    elif isinstance(obj, str):
        found |= {_to_float(m) for m in _NUMBER.findall(obj)}
    elif isinstance(obj, dict):
        for k, v in obj.items():
            found |= _numbers_in(k) | _numbers_in(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _numbers_in(v)
    return found


def _to_float(token: str) -> float:
    return float(token.replace("$", "").replace(",", "").replace("%", "").lstrip("+"))


def invented_numbers(summary: str, source: dict[str, Any]) -> list[str]:
    """Numbers in `summary` that do not appear in `source` (an exact or rounded match)."""
    allowed = _numbers_in(source)
    bad: list[str] = []
    for token in _NUMBER.findall(summary):
        value = _to_float(token)
        if value.is_integer() and 0 <= value <= SMALL_INT_OK:
            continue
        decimals = len(token.split(".")[1].rstrip("%")) if "." in token else 0
        if any(abs(round(a, decimals) - value) < 1e-9 for a in allowed):
            continue
        bad.append(token)
    return bad


def _top(counts: list[tuple[str, float]], n: int = 3) -> str:
    parts = [f"{label} ({v:,.0f})" for label, v in sorted(counts, key=lambda b: -b[1])[:n]]
    return ", ".join(parts)


def template_summary(results: ResultsFile) -> str:
    """The code-built summary: always correct numbers, plainer prose."""
    data = summary_input(results)
    st = results.stats
    tools = ", ".join(t for t in data["tools"] if t) or "the plan"
    sentences = [
        (
            f"Foreman processed {data['items_in']:,} items with {tools}; "
            f"{data['items_kept']:,} were kept and {data['items_dropped']:,} dropped."
        )
    ]
    for key, value in data["aggregates"].items():
        bars = bars_of(value)
        if bars and len(sentences) < 3:
            sentences.append(f"{title_of(key)}: {_top(bars)}.")
    model = st.by.get("jev", 0) + st.by.get("llm", 0)
    if model:
        sentences.append(
            f"Jev made {st.by.get('jev', 0):,} of the {model:,} model decisions "
            f"({data['computed']['jev_share_of_model_decisions_pct']}%), and the LLM "
            f"made {st.by.get('llm', 0):,}."
        )
    sentences.append(
        f"The run took {data['computed']['wall_time_s']} s and cost "
        f"${data['computed']['total_cost_usd']}."
    )
    return " ".join(sentences)


async def summarize(results: ResultsFile, ctx: RunContext) -> str:
    """3-5 sentences for a manager; the LLM's prose if its numbers check out, else the template."""
    source = summary_input(results)
    user = json.dumps(source, ensure_ascii=False, default=str)
    cost, latency, text = 0.0, 0, ""
    with track_usage() as u:
        try:
            raw = await ctx.llm.json(SYSTEM, user)
            text = str(raw.get("summary") or "").strip()
        except Exception as e:  # noqa: BLE001 - LLMError, timeouts, junk: the template answers
            log.warning("summary LLM call failed (%s); using the template", e)
        finally:
            if u.calls:
                ctx.ledger.record_call("llm", u.cost_usd, n=u.calls)
            cost, latency = u.cost_usd, u.latency_ms
    if text:
        bad = invented_numbers(text, source)
        if bad:
            log.warning("summary used numbers not in the input %s; using the template", bad)
            text = ""
    if text:
        ctx.ledger.add(
            Decision(
                run_id=ctx.run_id,
                item_id="__run__",
                tool="format",
                question_id="summary",
                qtype="proposal",
                value=text,
                confidence=None,
                probabilities=None,
                decided_by="llm",
                kind="proposal",
                latency_ms=latency,
                cost_usd=cost,
            )
        )
        return text
    return template_summary(results)
