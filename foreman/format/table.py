"""The results table shared by every export: which columns, which order, who decided.

Tools write `<field>__by`, `<field>__confidence` and `<field>__low_confidence` next to
each output field. Exports show the field and use those three to colour its cell, so the
bookkeeping columns never clutter the table.
"""

from __future__ import annotations

import json
from typing import Any

from foreman.contracts import ResultsFile
from foreman.core.state import ItemState

SUFFIX = "__"


def is_meta_field(name: str) -> bool:
    """`type__by`, `type__probs`... are bookkeeping, not a column of their own."""
    return SUFFIX in name


def columns(results: ResultsFile) -> list[str]:
    """Every visible field, in first-seen order: input columns first, outputs after."""
    seen: dict[str, None] = {}
    for item in results.items:
        for k in item.fields:
            if not is_meta_field(k):
                seen.setdefault(k, None)
    return list(seen)


def output_fields(results: ResultsFile) -> set[str]:
    """Fields some tool decided (they have a `__by` companion)."""
    out: set[str] = set()
    for item in results.items:
        out |= {k[: -len("__by")] for k in item.fields if k.endswith("__by")}
    return out


def ordered_items(results: ResultsFile) -> list[ItemState]:
    """Ranked items first, in rank order, when the run ranked anything; else as given."""
    order = results.aggregates.get("rank_order")
    if not isinstance(order, list) or not order:
        return list(results.items)
    pos = {str(i): n for n, i in enumerate(order)}
    ranked = sorted((i for i in results.items if i.id in pos), key=lambda i: pos[i.id])
    return ranked + [i for i in results.items if i.id not in pos]


def decided_by(item: ItemState, field: str) -> str | None:
    """'jev' | 'llm' | 'code' for a decided field, else None."""
    by = item.fields.get(f"{field}__by")
    return by if by in ("jev", "llm", "code") else None


def is_low(item: ItemState, field: str) -> bool:
    return bool(item.fields.get(f"{field}__low_confidence"))


def cell_text(v: Any) -> Any:
    """Numbers and text stay as they are; anything nested becomes compact JSON."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return json.dumps(v, ensure_ascii=False, default=str)
