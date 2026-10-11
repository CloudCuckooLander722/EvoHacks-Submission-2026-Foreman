"""Plan schema, field tracking and `validate_plan`.

A plan is a short list of tool nodes run in order over one `RunState`. Nodes talk to each
other only through item fields (classify writes `type`, a later node routes on
`where: {field: "type", ...}`), so the one thing that most often goes wrong in an
LLM-written plan is a node reading a field nothing has written yet. `FieldScope` tracks
which fields exist at each point; the planner uses it to reject bad plans before they run
and the executor uses it to skip nodes whose inputs a failed node never produced.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from foreman.core.state import ItemState
from foreman.tools import get_tool
from foreman.tools.base import ToolParams

MAX_NODES = 12
TEXT = "text"
# Ops in a Calculate node that write a field per item (the rest write run aggregates).
_CALC_ROW_OPS = frozenset(
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
_VALUE_OPS = frozenset({"eq", "ne", "in", "not_in"})


class PlanNode(BaseModel):
    """One step: a registered tool, its params, and a one-line reason shown in the UI."""

    id: str
    tool: str
    params: dict[str, Any] = Field(default_factory=dict)
    why: str = ""


class Plan(BaseModel):
    """The job and the nodes that do it, run in order."""

    job: str
    nodes: list[PlanNode] = Field(min_length=1, max_length=MAX_NODES)


class PlanError(Exception):
    """The planner could not produce a valid plan; `problems` says why."""

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        super().__init__(message)
        self.problems = problems or []


# --------------------------------------------------------------------------- fields


def sample_fields(items: list[ItemState]) -> set[str]:
    """Every field name present on at least one item (the columns from items.json)."""
    return {k for i in items for k in i.fields}


class FieldScope:
    """Which fields exist at a point in the plan.

    A node "produces" its `output_field` and anything prefixed `f"{output_field}_"`
    (`urgency_severity`, `rank_score`, `type__confidence`), plus Calculate row-op `as`
    names. Classify also records its possible values so `where` filters can be checked.
    """

    def __init__(self, fields: set[str]) -> None:
        self.fields = {TEXT, *fields}
        self.prefixes: set[str] = set()
        self.values: dict[str, set[Any]] = {}

    def has(self, field: str) -> bool:
        return field in self.fields or any(field.startswith(p) for p in self.prefixes)

    def add_output(self, field: str, values: set[Any] | None = None) -> None:
        self.fields.add(field)
        self.prefixes.add(f"{field}_")
        if values is not None:
            self.values[field] = values
        else:
            self.values.pop(field, None)

    def add_node(self, tool: str, params: dict[str, Any]) -> None:
        """Record what a node (with validated, dumped params) writes."""
        out = params.get("output_field")
        if tool == "calculate":
            for op in params.get("operations") or []:
                if op.get("op") in _CALC_ROW_OPS and op.get("as"):
                    self.fields.add(op["as"])
            return
        if isinstance(out, str):
            self.add_output(out, _known_values(tool, params))


def _known_values(tool: str, params: dict[str, Any]) -> set[Any] | None:
    """Values a node can write to its output field, when they are fixed by its params."""
    if tool == "filter":
        return {True, False}
    if tool != "classify":
        return None
    keys: set[Any] = set()
    if params.get("options"):
        keys |= set(params["options"])
    stack = [params.get("taxonomy") or {}]
    while stack:
        level = stack.pop()
        for key, node in level.items():
            keys.add(key)
            if isinstance(node, dict) and node.get("children"):
                stack.append(node["children"])
    if params.get("add_other", True):
        keys.add("other")
    return keys


def _is_number(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int | float):
        return True
    try:
        float(str(v).replace(",", ""))
    except ValueError:
        return False
    return True


def _where_refs(where: Any, loc: str) -> list[tuple[str, str]]:
    if isinstance(where, dict) and isinstance(where.get("field"), str):
        return [(f"{loc}.field", where["field"])]
    return []


def field_refs(tool: str, params: dict[str, Any]) -> list[tuple[str, str]]:
    """(location, field) for every field a node reads, except Calculate's intra-node ones.

    Generic over tools: `where.field`, any `*_field` (input_field, score_field,
    claim_field, source_field, goal_field...), any `*_fields` list, and `block_by`/`by`.
    """
    refs: list[tuple[str, str]] = []
    for key, v in params.items():
        if key == "output_field" or key == "operations":
            continue
        if key == "where":
            refs += _where_refs(v, "where")
        elif (key.endswith("_field") or key in ("block_by", "by")) and isinstance(v, str):
            refs.append((key, v))
        elif key.endswith("_fields") and isinstance(v, list):
            refs += [(f"{key}[{i}]", str(f)) for i, f in enumerate(v)]
    return refs


def missing_fields(tool: str, params: dict[str, Any], scope: FieldScope) -> list[tuple[str, str]]:
    """Fields this node reads that `scope` lacks. Does not change `scope`.

    Calculate ops run in order, so an op may read a field an earlier row op in the same
    node wrote; `a`/`b` strings that parse as numbers are literals, not fields.
    """
    missing = [(loc, f) for loc, f in field_refs(tool, params) if not scope.has(f)]
    if tool != "calculate":
        return missing
    local: set[str] = set()
    for i, op in enumerate(params.get("operations") or []):
        refs = [(f"operations[{i}].{k}", op.get(k)) for k in ("field", "by")]
        refs += [
            (f"operations[{i}].{k}", op.get(k)) for k in ("a", "b") if not _is_number(op.get(k))
        ]
        refs += _where_refs(op.get("where"), f"operations[{i}].where")
        for loc, f in refs:
            if isinstance(f, str) and not (scope.has(f) or f in local):
                missing.append((loc, f))
        if op.get("op") in _CALC_ROW_OPS and op.get("as"):
            local.add(op["as"])
    return missing


def _where_value_problems(params: dict[str, Any], scope: FieldScope) -> list[str]:
    """`where` values that the field it filters on can never hold (e.g. 'Bug' vs 'bug')."""
    wheres = [("where", params.get("where"))]
    wheres += [
        (f"operations[{i}].where", op.get("where"))
        for i, op in enumerate(params.get("operations") or [])
    ]
    out: list[str] = []
    for loc, w in wheres:
        if not isinstance(w, dict) or w.get("op") not in _VALUE_OPS:
            continue
        allowed = scope.values.get(w.get("field", ""))
        if allowed is None:
            continue
        value = w.get("value")
        values = value if w["op"] in ("in", "not_in") and isinstance(value, list) else [value]
        bad = [v for v in values if v not in allowed]
        if bad:
            choices = ", ".join(sorted(map(str, allowed)))
            out.append(
                f"{loc}.value {bad!r} is not a value of {w['field']!r} (it can be: {choices})"
            )
    return out


# --------------------------------------------------------------------------- validation


def validate_params(tool: str, params: dict[str, Any]) -> ToolParams:
    """The tool's Params model for `params`; raises KeyError or ValidationError."""
    return get_tool(tool).Params.model_validate(params)


def format_validation_error(e: ValidationError) -> list[str]:
    """Pydantic errors as short 'loc: message' strings an LLM (or a person) can act on."""
    out = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "(params)"
        out.append(f"{loc}: {err['msg']}")
    return out


def dump_params(params: ToolParams) -> dict[str, Any]:
    """Validated params as plain JSON-ready data, with aliases (`as`) and defaults."""
    return params.model_dump(mode="json", by_alias=True)


def validate_plan(plan: Plan, sample_fields: set[str]) -> list[str]:
    """Every problem with a plan, as readable strings; an empty list means it can run.

    Checks: known tools, params valid for each tool, unique node ids, and every field a
    node reads exists by then ("text", a sample field, or an earlier node's output).
    """
    problems: list[str] = []
    if not plan.nodes:
        return ["the plan has no nodes"]
    if len(plan.nodes) > MAX_NODES:
        problems.append(f"the plan has {len(plan.nodes)} nodes; the maximum is {MAX_NODES}")
    seen: set[str] = set()
    scope = FieldScope(sample_fields)
    for node in plan.nodes:
        tag = f"{node.id} ({node.tool})"
        if node.id in seen:
            problems.append(f"{tag}: duplicate node id {node.id!r}")
        seen.add(node.id)
        try:
            params = dump_params(validate_params(node.tool, node.params))
        except KeyError as e:
            problems.append(f"{tag}: {e.args[0]}")
            continue
        except ValidationError as e:
            problems += [f"{tag}: params.{msg}" for msg in format_validation_error(e)]
            continue
        for loc, f in missing_fields(node.tool, params, scope):
            problems.append(
                f"{tag}: {loc} refers to field {f!r}, which does not exist at this point "
                "(not in the items and not written by an earlier node)"
            )
        problems += [f"{tag}: {p}" for p in _where_value_problems(params, scope)]
        scope.add_node(node.tool, params)
    return problems
