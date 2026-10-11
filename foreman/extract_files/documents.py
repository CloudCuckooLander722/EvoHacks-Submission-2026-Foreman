"""pdf and docx -> document sections (MINH_PLAN §A2).

Documents are split into sections of at most 20,000 characters so each one fits in a
single Jev state; the ids carry the page or heading, so a result points back to a place.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from foreman.contracts import ItemsFile
from foreman.core.state import ItemState
from foreman.extract_files.detect import ExtractError
from foreman.extract_files.text import MAX_SECTION_CHARS, split_long

log = logging.getLogger(__name__)


def read_pdf(path: Path) -> ItemsFile:
    """Consecutive pages packed into sections; one huge page is split at paragraph breaks."""
    import pymupdf

    try:
        doc = pymupdf.open(path)
    except Exception as e:
        raise ExtractError(f"{path.name} is not a readable pdf: {e}") from e
    with doc:
        if doc.needs_pass:
            raise ExtractError(f"{path.name} is password protected.")
        pages = [(n, page.get_text().strip()) for n, page in enumerate(doc, start=1)]
    stem = path.stem
    items: list[ItemState] = []
    cur: list[str] = []
    start = end = 0

    def flush() -> None:
        if cur:
            n = len(items) + 1
            text = "\n\n".join(cur)
            meta = {"page_start": start, "page_end": end}
            items.append(ItemState(id=f"{stem}-p{start}-s{n}", text=text, meta=meta))
        cur.clear()

    for n, text in pages:
        if not text:
            continue
        for piece in split_long(text, MAX_SECTION_CHARS):
            size = sum(len(c) + 2 for c in cur)
            if cur and size + len(piece) > MAX_SECTION_CHARS:
                flush()
            if not cur:
                start = n
            cur.append(piece)
            end = n
    flush()
    if not items:
        raise ExtractError(f"{path.name} has no text layer (a scanned pdf needs OCR first).")
    source = {"filename": path.name, "kind": "pdf", "pages": len(pages)}
    return ItemsFile(source=source, items=items)


def _table_lines(table: Any) -> list[str]:
    rows = [[c.text.strip() for c in row.cells] for row in table.rows]
    if len(rows) < 2:
        return [" | ".join(r) for r in rows]
    header = rows[0]
    return ["; ".join(f"{h}: {v}" for h, v in zip(header, r, strict=False) if v) for r in rows[1:]]


def read_docx(path: Path) -> ItemsFile:
    """A new section at each Heading paragraph, packed to 20k chars; tables as lines."""
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        d = docx.Document(str(path))
    except Exception as e:
        raise ExtractError(f"{path.name} is not a readable docx: {e}") from e

    blocks: list[tuple[str | None, list[str]]] = [(None, [])]  # (heading, lines)
    for el in d.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, d)
            text = p.text.strip()
            style = (p.style.name if p.style is not None else "") or ""
            if style.startswith("Heading") or style == "Title":
                blocks.append((text, [text] if text else []))
            elif text:
                blocks[-1][1].append(text)
        elif tag == "tbl":
            blocks[-1][1].extend(_table_lines(Table(el, d)))

    stem = path.stem
    items: list[ItemState] = []
    for heading, lines in blocks:
        body = "\n".join(lines).strip()
        if not body:
            continue
        for piece in split_long(body, MAX_SECTION_CHARS):
            n = len(items) + 1
            items.append(
                ItemState(id=f"{stem}-s{n}", text=piece, meta={"section": n, "heading": heading})
            )
    if not items:
        raise ExtractError(f"{path.name} has no text.")
    return ItemsFile(source={"filename": path.name, "kind": "docx"}, items=items)
