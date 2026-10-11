"""Classify: put each item into exactly one category.

Three shapes, one tool:

- **Flat** (up to 255 options including `other`): one Jev Choice per item.
- **Tournament** (a longer flat list): Jev's Choice tops out at 255 options, so the
  options are split into chunks of at most 200, every chunk is asked in one Jev call
  (Jev answers all questions in a call in parallel against the same state), and a second
  call picks among the chunk winners.
- **Taxonomy** (a category tree): walked level by level with beam search over Jev's
  probabilities, so a close call near the top ("hardware" 0.55 vs "software" 0.45) does
  not lock the item into a branch where nothing fits well. All kept nodes of a level go
  in one Jev call, so a tree costs one call per level.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from foreman.config import get_settings
from foreman.core.concurrency import gather_bounded
from foreman.core.decide import DecisionError
from foreman.core.jev import StateTooLarge
from foreman.core.questions import MAX_OPTIONS, Answer, Choice
from foreman.core.state import ItemState, RunState, get_input, set_output
from foreman.tools.base import MAX_ITEM_NOTES, Tool, ToolParams, ToolReport, select

if TYPE_CHECKING:
    from foreman.core.context import RunContext

DEFAULT_INSTRUCTIONS = "Which category best describes the text?"
OTHER = "other"
OTHER_DESCRIPTION = "None of the listed categories fits the text"
CHUNK_SIZE = 200
MAX_FLAT_OPTIONS = CHUNK_SIZE * (MAX_OPTIONS - 1)  # every chunk winner must fit the final
MAX_DEPTH = 6
MIN_PROB = 0.1  # beam candidates below this (within their own question) are pruned
MAX_COVERS = 8  # children named in an undescribed parent's option text


def _check_keys(keys: list[str], where: str) -> None:
    """Keys must be non-empty and unique ignoring case and surrounding spaces."""
    if not keys:
        raise ValueError(f"{where} is empty")
    if any(not k or not k.strip() for k in keys):
        raise ValueError(f"{where} has an empty category key")
    norm = [k.strip().lower() for k in keys]
    dupes = sorted({k for k in norm if norm.count(k) > 1})
    if dupes:
        raise ValueError(f"{where} has repeated category keys: {', '.join(dupes)}")


class Node(BaseModel):
    """One category in a taxonomy. Leaves have no children.

    A bare string (or null) is accepted as shorthand for a leaf with that description,
    which keeps planner-written trees short.
    """

    description: str | None = None
    children: dict[str, Node] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _leaf_shorthand(cls, v: Any) -> Any:
        if v is None or isinstance(v, str):
            return {"description": v}
        return v

    @field_validator("children")
    @classmethod
    def _children_keys(cls, v: dict[str, Node]) -> dict[str, Node]:
        if v:
            _check_level(v, "a taxonomy node")
        return v


def _check_level(nodes: dict[str, Node], where: str) -> None:
    """One tree level is one Choice, so it must fit Jev's limit with `other` added."""
    _check_keys(list(nodes), where)
    if len(nodes) >= MAX_OPTIONS:
        raise ValueError(f"{where} has {len(nodes)} children; the maximum is {MAX_OPTIONS - 1}")


def _depth(nodes: dict[str, Node]) -> int:
    return 1 + max((_depth(n.children) for n in nodes.values() if n.children), default=0)


class ClassifyParams(ToolParams):
    """Categories (flat `options` or a `taxonomy` tree) and how to search them."""

    options: dict[str, str | None] | None = Field(
        default=None,
        description="Category key -> description. Keys are short slugs; descriptions state "
        "boundaries, e.g. 'charges, refunds, invoices — not login problems'.",
    )
    taxonomy: dict[str, Node] | None = Field(
        default=None,
        description="Category tree: key -> {description, children}. Leaves have no children.",
    )
    instructions: str | None = Field(
        default=None, description=f"The question to ask. Default: {DEFAULT_INSTRUCTIONS!r}"
    )
    add_other: bool = Field(
        default=True, description=f"Add {OTHER!r} ({OTHER_DESCRIPTION}) as the last option."
    )
    beam_width: int = Field(default=2, ge=1, le=4, description="Taxonomy paths kept per level.")
    top_n_probs: int = Field(default=3, ge=1, le=20)
    output_field: str = "category"

    @field_validator("options")
    @classmethod
    def _options_keys(cls, v: dict[str, str | None] | None) -> dict[str, str | None] | None:
        if v is not None:
            _check_keys(list(v), "options")
            if len(v) > MAX_FLAT_OPTIONS:
                raise ValueError(f"options has {len(v)} keys; the maximum is {MAX_FLAT_OPTIONS}")
        return v

    @field_validator("taxonomy")
    @classmethod
    def _taxonomy_shape(cls, v: dict[str, Node] | None) -> dict[str, Node] | None:
        if v is not None:
            _check_level(v, "taxonomy")
            if _depth(v) > MAX_DEPTH:
                raise ValueError(f"taxonomy is deeper than {MAX_DEPTH} levels")
        return v

    @model_validator(mode="after")
    def _exactly_one(self) -> ClassifyParams:
        if (self.options is None) == (self.taxonomy is None):
            raise ValueError("give exactly one of `options` or `taxonomy`")
        return self

    @property
    def question(self) -> str:
        return self.instructions or DEFAULT_INSTRUCTIONS


@dataclass(frozen=True)
class _Beam:
    """A partial taxonomy path; `children` is None or empty once the path has ended."""

    path: tuple[str, ...]
    prob: float
    answers: tuple[Answer, ...]
    children: dict[str, Node] | None

    @property
    def done(self) -> bool:
        return not self.children


@dataclass
class _Result:
    """One item's classification: the chosen value, the answers that led to it, all asked."""

    value: str
    chosen: list[Answer]
    asked: list[Answer]
    path: tuple[str, ...] | None = None


def with_other(options: dict[str, str | None], add_other: bool) -> dict[str, str | None]:
    """`options` plus the escape option last, unless it is off or already present."""
    if add_other and OTHER not in options:
        return {**options, OTHER: OTHER_DESCRIPTION}
    return dict(options)


def chunks(keys: list[str], size: int = CHUNK_SIZE) -> list[list[str]]:
    """Split into the fewest chunks of at most `size`, evenly sized."""
    n = math.ceil(len(keys) / size)
    step = math.ceil(len(keys) / n)
    return [keys[i : i + step] for i in range(0, len(keys), step)]


def choice_probs(a: Answer) -> dict[str, float]:
    """Per-option probabilities; an LLM answer counts as certain for its chosen key."""
    if a.decided_by == "llm" or not a.probabilities:
        return {str(a.value): 1.0}
    return a.probabilities


def top_probs(a: Answer, n: int) -> dict[str, float]:
    """The `n` most likely options of an answer, rounded for display."""
    ranked = sorted(choice_probs(a).items(), key=lambda kv: -kv[1])[:n]
    return {k: round(p, 3) for k, p in ranked}


def _node_options(children: dict[str, Node], add_other: bool) -> dict[str, str | None]:
    """Choice options for one tree level; undescribed parents list what they cover."""
    opts: dict[str, str | None] = {}
    for key, node in children.items():
        desc = node.description
        if desc is None and node.children:
            names = list(node.children)[:MAX_COVERS]
            more = ", ..." if len(node.children) > MAX_COVERS else ""
            desc = f"Covers: {', '.join(names)}{more}"
        opts[key] = desc
    return with_other(opts, add_other)


def _qid(path: tuple[str, ...], used: set[str]) -> str:
    base = "n_" + ("__".join(path) if path else "root")
    qid, i = base, 1
    while qid in used:
        qid, i = f"{base}_{i}", i + 1
    used.add(qid)
    return qid


def _keep(cands: list[tuple[float, _Beam]], width: int) -> list[_Beam]:
    """Top `width` paths by path probability, dropping weak picks but never everything."""
    ranked = sorted(cands, key=lambda c: -c[1].prob)
    kept = [b for p, b in ranked if p >= MIN_PROB][:width]
    return kept or [ranked[0][1]]


class Classify(Tool):
    """Puts each item into exactly one category: flat list, tournament or tree."""

    name: ClassVar[str] = "classify"
    description: ClassVar[str] = (
        "Puts each item into exactly one category, from a flat list (`options`) or a "
        "category tree (`taxonomy`). Use it to tag, route or triage items when the categories "
        "are known up front. Option descriptions should state boundaries, e.g. "
        "'billing: charges, refunds, invoices — not login problems'. Use `taxonomy` for nested "
        "categories instead of flattening them; lists over 255 options are handled automatically."
    )
    Params: ClassVar[type[ToolParams]] = ClassifyParams
    uses: ClassVar[Literal["jev", "jev+llm", "code"]] = "jev"

    async def run(self, run: RunState, params: ToolParams, ctx: RunContext) -> ToolReport:
        """Classify every selected item with bounded concurrency, then count the classes."""
        assert isinstance(params, ClassifyParams)
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

        out = params.output_field
        report = ToolReport(tool=self.name, items_in=items_in, items_out=items_in)
        counts: Counter[str] = Counter()
        errors: list[str] = []
        for item, (res, err) in zip(selected, results, strict=True):
            if res is None:
                item.fields[f"{out}__error"] = str(err)
                errors.append(f"{item.id}: skipped ({type(err).__name__}: {err})")
                continue
            self._apply(item, res, params)
            counts[res.value] += 1
            for a in res.asked:
                report.jev_decisions += a.decided_by == "jev"
                report.llm_decisions += a.decided_by == "llm"
                report.low_confidence += a.low_confidence

        run.aggregates[f"{out}_counts"] = dict(sorted(counts.items(), key=lambda kv: -kv[1]))
        report.notes.extend(errors[:MAX_ITEM_NOTES])
        if len(errors) > MAX_ITEM_NOTES:
            report.notes.append(f"... and {len(errors) - MAX_ITEM_NOTES} more items skipped")
        if len(selected) < items_in:
            report.notes.append(f"{len(selected)} of {items_in} items matched `where`")
        report.items_out = len(run.items)
        report.duration_ms = int((time.perf_counter() - t0) * 1000)
        return report

    async def _one(
        self, item: ItemState, params: ClassifyParams, ctx: RunContext
    ) -> tuple[_Result | None, Exception | None]:
        # Expected per-item failures are returned, not raised, so one item can't fail the node.
        try:
            if params.taxonomy is not None:
                return await self._tree(item, params, ctx), None
            assert params.options is not None
            options = with_other(params.options, params.add_other)
            if len(options) <= MAX_OPTIONS:
                return await self._flat(item, params, ctx, options), None
            return await self._tournament(item, params, ctx), None
        except (DecisionError, StateTooLarge) as e:
            return None, e

    async def _ask(
        self, item: ItemState, params: ClassifyParams, ctx: RunContext, qs: dict[str, Choice]
    ) -> dict[str, Answer]:
        return await ctx.decider.decide(
            item_id=item.id,
            tool=self.name,
            state=get_input(item, params.input_field),
            questions=dict(qs),
        )

    async def _flat(
        self,
        item: ItemState,
        params: ClassifyParams,
        ctx: RunContext,
        options: dict[str, str | None],
    ) -> _Result:
        a = (await self._ask(item, params, ctx, {"category": Choice(params.question, options)}))[
            "category"
        ]
        return _Result(str(a.value), [a], [a])

    async def _tournament(
        self, item: ItemState, params: ClassifyParams, ctx: RunContext
    ) -> _Result:
        """Chunk winners in one call, then a final pick among them (plus `other`)."""
        assert params.options is not None
        keys = [k for k in params.options if k != OTHER]
        qs = {
            f"chunk_{i}": Choice(params.question, {k: params.options[k] for k in chunk})
            for i, chunk in enumerate(chunks(keys))
        }
        round1 = await self._ask(item, params, ctx, qs)
        winners = {str(a.value): a for a in round1.values()}
        finals = {k: params.options[k] for k in winners}
        if params.add_other or OTHER in params.options:
            finals[OTHER] = params.options.get(OTHER, OTHER_DESCRIPTION)
        final = (await self._ask(item, params, ctx, {"final": Choice(params.question, finals)}))[
            "final"
        ]
        value = str(final.value)
        chosen = [winners[value], final] if value in winners else [final]
        return _Result(value, chosen, [*round1.values(), final])

    async def _tree(self, item: ItemState, params: ClassifyParams, ctx: RunContext) -> _Result:
        """Beam search: one Jev call per level over every kept, unfinished path."""
        assert params.taxonomy is not None
        beams = [_Beam((), 1.0, (), params.taxonomy)]
        asked: list[Answer] = []
        for _ in range(MAX_DEPTH):
            open_ = [b for b in beams if not b.done]
            if not open_:
                break
            used: set[str] = set()
            qids = [_qid(b.path, used) for b in open_]
            qs = {
                qid: Choice(self._level_question(params, b.path), self._options(b, params))
                for qid, b in zip(qids, open_, strict=True)
            }
            answers = await self._ask(item, params, ctx, qs)
            asked.extend(answers.values())

            cands: list[tuple[float, _Beam]] = [(1.0, b) for b in beams if b.done]
            for qid, b in zip(qids, open_, strict=True):
                a = answers[qid]
                probs = choice_probs(a)
                assert b.children is not None
                for key in qs[qid].options:
                    p = probs.get(key, 0.0)
                    if p <= 0:
                        continue
                    child = b.children.get(key)
                    nxt = child.children if child is not None else None
                    cands.append((p, _Beam((*b.path, key), b.prob * p, (*b.answers, a), nxt)))
            beams = _keep(cands, params.beam_width)

        best = max(beams, key=lambda b: b.prob)
        return _Result(best.path[-1], list(best.answers), asked, path=best.path)

    @staticmethod
    def _level_question(params: ClassifyParams, path: tuple[str, ...]) -> str:
        if not path:
            return params.question
        return f"{params.question} The options are subcategories of {' > '.join(path)}."

    @staticmethod
    def _options(beam: _Beam, params: ClassifyParams) -> dict[str, str | None]:
        assert beam.children is not None
        return _node_options(beam.children, params.add_other)

    @staticmethod
    def _apply(item: ItemState, res: _Result, params: ClassifyParams) -> None:
        out = params.output_field
        by = "llm" if any(a.decided_by == "llm" for a in res.chosen) else "jev"
        set_output(
            item,
            out,
            res.value,
            confidence=min(a.confidence for a in res.chosen),
            by=by,
            low_confidence=any(a.low_confidence for a in res.chosen),
        )
        if res.path is not None:
            item.fields[f"{out}_path"] = " > ".join(res.path)
        item.fields[f"{out}__probs"] = top_probs(res.chosen[-1], params.top_n_probs)
