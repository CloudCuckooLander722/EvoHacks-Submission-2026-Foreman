"""Score: rate items on several weighted dimensions, combined in code.

Jev is good at one gut-check judgment at a time and bad at weighing many factors at
once. So instead of asking "how urgent is this?", Score asks one Jev Score question per
dimension (severity, reach, clarity...) and combines the answers with weights that live
in code. All dimensions for an item go in one Jev call, which Jev answers in parallel.

Jev's answer is the *expected* level (a probability-weighted index), so dimension and
composite scores are good for ordering items and applying thresholds, not as exact
measurements.
"""

from __future__ import annotations

import statistics
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

from foreman.core.questions import MAX_LEVELS, MIN_LEVELS, Answer, Question, Score
from foreman.core.state import ItemState, RunState, set_output
from foreman.tools.base import PerItemTool, ToolParams, ToolReport, select

MAX_DIMENSIONS = 8
SLUG = r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$"


class Dimension(BaseModel):
    """One judgment Jev makes about each item; `levels` run from lowest to highest."""

    name: str = Field(pattern=SLUG, max_length=40, description="Slug, unique within the node.")
    instructions: str = Field(
        min_length=1,
        description="One judgment about the item, e.g. "
        "'How severe is the problem described in the text for the user?'",
    )
    levels: list[str] = Field(
        min_length=MIN_LEVELS,
        max_length=MAX_LEVELS,
        description="2-10 concrete descriptions, ordered from lowest to highest.",
    )
    weight: float = Field(default=1.0, gt=0, description="Relative weight in the composite.")

    @field_validator("instructions")
    @classmethod
    def _instructions_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("instructions are blank")
        return v

    @field_validator("levels")
    @classmethod
    def _levels_not_blank(cls, v: list[str]) -> list[str]:
        if any(not lvl.strip() for lvl in v):
            raise ValueError("levels must not be blank")
        return v


class ScoreParams(ToolParams):
    """Dimensions to rate, the composite's scale and where to write it."""

    dimensions: list[Dimension] = Field(min_length=1, max_length=MAX_DIMENSIONS)
    scale: float = Field(default=100.0, gt=0, description="Scores run from 0 to `scale`.")
    output_field: str = "score"

    @field_validator("dimensions")
    @classmethod
    def _unique_names(cls, v: list[Dimension]) -> list[Dimension]:
        names = [d.name for d in v]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"dimension names must be unique; repeated: {', '.join(dupes)}")
        return v


def _qid(dim: Dimension) -> str:
    return f"dim_{dim.name}"


def normalize(value: float, n_levels: int) -> float:
    """Expected level index -> 0..1, clamped so a stray answer can't leave the scale."""
    return min(1.0, max(0.0, float(value) / (n_levels - 1)))


class ScoreTool(PerItemTool):
    """Composite scorer: one Jev Score per dimension, weighted mean computed in code."""

    name: ClassVar[str] = "score"
    description: ClassVar[str] = (
        "Rates each item on 1-8 separate dimensions (e.g. severity, how many users are "
        "affected, clarity) and combines them with weights into one composite score from 0 "
        "to `scale`, also writing each dimension's score. Use it to prioritize or rate items "
        "when the judgment depends on several factors; each dimension is a separate gut-check, "
        "so give each one a single, specific judgment. Write `levels` as 2-10 concrete, "
        "ordered descriptions from lowest to highest (e.g. 'cosmetic, no impact' ... "
        "'data loss or app unusable'), not bare labels like 'low'/'high'."
    )
    Params: ClassVar[type[ToolParams]] = ScoreParams
    uses: ClassVar[Literal["jev", "jev+llm", "code"]] = "jev"

    def questions(self, item: ItemState, params: ScoreParams) -> dict[str, Question]:
        """One Score per dimension; the Decider sends them all in one Jev call."""
        return {
            _qid(d): Score(instructions=d.instructions, levels=list(d.levels))
            for d in params.dimensions
        }

    def apply(self, item: ItemState, answers: dict[str, Answer], params: ScoreParams) -> None:
        """Write each dimension's scaled score, then the weighted composite with provenance."""
        out = params.output_field
        weighted = total_weight = 0.0
        used: list[Answer] = []
        for d in params.dimensions:
            a = answers[_qid(d)]
            norm = normalize(float(a.value), len(d.levels))
            item.fields[f"{out}_{d.name}"] = round(norm * params.scale, 1)
            item.fields[f"{out}_{d.name}__confidence"] = a.confidence
            weighted += d.weight * norm
            total_weight += d.weight
            used.append(a)
        set_output(
            item,
            out,
            round(weighted / total_weight * params.scale, 1),
            confidence=min(a.confidence for a in used),
            by="llm" if any(a.decided_by == "llm" for a in used) else "jev",
            low_confidence=any(a.low_confidence for a in used),
        )

    def after_all(self, run: RunState, params: ScoreParams, report: ToolReport) -> None:
        """Summary statistics over the items this node scored (skipped items excluded)."""
        out = params.output_field
        scored = [
            i
            for i in select(run, params.where)
            if f"{out}__error" not in i.fields and isinstance(i.fields.get(out), (int, float))
        ]
        run.aggregates[f"{out}_summary"] = summarize(scored, params)
        report.code_ops += 1


def summarize(items: list[ItemState], params: ScoreParams) -> dict[str, Any]:
    """Mean / median / min / max of the composite and the mean of each dimension."""
    out = params.output_field
    composites = [float(i.fields[out]) for i in items]
    if not composites:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "min": None,
            "max": None,
            "per_dimension_mean": {d.name: None for d in params.dimensions},
        }
    return {
        "n": len(composites),
        "mean": round(statistics.fmean(composites), 1),
        "median": round(statistics.median(composites), 1),
        "min": min(composites),
        "max": max(composites),
        "per_dimension_mean": {
            d.name: round(statistics.fmean(float(i.fields[f"{out}_{d.name}"]) for i in items), 1)
            for d in params.dimensions
        },
    }
