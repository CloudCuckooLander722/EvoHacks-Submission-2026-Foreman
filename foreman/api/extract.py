"""Uploaded file -> items.json, via Minh's `extract()` when it exists, else a small fallback.

The fallback keeps the API usable before `foreman.extract_files` lands: items.json as is,
csv through the stdlib (longest text column becomes `text`, every column goes into
`fields`), and txt as one item per line. It follows the same id and meta conventions as
docs/CONTRACTS.md (`row-<n>` with the spreadsheet row number, `line-<n>`).
"""

from __future__ import annotations

import csv
import importlib
import io
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from foreman.contracts import ItemsFile
from foreman.core.state import ItemState

log = logging.getLogger(__name__)

_INT = re.compile(r"[-+]?\d{1,15}")
_FLOAT = re.compile(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?")


class ExtractFailed(ValueError):
    """The file could not be turned into items; the message is shown to the user."""


def load_optional(module: str, name: str) -> Callable[..., Any] | None:
    """`module.name` if that module exists and defines it, else None.

    Lets the API pick up Minh's `extract`, `summarize` and `export` as soon as they are
    merged, without a hard import that breaks the server before then.
    """
    try:
        mod = importlib.import_module(module)
    except ImportError:
        return None
    fn = getattr(mod, name, None)
    return fn if callable(fn) else None


def to_items(path: Path) -> ItemsFile:
    """Extract `path` with `foreman.extract_files.extract`, or the built-in fallback."""
    extract = load_optional("foreman.extract_files", "extract")
    if extract is not None:
        log.info("extracting %s with foreman.extract_files.extract", path.name)
        try:
            out = extract(path)
        except Exception as e:  # ExtractError and anything else: one readable message
            raise ExtractFailed(str(e) or type(e).__name__) from e
        return out if isinstance(out, ItemsFile) else ItemsFile.model_validate(out)
    log.info("extracting %s with the built-in fallback reader", path.name)
    return fallback_extract(path)


def fallback_extract(path: Path) -> ItemsFile:
    """items.json, .csv or .txt -> ItemsFile. Raises ExtractFailed with a readable reason."""
    readers = {".json": _read_json, ".csv": _read_csv, ".txt": _read_txt}
    reader = readers.get(path.suffix.lower())
    if reader is None:
        raise ExtractFailed(
            f"{path.suffix or 'files without an extension'} is not supported yet; "
            "upload items.json, .csv or .txt"
        )
    items = reader(path)
    if not items.items:
        raise ExtractFailed(f"{path.name} has no items")
    return items


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ExtractFailed(f"{path.name} is not utf-8 or cp1252 text")


def _read_json(path: Path) -> ItemsFile:
    try:
        data = json.loads(_read_text(path))
    except json.JSONDecodeError as e:
        raise ExtractFailed(f"{path.name} is not valid JSON: {e}") from e
    if not isinstance(data, dict) or "items" not in data:
        raise ExtractFailed(f'{path.name} is not an items.json (needs {{"items": [...]}})')
    try:
        return ItemsFile.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(x) for x in first["loc"])
        raise ExtractFailed(f"{path.name} is not a valid items.json: {loc}: {first['msg']}") from e


def _typed(value: str) -> int | float | str:
    """Numbers become numbers (so calculate can use them); everything else stays text."""
    v = value.strip()
    if _INT.fullmatch(v):
        return int(v)
    if _FLOAT.fullmatch(v):
        return float(v)
    return v


def _text_column(header: list[str], rows: list[dict[str, str]]) -> str | None:
    """The column whose non-empty values are longest on average (None if none are text)."""
    best, best_len = None, 0.0
    for col in header:
        values = [r[col] for r in rows if (r.get(col) or "").strip()]
        if not values or all(isinstance(_typed(v), (int, float)) for v in values):
            continue
        avg = sum(len(v) for v in values) / len(values)
        if avg > best_len:
            best, best_len = col, avg
    return best


def _read_csv(path: Path) -> ItemsFile:
    reader = csv.DictReader(io.StringIO(_read_text(path), newline=""))
    header = [h for h in (reader.fieldnames or []) if h]
    if not header:
        raise ExtractFailed(f"{path.name} has no header row")
    rows = list(reader)
    text_col = _text_column(header, rows)
    items: list[ItemState] = []
    for n, row in enumerate(rows, start=2):  # row 1 is the header, as in a spreadsheet
        fields = {c: _typed(v) for c in header if (v := row.get(c)) is not None and v.strip()}
        if not fields:
            continue
        text = (row.get(text_col) or "").strip() if text_col else ""
        if not text:
            text = "; ".join(f"{k}: {v}" for k, v in fields.items())
        items.append(ItemState(id=f"row-{n}", text=text, fields=fields, meta={"row": n}))
    source = {"filename": path.name, "kind": "csv", "rows": len(rows), "text_column": text_col}
    return ItemsFile(source=source, items=items)


def _read_txt(path: Path) -> ItemsFile:
    items = [
        ItemState(id=f"line-{n}", text=line.strip(), meta={"line": n})
        for n, line in enumerate(_read_text(path).splitlines(), start=1)
        if line.strip()
    ]
    return ItemsFile(source={"filename": path.name, "kind": "txt"}, items=items)
