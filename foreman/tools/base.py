"""Tool, PerItemTool, ToolParams, ToolReport.

A tool is one node of a plan. Most tools ask the same questions about every item
independently; `PerItemTool` does the fan-out, routing, error handling and counting for
them, so a concrete tool only says which questions to ask and how to apply the answers.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, Field

from foreman.config import get_settings
from foreman.core.concurrency import gather_bounded
from foreman.core.decide import DecisionError
from foreman.core.jev import StateTooLarge
from foreman.core.questions import Answer, Question
from foreman.core.state import Condition, ItemState, RunState, get_input

if TYPE_CHECKING:
    from foreman.core.context import RunContext

MAX_ITEM_NOTES = 20


class ToolParams(BaseModel):
    """Params every tool accepts. Subclasses add their own and set `output_field`'s default."""

    input_field: str = "text"
    output_field: str
    where: Condition | None = None


class ToolReport(BaseModel):
    """What one node did, for the meter, results.json and debugging."""

    tool: str
    items_in: int
    items_out: int
    jev_decisions: int = 0
    llm_decisions: int = 0
    code_ops: int = 0
    low_confidence: int = 0
    duration_ms: int = 0
    notes: list[str] = Field(default_factory=list)


class Tool(ABC):
    """One plan node. Subclasses define the class attributes and `run`.

    `description` is what the planner reads to choose tools, so it should say what the
    tool does and when to use it in 2-4 sentences.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    Params: ClassVar[type[ToolParams]]
    uses: ClassVar[Literal["jev", "jev+llm", "code"]]

    @abstractmethod
    async def run(self, run: RunState, params: ToolParams, ctx: RunContext) -> ToolReport:
        """Process `run` in place and report what happened."""


def select(run: RunState, where: Condition | None) -> list[ItemState]:
    """Items a node should process: all of them, or those matching `where`."""
    return [i for i in run.items if where is None or where.matches(i)]


class PerItemTool(Tool):
    """For tools that ask questions about each item independently."""

    @abstractmethod
    def questions(self, item: ItemState, params: Any) -> dict[str, Question]:
        """The questions to ask about one item, keyed by question id."""

    def state(self, item: ItemState, params: Any) -> str | dict:
        """What Jev sees. Default: the item's input text only (less noise, better answers)."""
        return get_input(item, params.input_field)

    @abstractmethod
    def apply(self, item: ItemState, answers: dict[str, Answer], params: Any) -> None:
        """Write the answers onto the item, usually with `set_output`."""

    def after_all(self, run: RunState, params: Any, report: ToolReport) -> None:
        """Optional hook after every item is applied (e.g. Filter moves items to `dropped`)."""

    async def _one(
        self, item: ItemState, params: Any, ctx: RunContext
    ) -> tuple[dict[str, Answer] | None, Exception | None]:
        # Expected per-item failures are returned, not raised, so one item can't fail the node.
        try:
            answers = await ctx.decider.decide(
                item_id=item.id,
                tool=self.name,
                state=self.state(item, params),
                questions=self.questions(item, params),
            )
        except (DecisionError, StateTooLarge) as e:
            return None, e
        return answers, None

    async def run(self, run: RunState, params: ToolParams, ctx: RunContext) -> ToolReport:
        """Ask every selected item's questions with bounded concurrency, then apply."""
        t0 = time.perf_counter()
        items_in = len(run.items)
        selected = select(run, params.where)

        def progress(done: int, total: int) -> None:
            if ctx.progress is not None:
                ctx.progress(self.name, done, total)

        results = await gather_bounded(
            [lambda item=item: self._one(item, params, ctx) for item in selected],
            limit=get_settings().jev_max_concurrency,
            on_done=progress,
        )

        report = ToolReport(tool=self.name, items_in=items_in, items_out=items_in)
        errors: list[str] = []
        for item, (answers, err) in zip(selected, results, strict=True):
            if answers is None:
                item.fields[f"{params.output_field}__error"] = str(err)
                errors.append(f"{item.id}: skipped ({type(err).__name__}: {err})")
                continue
            self.apply(item, answers, params)
            for a in answers.values():
                report.jev_decisions += a.decided_by == "jev"
                report.llm_decisions += a.decided_by == "llm"
                report.low_confidence += a.low_confidence

        report.notes.extend(errors[:MAX_ITEM_NOTES])
        if len(errors) > MAX_ITEM_NOTES:
            report.notes.append(f"... and {len(errors) - MAX_ITEM_NOTES} more items skipped")
        if len(selected) < items_in:
            report.notes.append(f"{len(selected)} of {items_in} items matched `where`")
        self.after_all(run, params, report)
        report.items_out = len(run.items)
        report.duration_ms = int((time.perf_counter() - t0) * 1000)
        return report
