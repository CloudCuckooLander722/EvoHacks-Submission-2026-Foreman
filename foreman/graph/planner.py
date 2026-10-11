"""Planner: one LLM call turns a plain-English job into a validated plan.

The LLM sees a compact tool catalog, the item fields with example values, a handful of
sample items and one complete example plan (the hero plan). Its output is checked with
`validate_plan`; if anything is wrong, it gets one repair call with the exact problems.
The plan's wording matters more than anything else for result quality, because every
question Jev answers during the run is built from these params.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from foreman.core.ledger import Decision
from foreman.core.llm import LLMError, track_usage
from foreman.core.state import RunState
from foreman.graph.plan import (
    MAX_NODES,
    Plan,
    PlanError,
    format_validation_error,
    sample_fields,
    validate_plan,
)
from foreman.tools import catalog

if TYPE_CHECKING:
    from foreman.core.context import RunContext

log = logging.getLogger(__name__)

N_SAMPLES = 15
SAMPLE_CHARS = 200
N_EXAMPLE_VALUES = 2
EXAMPLE_CHARS = 60
MAX_TYPE_DEPTH = 2
HERO_PLAN_PATH = Path(__file__).resolve().parents[2] / "data" / "plans" / "reviews_backlog.json"

SYSTEM_RULES = f"""\
You are planning a bulk job made of tools. Each tool runs on every item; the items flow \
through the nodes in order, and nodes pass information to each other only through item \
fields (a node writes its `output_field`; later nodes read it, e.g. in `where`).
Output JSON only: {{"nodes": [{{"id", "tool", "params", "why"}}]}}.

Rules:
- Use the fewest nodes that do the job (at most {MAX_NODES}). Ids are n1, n2, ... in order.
- Run filter first when junk or irrelevant items are likely.
- `why` is one short sentence a user reads: what this step does for the job.
- Every question must ask about something stated in the text, with exactly one condition \
per option or level. Never write "X or Y" inside one option or level when they are two \
different conditions.
- Always fill in filter's `true_means` and `false_means`.
- Every classify option gets a description with a boundary, e.g. \
"billing": "charges, refunds, subscriptions - not login problems".
- Score dimensions and rank levels are concrete, ordered from lowest to highest, one \
condition each (not bare labels like "low"/"high").
- Never ask a model about numbers, counts, dates or totals; use calculate for those.
- Score dimensions must be things the text states (severity, frequency described), never \
guesses about the world (how many users are affected).
- For free-text items, dedupe uses mode "issues" with `block_by` set to a category or theme \
field written by an earlier node. For short records, it uses mode "records" with `key_fields`.
- rank uses `score_field` when a score node exists, and always sets `top_k` (at most 25).
- Use `where` to run a node only on the items it applies to (e.g. score only bugs). A \
`where` value must be exactly one of the values the field can hold (classify option keys).
- Only refer to fields that exist: "text", the item fields listed, or an earlier node's \
`output_field`. A score node also writes `<output_field>_<dimension name>`.
- End with a calculate node producing the counts and summaries a report would need.
"""

EXAMPLE_TEMPLATE = """
Example of a good plan for the job "{job}" (adapt it; do not copy params that do not fit):
{example}
"""

USER_TEMPLATE = """\
JOB: {job}

ITEMS: {n_items} items. Each has "text"{fields_line}.
{fields_block}
SAMPLE ITEMS ({n_samples}, text truncated to {sample_chars} characters):
{samples}

TOOLS (name: description; then params as `name: type = default - description`; \
every tool also takes `where: {{field, op, value}}` with op one of \
eq|ne|in|not_in|truthy|falsy|gte|lte):
{tools}
"""

REPAIR_TEMPLATE = """\
{user}

YOUR PREVIOUS PLAN:
{previous}

PROBLEMS WITH IT:
{problems}

Return the corrected full plan as JSON only, in the same format. Fix every problem listed."""


# --------------------------------------------------------------------------- catalog


def _type_str(schema: dict[str, Any], defs: dict[str, Any], depth: int = 0) -> str:
    """A short type for a JSON-schema node: `list[{name, levels: list[str]}]`, `a|b`, ..."""
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        target = defs.get(name, {})
        props = target.get("properties")
        if not props or depth >= MAX_TYPE_DEPTH:
            return name
        inner = ", ".join(
            f"{k}: {_type_str(v, defs, depth + 1)}" for k, v in props.items() if k != "where"
        )
        return "{" + inner + "}"
    if "anyOf" in schema:
        parts = [_type_str(s, defs, depth) for s in schema["anyOf"] if s.get("type") != "null"]
        return " | ".join(parts) or "null"
    if "enum" in schema:
        return "|".join(map(str, schema["enum"]))
    if "const" in schema:
        return json.dumps(schema["const"])
    t = schema.get("type")
    if t == "array":
        return f"list[{_type_str(schema.get('items', {}), defs, depth)}]"
    if t == "object":
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return f"dict[str, {_type_str(extra, defs, depth)}]"
        return "dict"
    if isinstance(t, str):
        return {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}.get(t, t)
    return "any"


def compact_catalog() -> str:
    """Every tool as a few lines: description, then one line per param (no `$defs` noise)."""
    blocks = []
    for entry in catalog():
        schema = entry["params_schema"]
        defs = schema.get("$defs", {})
        required = set(schema.get("required", []))
        lines = [f"## {entry['name']}: {entry['description']}"]
        for name, prop in schema.get("properties", {}).items():
            if name == "where":
                continue
            line = f"- {name}: {_type_str(prop, defs)}"
            if name in required:
                line += " (required)"
            elif "default" in prop:
                line += f" = {json.dumps(prop['default'])}"
            if prop.get("description"):
                line += f" - {prop['description']}"
            lines.append(line)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- items


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def field_examples(run: RunState) -> dict[str, list[str]]:
    """Each item field with up to two distinct example values, in first-seen order."""
    out: dict[str, list[str]] = {}
    for item in run.items:
        for k, v in item.fields.items():
            vals = out.setdefault(k, [])
            shown = _clip(json.dumps(v, default=str, ensure_ascii=False), EXAMPLE_CHARS)
            if len(vals) < N_EXAMPLE_VALUES and shown not in vals:
                vals.append(shown)
    return out


def sample_items(run: RunState, n: int = N_SAMPLES) -> list[str]:
    """`n` items spread evenly over the file (not just the first rows), text truncated."""
    items = run.items
    if len(items) > n:
        step = len(items) / n
        items = [items[int(i * step)] for i in range(n)]
    return [f"- [{i.id}] {_clip(i.text, SAMPLE_CHARS)}" for i in items]


def hero_plan() -> Plan:
    """The hand-written reviews -> bug backlog plan: the few-shot example and demo fallback."""
    return Plan.model_validate_json(HERO_PLAN_PATH.read_text(encoding="utf-8"))


def build_prompts(job: str, run: RunState, example: Plan | None = None) -> tuple[str, str]:
    """System and user prompts for one planning call."""
    example = example or hero_plan()
    example_json = json.dumps(
        {"nodes": [n.model_dump() for n in example.nodes]}, ensure_ascii=False, indent=1
    )
    system = SYSTEM_RULES + EXAMPLE_TEMPLATE.format(job=example.job, example=example_json)
    examples = field_examples(run)
    fields_line = (", plus fields: " + ", ".join(examples)) if examples else ""
    fields_block = "".join(f"- {k}: e.g. {', '.join(v)}\n" for k, v in examples.items())
    samples = sample_items(run)
    user = USER_TEMPLATE.format(
        job=job,
        n_items=len(run.items),
        fields_line=fields_line,
        fields_block=fields_block,
        n_samples=len(samples),
        sample_chars=SAMPLE_CHARS,
        samples="\n".join(samples),
        tools=compact_catalog(),
    )
    return system, user


# --------------------------------------------------------------------------- planning


def _to_plan(job: str, raw: dict[str, Any]) -> tuple[Plan | None, list[str]]:
    """Parse the LLM's JSON into a Plan, tolerating missing ids/why; problems on failure."""
    nodes = raw.get("nodes")
    if not isinstance(nodes, list):
        return None, ['the reply must be a JSON object with a "nodes" list']
    fixed = []
    for i, n in enumerate(nodes, start=1):
        if isinstance(n, dict):
            n = {**n}
            n.setdefault("id", f"n{i}")
            if n.get("params") is None:
                n["params"] = {}
            if n.get("why") is None:
                n["why"] = ""
        fixed.append(n)
    try:
        return Plan.model_validate({"job": job, "nodes": fixed}), []
    except ValidationError as e:
        return None, [f"plan: {msg}" for msg in format_validation_error(e)]


async def _call(ctx: RunContext, system: str, user: str, attempt: int) -> dict[str, Any]:
    """One planner LLM call, charged to the run and logged as a proposal."""
    t0 = time.perf_counter()
    with track_usage() as u:
        try:
            raw = await ctx.llm.json(system, user)
        except LLMError as e:
            raise PlanError(f"planner LLM call failed: {e}") from e
        finally:
            if u.calls:
                ctx.ledger.record_call("llm", u.cost_usd, n=u.calls)
    ctx.ledger.add(
        Decision(
            run_id=ctx.run_id,
            item_id="__run__",
            tool="planner",
            question_id="plan" if attempt == 0 else f"plan_repair_{attempt}",
            qtype="plan",
            value=raw.get("nodes", raw),
            confidence=None,
            probabilities=None,
            decided_by="llm",
            kind="proposal",
            latency_ms=int((time.perf_counter() - t0) * 1000),
            cost_usd=u.cost_usd,
        )
    )
    return raw


async def make_plan(job: str, run: RunState, ctx: RunContext, *, max_repairs: int = 1) -> Plan:
    """Ask the LLM for a plan, validate it, and repair it up to `max_repairs` times.

    Raises `PlanError` (with `.problems`) when the LLM fails or the plan stays invalid.
    """
    fields = sample_fields(run.items)
    system, user = build_prompts(job, run)
    prompt = user
    problems: list[str] = []
    for attempt in range(max_repairs + 1):
        raw = await _call(ctx, system, prompt, attempt)
        plan, problems = _to_plan(job, raw)
        if plan is not None:
            problems = validate_plan(plan, fields)
            if not problems:
                return plan
        log.warning("plan attempt %d has %d problems: %s", attempt + 1, len(problems), problems)
        prompt = REPAIR_TEMPLATE.format(
            user=user,
            previous=json.dumps(raw, ensure_ascii=False, indent=1),
            problems="\n".join(f"- {p}" for p in problems),
        )
    raise PlanError(f"the planner's plan is still invalid after {max_repairs} repair(s)", problems)
