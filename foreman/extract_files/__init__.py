"""Minh: file -> items.json (docs/CONTRACTS.md).

`extract(path)` is pure code with no keys needed: the type is detected from the bytes,
and each format has its own reader. The API calls it on every upload.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from foreman.contracts import ItemsFile
from foreman.extract_files.detect import ExtractError, Kind, check_path, detect_kind

log = logging.getLogger(__name__)

__all__ = ["ExtractError", "detect_kind", "extract"]


def _readers() -> dict[Kind, Callable[[Path], ItemsFile]]:
    from foreman.extract_files.documents import read_docx, read_pdf
    from foreman.extract_files.tables import read_csv, read_xlsx
    from foreman.extract_files.text import read_json, read_txt

    return {
        "csv": read_csv,
        "xlsx": read_xlsx,
        "json": read_json,
        "txt": read_txt,
        "pdf": read_pdf,
        "docx": read_docx,
    }


def extract(path: str | os.PathLike[str]) -> ItemsFile:
    """A csv, xlsx, pdf, docx, txt or json file -> a validated ItemsFile.

    Raises ExtractError with a message meant for the user (missing file, unreadable
    format, no items, duplicate ids...).
    """
    p = check_path(path)
    kind = detect_kind(p)
    try:
        out = _readers()[kind](p)
    except ExtractError:
        raise
    except ImportError as e:
        raise ExtractError(f"Reading {kind} files needs an extra package: {e.name}") from e
    except Exception as e:  # a library choking on a malformed file
        log.exception("extracting %s as %s failed", p.name, kind)
        raise ExtractError(f"Could not read {p.name} as {kind}: {e}") from e
    if not out.items:
        raise ExtractError(f"{p.name} has no items.")
    ids = [i.id for i in out.items]
    if len(set(ids)) != len(ids):
        raise ExtractError(f"{p.name}: item ids are not unique.")
    out.source.setdefault("kind", kind)
    log.info("extracted %d items from %s (%s)", len(out.items), p.name, kind)
    return ItemsFile.model_validate(out.model_dump())
