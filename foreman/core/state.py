"""ItemState, RunState, Condition (the "node state").

Every item flows through the plan as an `ItemState`. Tools read its fields and write new
ones, so the plan is just a list of nodes reading and writing typed fields on shared state.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

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
    """A routing predicate on one field; lets the plan branch without a graph engine."""

    field: str
    op: Literal["eq", "ne", "in", "not_in", "truthy", "falsy", "gte", "lte"]
    value: Any = None

    def matches(self, item: ItemState) -> bool:
        """True if the item passes. A missing field never matches, except for `falsy`."""
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
