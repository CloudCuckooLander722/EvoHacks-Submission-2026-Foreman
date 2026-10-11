"""Rank: sort items by one fuzzy quality (urgency, fit, quality...).

Jev cannot sort a list, but it can rate one item and it can compare two. So Rank:

1. Scores every item with one Jev Score question, or reuses a numeric field an earlier
   node already computed (usually Score's output), which costs no Jev calls at all.
2. Sorts in code.
3. Re-checks near-ties with pairwise Jev Choices. Each pair is asked in both option
   orders in the same call, because Jev leans slightly toward the first option; the pair
   is swapped only when both orders agree, so the bias can never cause a swap by itself.

The tie-break is a single top-to-bottom pass over adjacent pairs, not a full sort: it
fixes the close calls a 5-level score cannot separate, at a bounded cost.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import Field, field_validator

from foreman.core.decide import DecisionError
from foreman.core.jev import StateTooLarge
from foreman.core.ledger import Decision
from foreman.core.questions import MAX_LEVELS, MIN_LEVELS, Answer, Choice, Question, Score
from foreman.core.state import ItemState, RunState, get_input, set_output
from foreman.tools.base import PerItemTool, ToolParams, ToolReport, select

if TYPE_CHECKING:
    from foreman.core.context import RunContext

MAX_PAIR_CHARS = 2000  # per item in a tie-break state; two items must share Jev's window
SCORE_QID = "score"


def default_levels(criterion: str) -> list[str]:
    """Five levels, low -> high, each naming the criterion so Jev rates the right thing."""
    return [
        f"Very low {criterion}",
        f"Low {criterion}",
        f"Moderate {criterion}",
        f"High {criterion}",
        f"Very high {criterion}",
    ]


class RankParams(ToolParams):
    """What to rank by, where the scores come from and how hard to re-check near-ties."""

    criterion: str = Field(
        description="The quality to rank by, as a noun phrase about something the text "
        "states, e.g. 'urgency of fixing this issue'."
    )
    levels: list[str] | None = Field(
        default=None,
        min_length=MIN_LEVELS,
        max_length=MAX_LEVELS,
        description="2-10 levels, low -> high, one condition each. "
        "Default: 'Very low <criterion>' ... 'Very high <criterion>'.",
    )
    score_field: str | None = Field(
        default=None,
        description="Read this numeric field (e.g. a Score node's output) instead of asking "
        "Jev. Non-numeric values sort last.",
    )
    descending: bool = Field(default=True, description="Rank 1 = most of the criterion.")
    top_k: int | None = Field(default=None, ge=1, description="Only tie-break the top k.")
    tie_break: bool = True
    tie_margin: float = Field(
        default=0.05, ge=0.0, le=1.0, description="Normalized 0-1 score gap treated as a tie."
    )
    max_tiebreaks: int = Field(default=200, ge=0)
    output_field: str = "rank"

    @field_validator("criterion")
    @classmethod
    def _criterion_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("criterion must not be empty")
        return v

    @field_validator("levels")
    @classmethod
    def _levels_not_blank(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any(not lvl.strip() for lvl in v):
            raise ValueError("levels must not be blank")
        return v

    @property
    def score_levels(self) -> list[str]:
        return list(self.levels) if self.levels is not None else default_levels(self.criterion)


def parse_number(v: Any) -> float | None:
    """A finite float, or None. Booleans are not scores, and NaN would break the sort."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def min_max(values: dict[str, float]) -> dict[str, float]:
    """Rescale to 0..1 so `tie_margin` means the same whatever scale the field used.

    When every value is equal there is no spread to scale; all items are equally tied.
    """
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {k: 0.0 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def pair_questions(criterion: str) -> dict[str, Question]:
    """The same comparison with item_a listed first (`ab`) and item_b first (`ba`).

    No LLM fallback: a near-tie Jev is unsure about is best left in score order.
    """
    a = ("item_a", f"item_a shows more {criterion}")
    b = ("item_b", f"item_b shows more {criterion}")
    eq = ("equal", "They are about the same")
    instructions = f"Which item shows more {criterion}?"
    return {
        "ab": Choice(instructions, dict([a, b, eq]), fallback=False),
        "ba": Choice(instructions, dict([b, a, eq]), fallback=False),
    }


class RankTool(PerItemTool):
    """Score (or reuse a score), sort in code, then re-check near-ties pairwise."""

    name: ClassVar[str] = "rank"
    description: ClassVar[str] = (
        "Sorts items by one fuzzy quality (e.g. 'urgency of fixing this issue for users') and "
        "writes a 1-based rank. If a Score node already produced a number, pass that field as "
        "`score_field` so no new judgments are needed. Otherwise phrase `criterion` as "
        "something the text itself states or shows, and give each of `levels` a single "
        "condition (not 'X or Y'). Use `top_k` when only the top of the list matters."
    )
    Params: ClassVar[type[ToolParams]] = RankParams
    uses: ClassVar[Literal["jev", "jev+llm", "code"]] = "jev"

    def questions(self, item: ItemState, params: RankParams) -> dict[str, Question]:
        return {
            SCORE_QID: Score(
                instructions=f"How much {params.criterion} does the text show?",
                levels=params.score_levels,
            )
        }

    def apply(self, item: ItemState, answers: dict[str, Answer], params: RankParams) -> None:
        """Record the normalized score and its provenance; the rank is filled in after sorting."""
        a = answers[SCORE_QID]
        n = len(params.score_levels)
        norm = min(1.0, max(0.0, float(a.value) / (n - 1)))
        out = params.output_field
        item.fields[f"{out}_score"] = round(norm, 3)
        set_output(
            item,
            out,
            None,
            confidence=a.confidence,
            by=a.decided_by,
            low_confidence=a.low_confidence,
        )

    async def run(self, run: RunState, params: ToolParams, ctx: RunContext) -> ToolReport:
        """Score (Jev or `score_field`), sort, tie-break, then write ranks and the order."""
        assert isinstance(params, RankParams)
        t0 = time.perf_counter()
        out = params.output_field
        if params.score_field is None:
            report = await super().run(run, params, ctx)
            ranked = [i for i in select(run, params.where) if f"{out}__error" not in i.fields]
        else:
            report = self._code_scores(run, params)
            ranked = select(run, params.where)

        order = self._sort(ranked, params)
        if params.tie_break:
            await self._tie_break(order, params, ctx, report)

        for pos, item in enumerate(order, start=1):
            if params.score_field is None:
                item.fields[out] = pos
            else:
                set_output(item, out, pos, confidence=1.0, by="code")
        run.aggregates[f"{out}_order"] = [i.id for i in order]
        report.code_ops += 1
        ctx.ledger.add(
            Decision(
                run_id=ctx.run_id,
                item_id="__run__",
                tool=self.name,
                question_id=f"{out}_order",
                qtype="sort",
                value=run.aggregates[f"{out}_order"],
                confidence=1.0,
                probabilities=None,
                decided_by="code",
                kind="compute",
            )
        )
        report.items_out = len(run.items)
        report.duration_ms = int((time.perf_counter() - t0) * 1000)
        return report

    def _code_scores(self, run: RunState, params: RankParams) -> ToolReport:
        """Read `score_field` as numbers and min-max them to 0..1; no model involved."""
        assert params.score_field is not None
        items_in = len(run.items)
        selected = select(run, params.where)
        raw = {
            i.id: f
            for i in selected
            if (f := parse_number(i.fields.get(params.score_field))) is not None
        }
        norm = min_max(raw)
        for item in selected:
            v = norm.get(item.id)
            item.fields[f"{params.output_field}_score"] = None if v is None else round(v, 3)
        report = ToolReport(tool=self.name, items_in=items_in, items_out=items_in)
        report.code_ops += len(selected)
        if len(raw) < len(selected):
            report.notes.append(
                f"{len(selected) - len(raw)} items had no numeric {params.score_field!r}; "
                "ranked last"
            )
        if len(selected) < items_in:
            report.notes.append(f"{len(selected)} of {items_in} items matched `where`")
        return report

    @staticmethod
    def _sort(items: list[ItemState], params: RankParams) -> list[ItemState]:
        """Stable sort by normalized score; items without a score go last, in input order."""
        key = f"{params.output_field}_score"
        scored = [i for i in items if i.fields.get(key) is not None]
        unscored = [i for i in items if i.fields.get(key) is None]
        sign = -1.0 if params.descending else 1.0
        return sorted(scored, key=lambda i: sign * float(i.fields[key])) + unscored

    async def _tie_break(
        self, order: list[ItemState], params: RankParams, ctx: RunContext, report: ToolReport
    ) -> None:
        """One pass down the list, swapping a near-tied pair only when both orders agree.

        "Should be higher" means "shows more" when descending and "shows less" when
        ascending, so the swap test flips with `descending`.
        """
        key = f"{params.output_field}_score"
        n_scored = sum(1 for i in order if i.fields.get(key) is not None)
        window = n_scored if params.top_k is None else min(params.top_k, n_scored)
        promote = "item_b" if params.descending else "item_a"
        questions = pair_questions(params.criterion)
        runs = swaps = 0
        for pos in range(window - 1):
            a, b = order[pos], order[pos + 1]
            if abs(float(a.fields[key]) - float(b.fields[key])) > params.tie_margin:
                continue
            if runs >= params.max_tiebreaks:
                report.notes.append(f"tie-break budget of {params.max_tiebreaks} reached")
                break
            runs += 1
            state = {
                "item_a": get_input(a, params.input_field)[:MAX_PAIR_CHARS],
                "item_b": get_input(b, params.input_field)[:MAX_PAIR_CHARS],
            }
            try:
                answers = await ctx.decider.decide(
                    item_id=f"{a.id}|{b.id}", tool=self.name, state=state, questions=questions
                )
            except (DecisionError, StateTooLarge) as e:
                report.notes.append(f"{a.id}|{b.id}: tie-break skipped ({type(e).__name__})")
                continue
            for ans in answers.values():
                report.jev_decisions += ans.decided_by == "jev"
                report.llm_decisions += ans.decided_by == "llm"
                report.low_confidence += ans.low_confidence
            if all(ans.value == promote for ans in answers.values()):
                order[pos], order[pos + 1] = b, a
                swaps += 1
        report.notes.append(f"tie-breaks: {runs} run, {swaps} swapped")
