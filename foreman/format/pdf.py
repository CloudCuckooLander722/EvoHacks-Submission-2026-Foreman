"""results.json -> a pdf report (MINH_PLAN §B2).

Order: the summary, the Jev / LLM / code split, the aggregate charts, then the top 50
rows with cells coloured by who decided them, the same colours as the xlsx and the site.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from fpdf import FPDF

from foreman.contracts import ResultsFile
from foreman.format.charts import find_charts, render_png
from foreman.format.colors import FILL, LABEL, LOW_CONFIDENCE_TEXT, MUTED, TEXT, hex_rgb
from foreman.format.table import (
    cell_text,
    columns,
    decided_by,
    is_low,
    ordered_items,
    output_fields,
)

log = logging.getLogger(__name__)

TOP_ROWS = 50
MAX_COLS = 5  # id + text + up to this many decided fields fit on a landscape page


def _font_dir() -> Path:
    import matplotlib

    return Path(matplotlib.get_data_path()) / "fonts" / "ttf"


class _Report(FPDF):
    def __init__(self) -> None:
        super().__init__(orientation="L", unit="mm", format="A4")
        fonts = _font_dir()
        # DejaVu covers far more of Unicode than the core fonts (reviews have accents, emoji).
        self.add_font("DejaVu", "", str(fonts / "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", str(fonts / "DejaVuSans-Bold.ttf"))
        self.set_auto_page_break(True, margin=12)
        self.set_margins(14, 12, 14)

    def footer(self) -> None:
        self.set_y(-10)
        self.set_font("DejaVu", "", 8)
        self.set_text_color(*hex_rgb(MUTED))
        self.cell(0, 6, f"Foreman · page {self.page_no()}", align="R")


def _fit(pdf: FPDF, s: object, width: float) -> str:
    """Trim `s` so it fits a cell `width` mm wide in the current font."""
    text = _safe(" ".join(str(s).split()))
    if pdf.get_string_width(text) <= width - 1.5:
        return text
    while text and pdf.get_string_width(text + "…") > width - 1.5:
        text = text[: max(0, int(len(text) * 0.9) - 1)] if len(text) > 40 else text[:-1]
    return text + "…"


def _safe(s: str) -> str:
    """Drop characters outside the Basic Multilingual Plane (emoji) the font cannot draw."""
    return "".join(ch for ch in s if ord(ch) <= 0xFFFF)


def to_pdf(results: ResultsFile, summary: str | None = None) -> bytes:
    """The report as pdf bytes."""
    pdf = _Report()
    pdf.add_page()
    pdf.set_text_color(*hex_rgb(TEXT))
    pdf.set_font("DejaVu", "B", 20)
    pdf.cell(0, 10, "Foreman run report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 10)
    pdf.set_text_color(*hex_rgb(MUTED))
    pdf.multi_cell(0, 5, _safe(f"Job: {results.job}"), new_x="LMARGIN", new_y="NEXT")
    st = results.stats
    pdf.cell(
        0, 6,
        f"Run {results.run_id} · threshold {results.threshold} · {st.decisions_total:,} decisions"
        f" · ${st.jev_cost_usd + st.llm_cost_usd:.4f} · {st.wall_time_s:.1f} s",
        new_x="LMARGIN", new_y="NEXT",
    )  # fmt: skip
    pdf.ln(3)
    if summary:
        pdf.set_text_color(*hex_rgb(TEXT))
        pdf.set_font("DejaVu", "B", 13)
        pdf.cell(0, 8, "Summary", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 11)
        pdf.multi_cell(0, 6, _safe(summary), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    width_in = 7.0
    for chart in find_charts(results):
        h_mm = max(1.4, 0.38 * len(chart.bars) + 0.8) * 25.4
        if pdf.get_y() + h_mm > pdf.h - pdf.b_margin:
            pdf.add_page()
        y = pdf.get_y()
        pdf.image(io.BytesIO(render_png(chart, width_in)), x=pdf.l_margin, y=y, w=width_in * 25.4)
        pdf.set_y(y + h_mm + 2)
    _table(pdf, results)
    return bytes(pdf.output())


def _table(pdf: _Report, results: ResultsFile) -> None:
    decided = output_fields(results)
    cols = [c for c in columns(results) if c in decided][:MAX_COLS]
    pdf.add_page()
    pdf.set_text_color(*hex_rgb(TEXT))
    pdf.set_font("DejaVu", "B", 13)
    pdf.cell(0, 8, f"Top {TOP_ROWS} rows", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 8)
    pdf.set_text_color(*hex_rgb(MUTED))
    legend = "  ".join(f"■ {LABEL[k]}" for k in ("jev", "llm", "code"))
    pdf.cell(0, 5, f"Cell colour = who decided: {legend}  · red text = low confidence",
             new_x="LMARGIN", new_y="NEXT")  # fmt: skip
    rest = pdf.epw - 22
    widths = [22, rest * 0.55, *([rest * 0.45 / max(1, len(cols))] * len(cols))]
    pdf.set_font("DejaVu", "B", 8)
    pdf.set_text_color(*hex_rgb(TEXT))
    pdf.set_fill_color(245, 245, 247)
    for w, name in zip(widths, ["id", "text", *cols], strict=True):
        pdf.cell(w, 6, _fit(pdf, name, w), border=0, fill=True)
    pdf.ln()
    pdf.set_font("DejaVu", "", 8)
    for item in ordered_items(results)[:TOP_ROWS]:
        pdf.set_text_color(*hex_rgb(TEXT))
        pdf.cell(widths[0], 5.5, _fit(pdf, item.id, widths[0]))
        pdf.cell(widths[1], 5.5, _fit(pdf, item.text, widths[1]))
        for w, field in zip(widths[2:], cols, strict=True):
            by = decided_by(item, field)
            fill = by is not None
            if by:
                pdf.set_fill_color(*hex_rgb(FILL[by]))
            color = LOW_CONFIDENCE_TEXT if is_low(item, field) else TEXT
            pdf.set_text_color(*hex_rgb(color))
            v = cell_text(item.fields.get(field))
            pdf.cell(w, 5.5, _fit(pdf, "" if v is None else v, w), fill=fill)
        pdf.ln()
