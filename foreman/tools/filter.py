"""Filter: keep only the items that pass a yes/no condition.

Usually the first node of a plan: every junk item dropped here is one fewer item that
every later tool pays to process. One Noul per item; the keep/drop cutoff is applied in
code so the planner can tune strictness without rewording the question.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import Field, field_validator

from foreman.core.ledger import Decision
from foreman.core.questions import Answer, Noul, Question
from foreman.core.state import ItemState, RunState, get_input, set_output
from foreman.tools.base import PerItemTool, ToolParams, ToolReport, select

if TYPE_CHECKING:
    from foreman.core.context import RunContext

_MENTIONS_TEXT = re.compile(r"\btext\b", re.IGNORECASE)


class FilterParams(ToolParams):
    """Params for `filter`. `condition` must be phrased so that "yes" means keep."""

    condition: str = Field(description='A yes/no question about one item; "yes" means keep.')
    true_means: str | None = Field(None, description="Optional: what qualifies as yes.")
    false_means: str | None = Field(None, description="Optional: what counts as no.")
    cutoff: float = Field(0.5, ge=0.0, le=1.0, description="Keep when P(yes) >= cutoff.")
    drop: bool = Field(True, description="Move non-kept items out of the run into `dropped`.")
    output_field: str = "keep"

    @field_validator("condition")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("condition must not be empty")
        return v


def about_the_text(condition: str) -> str:
    """Make the question name the state, because Jev answers more reliably when it does."""
    if _MENTIONS_TEXT.search(condition):
        return condition
    return f"About the text: {condition}"


class FilterTool(PerItemTool):
    """One Noul per item; items below the cutoff are moved to `run.dropped`."""

    name: ClassVar[str] = "filter"
    description: ClassVar[str] = (
        "Keeps only the items that pass a yes/no condition, e.g. 'Is this a genuine product "
        "review (not spam or an ad)?'. Use it early to remove irrelevant items, or with "
        "drop=false to split items by a yes/no property into a boolean field. Phrase "
        "`condition` so that 'yes' means keep."
    )
    Params: ClassVar[type[ToolParams]] = FilterParams
    uses: ClassVar[Literal["jev", "jev+llm", "code"]] = "jev"

    def questions(self, item: ItemState, params: FilterParams) -> dict[str, Question]:
        return {
            "keep": Noul(
                instructions=about_the_text(params.condition),
                true=params.true_means,
                false=params.false_means,
            )
        }

    async def _one(
        self, item: ItemState, params: Any, ctx: RunContext
    ) -> tuple[dict[str, Answer] | None, Exception | None]:
        # Empty input has nothing to judge: decide "no" in code instead of paying for a call.
        if not get_input(item, params.input_field).strip():
            ctx.ledger.add(
                Decision(
                    run_id=ctx.run_id,
                    item_id=item.id,
                    tool=self.name,
                    question_id="keep",
                    qtype="noul",
                    value=0.0,
                    confidence=1.0,
                    probabilities=None,
                    decided_by="code",
                )
            )
            return {}, None
        return await super()._one(item, params, ctx)

    def apply(self, item: ItemState, answers: dict[str, Answer], params: FilterParams) -> None:
        out = params.output_field
        answer = answers.get("keep")
        if answer is None:  # empty input, decided in `_one`
            set_output(item, out, False, confidence=1.0, by="code")
            item.fields[f"{out}__p"] = 0.0
            return
        p = float(answer.value)
        set_output(
            item,
            out,
            p >= params.cutoff,
            confidence=answer.confidence,
            by=answer.decided_by,
            low_confidence=answer.low_confidence,
        )
        item.fields[f"{out}__p"] = round(p, 3)

    def after_all(self, run: RunState, params: FilterParams, report: ToolReport) -> None:
        """Count code decisions and, if `drop`, move rejected items to `run.dropped`.

        Only items processed by this node can be dropped: items outside `where` have no
        output, and items that errored are kept because an error is not a "no".
        """
        out = params.output_field
        processed = [
            i
            for i in select(run, params.where)
            if out in i.fields and f"{out}__error" not in i.fields
        ]
        report.code_ops += sum(i.fields.get(f"{out}__by") == "code" for i in processed)
        if not params.drop:
            return
        rejected = [i for i in processed if i.fields[out] is False]
        for item in rejected:
            item.meta["drop_reason"] = f"filter: {params.condition}"
        run.dropped.extend(rejected)
        gone = {id(i) for i in rejected}
        run.items = [i for i in run.items if id(i) not in gone]
        report.notes.append(f"dropped {len(rejected)} of {len(processed)} processed items")
