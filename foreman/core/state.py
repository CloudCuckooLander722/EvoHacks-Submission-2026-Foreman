"""ItemState, RunState, Condition (the "node state").

Every item flows through the plan as an `ItemState`. Tools read its fields and write new
ones, so the plan is just a list of nodes reading and writing typed fields on shared state.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

_MISSING = object()


class ItemState(BaseModel):
    """One unit of work (a row, ticket, review or document section)."""

    id: str
    text: str
    fields: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)


class RunState(BaseModel):
    """Everything a run carries between nodes: live items, dropped items and aggregates."""

    run_id: str
    job: str
    items: list[ItemState]
    dropped: list[ItemState] = Field(default_factory=list)
    aggregates: dict[str, Any] = Field(default_factory=dict)


class Condition(BaseModel):
    """A routing predicate; lets the plan branch without a graph engine.

    A leaf tests one field (`field` + `op` + `value`). `all` (AND) and `any` (OR) combine
    nested conditions, e.g. "a bug AND the canonical copy of its duplicate group". When a
    condition has several parts (leaf, `all`, `any`), every part must hold.
    """

    field: str | None = None
    op: Literal["eq", "ne", "in", "not_in", "truthy", "falsy", "gte", "lte"] | None = None
    value: Any = None
    all: list[Condition] | None = None
    any: list[Condition] | None = None

    @model_validator(mode="after")
    def _has_a_test(self) -> Condition:
        if (self.field is None) != (self.op is None):
            raise ValueError("a field condition needs both `field` and `op`")
        if self.field is None and not self.all and not self.any:
            raise ValueError("give `field` and `op`, or a non-empty `all` or `any` list")
        return self

    def fields(self) -> list[str]:
        """Every field this condition reads, including inside `all`/`any`."""
        out = [self.field] if self.field is not None else []
        for sub in [*(self.all or []), *(self.any or [])]:
            out += sub.fields()
        return out

    def matches(self, item: ItemState) -> bool:
        """True if the item passes. A missing field never matches, except for `falsy`."""
        if self.field is not None and not self._leaf(item):
            return False
        if self.all and not all(c.matches(item) for c in self.all):
            return False
        return not self.any or any(c.matches(item) for c in self.any)

    def _leaf(self, item: ItemState) -> bool:
        if self.field == "text":
            v: Any = item.text
        else:
            v = item.fields.get(self.field, _MISSING)
        if v is _MISSING:
            return self.op == "falsy"
        try:
            match self.op:
                case "eq":
                    return v == self.value
                case "ne":
                    return v != self.value
                case "in":
                    return v in (self.value or [])
                case "not_in":
                    return v not in (self.value or [])
                case "truthy":
                    return bool(v)
                case "falsy":
                    return not v
                case "gte":
                    return v is not None and v >= self.value
                case "lte":
                    return v is not None and v <= self.value
        except TypeError:
            # Comparing incompatible types (e.g. str >= int) is a non-match, not a crash.
            return False
        return False


def get_input(item: ItemState, field: str) -> str:
    """Resolve a tool's `input_field`: "text" is the item text, anything else a field."""
    if field == "text":
        return item.text
    return str(item.fields.get(field, ""))


def set_output(
    item: ItemState,
    field: str,
    value: Any,
    *,
    confidence: float | None,
    by: str,
    low_confidence: bool = False,
) -> None:
    """Write a tool output with its provenance so the UI and reports can explain every cell."""
    item.fields[field] = value
    item.fields[f"{field}__confidence"] = confidence
    item.fields[f"{field}__by"] = by
    if low_confidence:
        item.fields[f"{field}__low_confidence"] = True
