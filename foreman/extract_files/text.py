"""txt and json -> items (MINH_PLAN §A1), plus section packing shared with pdf/docx.

Long text is cut into sections of at most `MAX_SECTION_CHARS`, because Jev's state limit
is about 32k tokens and a question about one section must fit in one call.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from foreman.contracts import ItemsFile
from foreman.core.state import ItemState
from foreman.extract_files.detect import ExtractError, decode_text
from foreman.extract_files.tables import clean_value

log = logging.getLogger(__name__)

MAX_SECTION_CHARS = 20_000
TEXT_KEYS = ("text", "body", "review", "content", "message")
_BLANK_LINES = re.compile(r"\n\s*\n")


def split_long(text: str, limit: int = MAX_SECTION_CHARS) -> list[str]:
    """One over-long paragraph -> pieces under `limit`, cut at line, sentence or space."""
    out: list[str] = []
    rest = text.strip()
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind("\n"), window.rfind(". ") + 1, 0)
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut < limit // 2:
            cut = limit
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return out


def pack(paragraphs: Iterable[str], limit: int = MAX_SECTION_CHARS) -> list[str]:
    """Greedily join paragraphs into sections of at most `limit` characters."""
    sections: list[str] = []
    cur = ""
    for para in paragraphs:
        for piece in split_long(para, limit):
            if cur and len(cur) + 2 + len(piece) > limit:
                sections.append(cur)
                cur = piece
            else:
                cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        sections.append(cur)
    return sections


def _is_list(lines: list[str]) -> bool:
    """At least 5 non-empty lines and most of them under 300 characters."""
    nonempty = [ln for ln in lines if ln.strip()]
    if len(nonempty) < 5:
        return False
    short = sum(1 for ln in nonempty if len(ln) < 300)
    return short / len(nonempty) >= 0.8


def read_txt(path: Path) -> ItemsFile:
    """A list-like txt -> one item per line; prose -> packed paragraph sections."""
    text = decode_text(path.read_bytes(), path.name)
    if not text.strip():
        raise ExtractError(f"{path.name} contains only whitespace.")
    lines = text.splitlines()
    source: dict[str, Any] = {"filename": path.name, "kind": "txt"}
    if _is_list(lines):
        items = [
            ItemState(id=f"line-{n}", text=ln.strip(), meta={"line": n})
            for n, ln in enumerate(lines, start=1)
            if ln.strip()
        ]
        return ItemsFile(source={**source, "mode": "lines"}, items=items)
    paragraphs = [p.strip() for p in _BLANK_LINES.split(text) if p.strip()]
    stem = path.stem
    items = [
        ItemState(id=f"{stem}-s{n}", text=s, meta={"section": n})
        for n, s in enumerate(pack(paragraphs), start=1)
    ]
    return ItemsFile(source={**source, "mode": "sections"}, items=items)


def _text_of(obj: dict[str, Any]) -> str:
    for key in TEXT_KEYS:
        v = obj.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    strings = [v for v in obj.values() if isinstance(v, str) and v.strip()]
    if strings:
        return max(strings, key=len).strip()
    return "; ".join(f"{k}: {v}" for k, v in obj.items() if v is not None)


def _json_safe(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_json_safe(x) for x in v]
    if isinstance(v, float):
        return clean_value(v)
    return v


def read_json(path: Path) -> ItemsFile:
    """An items.json passes through validated; a list of objects becomes one item each."""
    try:
        data = json.loads(decode_text(path.read_bytes(), path.name))
    except json.JSONDecodeError as e:
        raise ExtractError(f"{path.name} is not valid JSON: {e}") from e
    if isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        if all(isinstance(i, dict) and "id" in i and "text" in i for i in data["items"]):
            try:
                return ItemsFile.model_validate(data)
            except ValidationError as e:
                first = e.errors()[0]
                loc = ".".join(str(x) for x in first["loc"])
                raise ExtractError(
                    f"{path.name} is not a valid items.json: {loc}: {first['msg']}"
                ) from e
        data = data["items"]
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if len(lists) != 1:
            raise ExtractError(f"{path.name} is a JSON object; expected a list of records.")
        data = lists[0]
    if not isinstance(data, list):
        raise ExtractError(f"{path.name} must hold a list of objects.")
    objs = [o for o in data if isinstance(o, dict) and o]
    if len(objs) < len([o for o in data if o is not None]):
        log.warning("%s: %d non-object entries skipped", path.name, len(data) - len(objs))
    ids = [o.get("id") for o in objs]
    use_ids = all(isinstance(i, (str, int)) and str(i).strip() for i in ids) and len(
        {str(i) for i in ids}
    ) == len(ids)
    items = [
        ItemState(
            id=str(o["id"]) if use_ids else f"item-{n}",
            text=_text_of(o),
            fields=_json_safe(o),
            meta={"index": n},
        )
        for n, o in enumerate(objs, start=1)
    ]
    return ItemsFile(source={"filename": path.name, "kind": "json", "rows": len(objs)}, items=items)
