"""File type detection in code (MINH_PLAN §2 point 4).

The type is decided from the leading bytes first and the extension second, never by a
model: code knows for certain whether a file is a zip or a PDF, and a renamed file
(`report.txt` that is really a PDF) should still be read correctly.
"""

from __future__ import annotations

import csv
import logging
import os
import zipfile
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

Kind = Literal["csv", "xlsx", "pdf", "docx", "txt", "json"]

_TEXT_EXT: dict[str, Kind] = {".csv": "csv", ".tsv": "csv", ".json": "json", ".txt": "txt"}
SNIFF_BYTES = 64 * 1024


class ExtractError(ValueError):
    """A file could not be turned into items. The message is shown to the user as is."""


def check_path(path: str | os.PathLike[str] | None) -> Path:
    """The upload edge cases from the old `file_reader`, each with its own message."""
    if path is None or (isinstance(path, str) and not path.strip()):
        raise ExtractError("No file path was given.")
    p = Path(path)
    if not p.exists():
        raise ExtractError(f"File not found: {p}")
    if not p.is_file():
        raise ExtractError(f"Not a regular file: {p}")
    if not os.access(p, os.R_OK):
        raise ExtractError(f"No permission to read: {p}")
    if p.stat().st_size == 0:
        raise ExtractError(f"{p.name} is empty.")
    return p


def decode_text(raw: bytes, name: str) -> str:
    """utf-8 (with or without BOM), then cp1252; anything else is not a text file."""
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ExtractError(f"{name} is not a text file we can read (tried utf-8 and cp1252).")


def _zip_kind(path: Path) -> Kind | None:
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile:
        return None
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    return None


def _looks_like_csv(text: str) -> bool:
    lines = [ln for ln in text.splitlines()[:20] if ln.strip()]
    if len(lines) < 2:
        return False
    try:
        dialect = csv.Sniffer().sniff("\n".join(lines), delimiters=",;\t|")
    except csv.Error:
        return False
    counts = {ln.count(dialect.delimiter) for ln in lines[:10]}
    return min(counts) >= 1


def detect_kind(path: str | os.PathLike[str]) -> Kind:
    """csv, xlsx, pdf, docx, txt or json, from the leading bytes plus the extension."""
    p = Path(path)
    with p.open("rb") as f:
        head = f.read(SNIFF_BYTES)
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        kind = _zip_kind(p)
        if kind is None:
            raise ExtractError(f"{p.name} is a zip archive, but not an xlsx or docx file.")
        return kind
    if b"\x00" in head:
        raise ExtractError(
            f"{p.name} looks like a binary file; upload csv, xlsx, pdf, docx, txt or json."
        )
    text = decode_text(head, p.name).lstrip()
    ext = _TEXT_EXT.get(p.suffix.lower())
    if ext == "json" or (ext is None and text[:1] in "[{" and text):
        return "json"
    if ext == "csv":
        return "csv"
    if ext is None and _looks_like_csv(text):
        return "csv"
    return "txt"
