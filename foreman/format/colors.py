"""One palette for every export, mirrored in web/src/index.css.

Who decided a value is the product's core story, so Jev / LLM / code look the same in the
xlsx, the pdf, the docx and the site: a reader learns the colours once.
"""

from __future__ import annotations

# Cell fills (light) and strong marks (bars, legend dots, text).
FILL = {"jev": "E5F1FF", "llm": "FFF1D6", "code": "F2F2F7"}
MARK = {"jev": "0A84FF", "llm": "C77700", "code": "8E8E93"}
LABEL = {"jev": "Jev", "llm": "LLM", "code": "Code"}
LOW_CONFIDENCE_TEXT = "D70015"
ACCENT = "0A84FF"
TEXT = "1D1D1F"
MUTED = "6E6E73"
HEADER_FILL = "F5F5F7"


def hex_rgb(h: str) -> tuple[int, int, int]:
    """'0A84FF' -> (10, 132, 255), for fpdf."""
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def css(h: str) -> str:
    """'0A84FF' -> '#0A84FF', for matplotlib."""
    return f"#{h}"
