"""Find chartable aggregates by their shape, not their name (MINH_PLAN §B1).

Aggregate names follow each tool's output field (`type_counts`, `category_counts`,
`theme_groups`...), so matching on names would miss new tools and new plans. A
`dict[str, number]` or a list of `{label, count}` records is a bar chart wherever it
came from. web/src/sections/Results/charts.ts applies the same rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from foreman.contracts import ResultsFile

SKIP = {"escalation_curve", "summary"}
MAX_BARS = 15
LABEL_KEYS = ("theme", "name", "label", "category", "group", "key")


@dataclass
class Chart:
    """One bar chart: a title and (label, value) bars, largest first."""

    key: str
    title: str
    bars: list[tuple[str, float]]
    split: bool = False  # the Jev / LLM / code split, drawn in the decider colours


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def title_of(key: str) -> str:
    """`theme_counts` -> `Theme counts`."""
    words = key.replace("__", " ").replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def _bars_from_records(rows: list[Any]) -> list[tuple[str, float]] | None:
    if not rows or not all(isinstance(r, dict) for r in rows):
        return None
    first = rows[0]
    label = next((k for k in LABEL_KEYS if isinstance(first.get(k), str)), None)
    if label is None or not _is_num(first.get("count")):
        return None
    if not all(isinstance(r.get(label), str) and _is_num(r.get("count")) for r in rows):
        return None
    return [(r[label], float(r["count"])) for r in rows]


def bars_of(value: Any) -> list[tuple[str, float]] | None:
    """The bars for one aggregate, or None if its shape is not chartable."""
    if isinstance(value, dict) and len(value) >= 2 and all(_is_num(v) for v in value.values()):
        return [(str(k), float(v)) for k, v in value.items()]
    if isinstance(value, list):
        return _bars_from_records(value)
    return None


def find_charts(results: ResultsFile) -> list[Chart]:
    """The Jev / LLM / code split first, then every chartable aggregate (duplicates dropped)."""
    by = results.stats.by
    charts = [
        Chart(
            key="decided_by",
            title="Decisions by Jev / LLM / code",
            bars=[(k, float(by.get(k, 0))) for k in ("jev", "llm", "code")],
            split=True,
        )
    ]
    seen: set[tuple[tuple[str, float], ...]] = set()
    for key, value in results.aggregates.items():
        if key in SKIP:
            continue
        bars = bars_of(value)
        if not bars:
            continue
        bars = sorted(bars, key=lambda b: -b[1])[:MAX_BARS]
        sig = tuple(sorted(bars))
        if sig in seen:
            continue
        seen.add(sig)
        charts.append(Chart(key=key, title=title_of(key), bars=bars))
    return charts


def render_png(chart: Chart, width_in: float = 7.0) -> bytes:
    """A horizontal bar chart as PNG (pdf and docx exports), in the shared palette."""
    import io

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from foreman.format.colors import ACCENT, LABEL, MARK, MUTED, TEXT, css

    labels = [LABEL.get(lbl, lbl) if chart.split else lbl for lbl, _ in chart.bars]
    values = [v for _, v in chart.bars]
    colors = [css(MARK[lbl]) if chart.split else css(ACCENT) for lbl, _ in chart.bars]
    height = max(1.4, 0.38 * len(labels) + 0.8)
    fig, ax = plt.subplots(figsize=(width_in, height), dpi=150)
    y = list(range(len(labels)))[::-1]
    ax.barh(y, values, color=colors, height=0.62)
    ax.set_yticks(y, [lbl if len(lbl) <= 42 else lbl[:40] + "…" for lbl in labels])
    ax.set_title(chart.title, loc="left", fontsize=11, color=css(TEXT), fontweight="bold")
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(css(MUTED))
    ax.tick_params(colors=css(TEXT), labelsize=9, length=0)
    top = max(values) if values else 1
    for yi, v in zip(y, values, strict=True):
        ax.text(v + top * 0.01, yi, f"{v:,.0f}", va="center", fontsize=8, color=css(MUTED))
    ax.set_xlim(0, top * 1.12 if top else 1)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
