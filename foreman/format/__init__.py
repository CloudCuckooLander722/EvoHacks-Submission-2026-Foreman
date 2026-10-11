"""Minh: results.json -> report + exports.

`summarize()` writes the manager summary (one LLM call, numbers checked in code);
`export()` turns a ResultsFile into xlsx, pdf or docx bytes. The API picks both up.
"""

from __future__ import annotations

from typing import Literal

from foreman.contracts import ResultsFile
from foreman.format.summary import summarize, template_summary

__all__ = ["export", "summarize", "template_summary"]

Format = Literal["xlsx", "pdf", "docx"]


def export(results: ResultsFile, fmt: Format, summary: str | None = None) -> bytes:
    """The run as file bytes. `summary` defaults to `aggregates.summary` when present.

    Each format's library is imported only when that format is asked for, so a missing
    optional package breaks only its own download.
    """
    if summary is None:
        found = results.aggregates.get("summary")
        summary = found if isinstance(found, str) else None
    if fmt == "xlsx":
        from foreman.format.xlsx import to_xlsx

        return to_xlsx(results, summary)
    if fmt == "pdf":
        from foreman.format.pdf import to_pdf

        return to_pdf(results, summary)
    if fmt == "docx":
        from foreman.format.docx import to_docx

        return to_docx(results, summary)
    raise ValueError(f"unknown export format {fmt!r}; use xlsx, pdf or docx")
