"""Calculate: totals, counts, group-bys, percentages and date math in plain Python.

Jev (and LLMs) are unreliable at numbers, so every number in a Foreman result comes from
this tool. It never calls a model. Operations run in order, so a later operation can use
a field written by an earlier one (e.g. `parse_number` then `group_sum` on its output).
"""

from __future__ import annotations

import bisect
import json
import math
import re
import statistics
import time
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from dateutil import parser as date_parser
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from foreman.core.ledger import Decision
from foreman.core.state import Condition, ItemState, RunState, set_output
from foreman.tools.base import Tool, ToolParams, ToolReport, select

if TYPE_CHECKING:
    from foreman.core.context import RunContext

OpName = Literal[
    "parse_number",
    "parse_date",
    "add",
    "sub",
    "mul",
    "div",
    "abs",
    "round",
    "date_diff_days",
    "days_since",
    "bucket",
    "count",
    "count_true",
    "sum",
    "mean",
    "median",
    "min",
    "max",
    "group_count",
    "group_sum",
    "group_mean",
    "share",
    "top_n",
]

ROW_OPS = frozenset(
    {
        "parse_number",
        "parse_date",
        "add",
        "sub",
        "mul",
        "div",
        "abs",
        "round",
        "date_diff_days",
        "days_since",
        "bucket",
    }
)

# Arguments each op cannot run without; checked when the params are validated so a bad
# plan fails before the run starts rather than halfway through.
REQUIRED: dict[str, tuple[str, ...]] = {
    "parse_number": ("field",),
    "parse_date": ("field",),
    "add": ("a", "b"),
    "sub": ("a", "b"),
    "mul": ("a", "b"),
    "div": ("a", "b"),
    "abs": ("field",),
    "round": ("field",),
    "date_diff_days": ("a", "b"),
    "days_since": ("field",),
    "bucket": ("field", "edges", "labels"),
    "count": (),
    "count_true": ("field",),
    "sum": ("field",),
    "mean": ("field",),
    "median": ("field",),
    "min": ("field",),
    "max": ("field",),
    "group_count": ("by",),
    "group_sum": ("by", "field"),
    "group_mean": ("by", "field"),
    "share": ("by",),
    "top_n": ("field",),
}

DEFAULT_TOP_N = 10
_TRUE_WORDS = frozenset({"true", "yes", "y", "1", "t"})
_CURRENCY = re.compile(r"[$€£¥₹₩₽¢]|\b(?:usd|eur|gbp|jpy|inr|cad|aud)\b", re.IGNORECASE)
_NUMBER = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_BLANKS = frozenset({"", "-", "--", "—", "–", "n/a", "na", "none", "null", "nan"})


# --------------------------------------------------------------------------- parsing


def _normalize_separators(s: str) -> str | None:
    """Turn `1,234.50` / `1.234,50` / `1 234,5` into `1234.50`-style text.

    A lone comma is a decimal point only when it can't be a thousands separator (not
    followed by exactly three digits), so `1,234` stays one thousand two hundred and four.
    """
    s = re.sub(r"[\s  ']", "", s)
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            return s.replace(".", "").replace(",", ".")
        return s.replace(",", "")
    if s.count(",") > 1:
        return s.replace(",", "")
    if s.count(",") == 1:
        after = s.split(",", 1)[1]
        return s.replace(",", "") if len(after) == 3 and after.isdigit() else s.replace(",", ".")
    if s.count(".") > 1:
        return s.replace(".", "")
    return s


def to_number(v: Any) -> float | None:
    """Parse a messy cell into a float, or None when it isn't a number.

    Shared by `parse_number` and every numeric aggregate so `"$1,200"` sums the same way it
    parses. Handles currency, `(12.00)` as negative, `12%` as 12.0, thousands separators
    and an unambiguous comma decimal. Booleans are not numbers.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        f = float(v)
        return f if math.isfinite(f) else None
    if not isinstance(v, str):
        return None
    s = v.strip().replace("−", "-")
    if s.lower() in _BLANKS:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1].strip()
    s = _CURRENCY.sub("", s).strip()
    if s.endswith("%"):
        s = s[:-1].strip()
    if s.startswith("-") or s.endswith("-"):
        negative, s = not negative, s.strip("-").strip()
    s = _CURRENCY.sub("", s).strip()  # "-$5" leaves "$5" after the sign is removed
    norm = _normalize_separators(s)
    if not norm or not _NUMBER.match(norm):
        return None
    f = float(norm)
    if not math.isfinite(f):
        return None
    return -f if negative else f


def to_date(v: Any, today: date) -> date | None:
    """Parse a date-like cell; missing parts default to Jan 1 of `today`'s year."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    if s.lower() in _BLANKS:
        return None
    try:
        return date_parser.parse(s, default=datetime(today.year, 1, 1)).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() in _TRUE_WORDS
    return bool(v)


def _group_key(v: Any) -> str | None:
    """A JSON-safe dict key for a group value; None for missing/blank values."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    return v if isinstance(v, str) else json.dumps(v, default=str)


def _get(item: ItemState, name: str) -> Any:
    return item.text if name == "text" else item.fields.get(name)


# --------------------------------------------------------------------------- params


class Operation(BaseModel):
    """One calculation step. Row ops write `item.fields[as]`; aggregates write `run.aggregates[as]`."""

    model_config = ConfigDict(
        validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    op: OpName
    as_: str = Field(alias="as", min_length=1, description="Output field or aggregate name.")
    field: str | None = None
    a: str | float | None = Field(
        default=None, description="Field name or numeric literal (date field for date_diff_days)."
    )
    b: str | float | None = Field(
        default=None, description="Field name or numeric literal (date field for date_diff_days)."
    )
    by: str | None = Field(default=None, description="Field to group by.")
    n: int | None = Field(default=None, ge=1, description=f"top_n size (default {DEFAULT_TOP_N}).")
    edges: list[float] | None = Field(default=None, description="bucket edges, ascending.")
    labels: list[str] | None = Field(default=None, description="bucket labels, len(edges)+1.")
    ndigits: int | None = Field(default=None, description="Rounding for round and numeric results.")
    desc: bool = Field(default=True, description="top_n: largest first.")
    where: Condition | None = Field(default=None, description="ANDed with the tool-level where.")

    @model_validator(mode="after")
    def _check_args(self) -> Operation:
        missing = [k for k in REQUIRED[self.op] if getattr(self, k) is None]
        if missing:
            raise ValueError(f"op {self.op!r} requires {', '.join(missing)}")
        if self.op == "bucket":
            assert self.edges is not None and self.labels is not None
            if len(self.labels) != len(self.edges) + 1:
                raise ValueError("bucket needs len(labels) == len(edges) + 1")
            if self.edges != sorted(self.edges):
                raise ValueError("bucket edges must be ascending")
        return self


class CalculateParams(ToolParams):
    """Params for `calculate`. `output_field` is required by the base class but unused."""

    output_field: str = "calc"
    operations: list[Operation] = Field(min_length=1)
    today: str | None = Field(default=None, description="ISO date for days_since; default today.")

    @field_validator("today")
    @classmethod
    def _iso(cls, v: str | None) -> str | None:
        if v is not None:
            date.fromisoformat(v)
        return v


# --------------------------------------------------------------------------- ops


RowFn = Callable[[ItemState, Operation, date], Any]
AggResult = tuple[Any, int]  # (value, skipped)


def _operand(item: ItemState, ref: str | float | None) -> float | None:
    """A field's numeric value, or the literal itself when no such field exists."""
    if ref is None:
        return None
    if isinstance(ref, int | float):
        return to_number(ref)
    if ref == "text" or ref in item.fields:
        return to_number(_get(item, ref))
    return to_number(ref)


def _arith(fn: Callable[[float, float], float]) -> RowFn:
    def row(item: ItemState, op: Operation, _today: date) -> Any:
        x, y = _operand(item, op.a), _operand(item, op.b)
        if x is None or y is None:
            return None
        try:
            out = fn(x, y)
        except ZeroDivisionError:
            return None
        return out if math.isfinite(out) else None

    return row


def _parse_date(item: ItemState, op: Operation, today: date) -> str | None:
    d = to_date(_get(item, op.field or ""), today)
    return d.isoformat() if d else None


def _abs(item: ItemState, op: Operation, _today: date) -> float | None:
    x = to_number(_get(item, op.field or ""))
    return None if x is None else abs(x)


def _round(item: ItemState, op: Operation, _today: date) -> float | None:
    x = to_number(_get(item, op.field or ""))
    return None if x is None else round(x, op.ndigits)


def _date_diff(item: ItemState, op: Operation, today: date) -> int | None:
    a = to_date(_get(item, str(op.a)), today)
    b = to_date(_get(item, str(op.b)), today)
    return None if a is None or b is None else (b - a).days


def _days_since(item: ItemState, op: Operation, today: date) -> int | None:
    d = to_date(_get(item, op.field or ""), today)
    return None if d is None else (today - d).days


def _bucket(item: ItemState, op: Operation, _today: date) -> str | None:
    x = to_number(_get(item, op.field or ""))
    if x is None or op.edges is None or op.labels is None:
        return None
    return op.labels[bisect.bisect_right(op.edges, x)]


ROW_FNS: dict[str, RowFn] = {
    "parse_number": lambda item, op, _t: to_number(_get(item, op.field or "")),
    "parse_date": _parse_date,
    "add": _arith(lambda x, y: x + y),
    "sub": _arith(lambda x, y: x - y),
    "mul": _arith(lambda x, y: x * y),
    "div": _arith(lambda x, y: x / y),
    "abs": _abs,
    "round": _round,
    "date_diff_days": _date_diff,
    "days_since": _days_since,
    "bucket": _bucket,
}


def _numbers(items: list[ItemState], field: str) -> tuple[list[tuple[ItemState, float]], int]:
    out: list[tuple[ItemState, float]] = []
    for item in items:
        x = to_number(_get(item, field))
        if x is not None:
            out.append((item, x))
    return out, len(items) - len(out)


def _maybe_round(v: Any, ndigits: int | None) -> Any:
    return round(v, ndigits) if ndigits is not None and isinstance(v, float) else v


def _sorted_desc(d: dict[str, Any]) -> dict[str, Any]:
    return dict(sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))


def _stat(items: list[ItemState], op: Operation) -> AggResult:
    pairs, skipped = _numbers(items, op.field or "")
    xs = [x for _, x in pairs]
    if op.op == "sum":
        return _maybe_round(math.fsum(xs), op.ndigits), skipped
    if not xs:
        return None, skipped
    fn: dict[str, Callable[[list[float]], float]] = {
        "mean": statistics.fmean,
        "median": statistics.median,
        "min": min,
        "max": max,
    }
    return _maybe_round(float(fn[op.op](xs)), op.ndigits), skipped


def _groups(items: list[ItemState], by: str) -> tuple[dict[str, list[ItemState]], int]:
    groups: dict[str, list[ItemState]] = defaultdict(list)
    skipped = 0
    for item in items:
        key = _group_key(_get(item, by))
        if key is None:
            skipped += 1
        else:
            groups[key].append(item)
    return groups, skipped


def _group_count(items: list[ItemState], op: Operation) -> AggResult:
    groups, skipped = _groups(items, op.by or "")
    return _sorted_desc({k: len(v) for k, v in groups.items()}), skipped


def _group_num(items: list[ItemState], op: Operation) -> AggResult:
    groups, skipped = _groups(items, op.by or "")
    out: dict[str, float] = {}
    for key, members in groups.items():
        pairs, k = _numbers(members, op.field or "")
        skipped += k
        xs = [x for _, x in pairs]
        if op.op == "group_sum":
            out[key] = _maybe_round(math.fsum(xs), op.ndigits)
        elif xs:
            out[key] = _maybe_round(statistics.fmean(xs), op.ndigits)
    return _sorted_desc(out), skipped


def _share(items: list[ItemState], op: Operation) -> AggResult:
    groups, skipped = _groups(items, op.by or "")
    total = sum(len(v) for v in groups.values())
    if total == 0:
        return {}, skipped
    counts = _sorted_desc({k: len(v) for k, v in groups.items()})
    return {k: round(100 * c / total, 1) for k, c in counts.items()}, skipped


def _top_n(items: list[ItemState], op: Operation) -> AggResult:
    pairs, skipped = _numbers(items, op.field or "")
    pairs.sort(key=lambda p: p[1], reverse=op.desc)  # stable: ties keep input order
    return [item.id for item, _ in pairs[: op.n or DEFAULT_TOP_N]], skipped


AGG_FNS: dict[str, Callable[[list[ItemState], Operation], AggResult]] = {
    "count": lambda items, _op: (len(items), 0),
    "count_true": lambda items, op: (sum(_truthy(_get(i, op.field or "")) for i in items), 0),
    "sum": _stat,
    "mean": _stat,
    "median": _stat,
    "min": _stat,
    "max": _stat,
    "group_count": _group_count,
    "group_sum": _group_num,
    "group_mean": _group_num,
    "share": _share,
    "top_n": _top_n,
}


# --------------------------------------------------------------------------- tool


class Calculate(Tool):
    """Pure-code math over the run; no model is ever called."""

    name: ClassVar[str] = "calculate"
    description: ClassVar[str] = (
        "Does all math, counting, date arithmetic and aggregation in plain code (never a "
        "model). Use it for any totals, counts, group-bys, percentages, averages or date "
        "differences. Row operations write a field on each item: parse_number, parse_date, "
        "add, sub, mul, div, abs, round, date_diff_days, days_since, bucket. Aggregate "
        "operations write run aggregates: count, count_true, sum, mean, median, min, max, "
        "group_count, group_sum, group_mean, share (percent per value), top_n (item ids). "
        "Operations run in order, so later ones can use fields written by earlier ones."
    )
    Params: ClassVar[type[ToolParams]] = CalculateParams
    uses: ClassVar = "code"

    async def run(self, run: RunState, params: ToolParams, ctx: RunContext) -> ToolReport:
        """Run each operation in order, log one compute Decision per op, and report."""
        assert isinstance(params, CalculateParams)
        t0 = time.perf_counter()
        today = date.fromisoformat(params.today) if params.today else date.today()
        report = ToolReport(tool=self.name, items_in=len(run.items), items_out=len(run.items))
        total = len(params.operations)

        for done, op in enumerate(params.operations, start=1):
            items = [
                i for i in select(run, params.where) if op.where is None or op.where.matches(i)
            ]
            if op.op in ROW_OPS:
                written, skipped = self._row(items, op, today)
                report.code_ops += written
                value: Any = {"rows_written": written, "skipped": skipped}
            else:
                value, skipped = AGG_FNS[op.op](items, op)
                run.aggregates[op.as_] = value
                report.code_ops += 1
            if skipped:
                report.notes.append(f"{op.op} -> {op.as_}: skipped {skipped} of {len(items)}")
            ctx.ledger.add(
                Decision(
                    run_id=ctx.run_id,
                    item_id="__run__",
                    tool=self.name,
                    question_id=op.as_,
                    qtype=op.op,
                    value=value,
                    confidence=1.0,
                    probabilities=None,
                    decided_by="code",
                    kind="compute",
                )
            )
            if ctx.progress is not None:
                ctx.progress(self.name, done, total)

        if params.where is not None:
            n = len(select(run, params.where))
            report.notes.append(f"{n} of {len(run.items)} items matched `where`")
        report.duration_ms = int((time.perf_counter() - t0) * 1000)
        return report

    @staticmethod
    def _row(items: list[ItemState], op: Operation, today: date) -> tuple[int, int]:
        """Write `op.as_` on every item (None when it can't be computed); count both kinds."""
        fn = ROW_FNS[op.op]
        written = 0
        for item in items:
            value = fn(item, op, today)
            set_output(item, op.as_, value, confidence=1.0, by="code")
            written += value is not None
        return written, len(items) - written


__all__ = ["Calculate", "CalculateParams", "Operation", "to_date", "to_number"]
