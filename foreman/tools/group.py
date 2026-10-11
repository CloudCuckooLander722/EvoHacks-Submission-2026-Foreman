"""Group: discover themes in a pile of items and put every item in one of them.

"10,000 reviews -> 8 themes with counts." The LLM only names themes, from a small seeded
sample (one call), plus at most one more call to name themes for the outliers. Jev assigns
every item with one Choice, so the cost of a run grows with Jev's price, not the LLM's.

Steps: propose themes (unless given) -> assign every item -> if enough items fit none of
the themes, propose a few new ones and re-assign only those outliers -> write outputs and
a `{out}_groups` aggregate with counts and example ids.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

from foreman.config import get_settings
from foreman.core.concurrency import gather_bounded
from foreman.core.decide import DecisionError
from foreman.core.jev import StateTooLarge
from foreman.core.ledger import Decision
from foreman.core.llm import LLMError, track_usage
from foreman.core.questions import Answer, Choice
from foreman.core.state import ItemState, RunState, get_input, set_output
from foreman.tools.base import MAX_ITEM_NOTES, Tool, ToolParams, ToolReport, select

if TYPE_CHECKING:
    from foreman.core.context import RunContext

NONE = "none_of_these"
NONE_DESCRIPTION = "The text fits none of the themes above"
SAMPLE_CHARS = 300
MAX_OUTLIER_SAMPLE = 40
OUTLIER_SHARE = 0.03
MAX_GIVEN_THEMES = 200
MAX_SLUG = 40
EXAMPLES = 3
_MENTIONS_TEXT = re.compile(r"\btext\b", re.IGNORECASE)

_THEME_RULES = (
    "Rules:\n"
    "- name: short, 1-4 words. All names must be distinct.\n"
    "- description: one sentence stating what belongs in the theme, then one boundary "
    '("not X"), e.g. "The app crashes when the camera opens; not slow loading."\n'
    "- Each theme must be about one thing a reader can see stated in the text. Do not "
    'combine two conditions with "or" in one theme; make two themes instead.\n'
    'Reply with only a JSON object: {"themes": [{"name": "...", "description": "..."}]}'
)


class GroupError(RuntimeError):
    """Theme discovery failed (LLM error or too few usable themes); nothing was assigned."""


class Theme(BaseModel):
    """One theme: a short display name and a one-sentence description with a boundary."""

    name: str = Field(min_length=1, description="Short, 1-4 words.")
    description: str = Field(default="", description="What belongs, plus one boundary ('not X').")

    @field_validator("name", "description")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()


class GroupParams(ToolParams):
    """Theme discovery settings, or fixed `themes` to skip discovery."""

    themes: list[Theme] | None = Field(
        default=None, description="Fixed themes; if given, theme discovery is skipped."
    )
    n_themes: int = Field(default=8, ge=2, le=30, description="Themes to discover.")
    focus: str | None = Field(
        default=None,
        description="What the themes should be about, stated in the text, e.g. "
        "'the product problem described'.",
    )
    sample_size: int = Field(default=60, ge=2, le=500, description="Items the LLM reads.")
    allow_new_themes: bool = Field(
        default=True, description="Propose new themes once if many items fit none."
    )
    max_new_themes: int = Field(default=3, ge=0, le=10)
    outlier_min: int = Field(
        default=10, ge=1, description="Refine only with at least this many outliers."
    )
    seed: int = 7
    output_field: str = "theme"

    @field_validator("themes")
    @classmethod
    def _themes_ok(cls, v: list[Theme] | None) -> list[Theme] | None:
        if v is None:
            return v
        if not 2 <= len(v) <= MAX_GIVEN_THEMES:
            raise ValueError(f"themes needs 2 to {MAX_GIVEN_THEMES} entries, got {len(v)}")
        names = [t.name.lower() for t in v]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"themes has repeated names: {', '.join(dupes)}")
        return v

    @property
    def instructions(self) -> str:
        """The Choice question; it names the text because Jev answers that more reliably."""
        if self.focus is None or not self.focus.strip():
            return "Which theme best describes the text?"
        focus = self.focus.strip().rstrip("?.")
        suffix = "" if _MENTIONS_TEXT.search(focus) else " in the text"
        return f"Which theme best describes {focus}{suffix}?"


def slugify(name: str, used: set[str]) -> str:
    """A unique option key for a theme name, never colliding with `none_of_these`."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:MAX_SLUG].strip("_") or "theme"
    slug, i = base, 2
    while slug in used or slug == NONE:
        slug, i = f"{base}_{i}", i + 1
    used.add(slug)
    return slug


def validate_themes(raw: Any, limit: int, existing: list[Theme] | None = None) -> list[Theme]:
    """Usable themes from LLM output: well-formed, deduped case-insensitively, trimmed.

    Names already in `existing` are dropped too, so refinement only adds new themes.
    """
    items = raw.get("themes") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    seen = {t.name.lower() for t in existing or []} | {NONE}
    out: list[Theme] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        name, desc = entry.get("name"), entry.get("description", "")
        if not isinstance(name, str) or not isinstance(desc, str) or not name.strip():
            continue
        theme = Theme(name=name, description=desc)
        if theme.name.lower() in seen:
            continue
        seen.add(theme.name.lower())
        out.append(theme)
        if len(out) >= limit:
            break
    return out


def _snippet(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:SAMPLE_CHARS]


def _numbered(texts: list[str]) -> str:
    return "\n".join(f"{i}. {t}" for i, t in enumerate(texts, 1))


def _about(focus: str | None) -> str:
    return f" Every theme must be about {focus.strip()}." if focus and focus.strip() else ""


def propose_prompt(texts: list[str], n: int, focus: str | None) -> tuple[str, str]:
    """System/user prompts asking for exactly `n` themes covering the sample."""
    system = (
        "You name the themes in a pile of short texts. Read the SAMPLE and propose exactly "
        f"{n} themes that together cover it.{_about(focus)}\n{_THEME_RULES}"
    )
    user = f"SAMPLE ({len(texts)} texts):\n{_numbered(texts)}"
    return system, user


def refine_prompt(
    texts: list[str], existing: list[Theme], m: int, focus: str | None
) -> tuple[str, str]:
    """System/user prompts asking for up to `m` NEW themes for texts that fit none."""
    system = (
        "The texts below fit none of the EXISTING THEMES. Propose at most "
        f"{m} NEW themes that cover them.{_about(focus)} A NEW theme must not overlap any "
        'existing theme. If the texts share no clear theme, reply {"themes": []}.\n'
        f"{_THEME_RULES}"
    )
    names = "\n".join(f"- {t.name}" for t in existing)
    user = f"EXISTING THEMES:\n{names}\n\nTEXTS ({len(texts)}):\n{_numbered(texts)}"
    return system, user


async def _llm_json(ctx: RunContext, system: str, user: str) -> tuple[dict[str, Any], float, int]:
    """A direct LLM call (not a decision), with its calls and cost recorded for run stats."""
    with track_usage() as u:
        try:
            raw = await ctx.llm.json(system, user)
        finally:
            if u.calls:
                ctx.ledger.record_call("llm", u.cost_usd, n=u.calls)
    return raw, u.cost_usd, u.latency_ms


@dataclass
class _Assignment:
    """One item's result: the final answer (None if it failed) plus every answer asked."""

    answer: Answer | None
    asked: list[Answer] = field(default_factory=list)
    error: Exception | None = None


def _value(r: _Assignment) -> str | None:
    return None if r.answer is None else str(r.answer.value)


class Group(Tool):
    """LLM names themes from a sample; Jev assigns every item; outliers get one refinement."""

    name: ClassVar[str] = "group"
    description: ClassVar[str] = (
        "Discovers themes in a pile of items and puts every item in one theme, with counts, "
        "e.g. '10,000 reviews -> 8 themes'. Use Group when the categories aren't known in "
        "advance; use Classify when they are. Set `focus` to something the text states "
        "(e.g. 'the product problem described'), not something that must be inferred; if you "
        "give `themes`, each description must name one observable condition (no 'X or Y')."
    )
    Params: ClassVar[type[ToolParams]] = GroupParams
    uses: ClassVar[Literal["jev", "jev+llm", "code"]] = "jev+llm"

    async def run(self, run: RunState, params: ToolParams, ctx: RunContext) -> ToolReport:
        """Propose (or take) themes, assign every selected item, refine once, aggregate."""
        assert isinstance(params, GroupParams)
        t0 = time.perf_counter()
        items_in = len(run.items)
        selected = select(run, params.where)
        report = ToolReport(tool=self.name, items_in=items_in, items_out=items_in)
        if len(selected) < items_in:
            report.notes.append(f"{len(selected)} of {items_in} items matched `where`")
        if not selected:
            report.notes.append("no items to group")
            return report

        if params.themes is not None:
            themes = list(params.themes)
            report.notes.append(f"{len(themes)} themes given; discovery skipped")
        else:
            themes = await self._propose(selected, params, ctx)
            report.llm_decisions += 1  # the proposal is an LLM decision in the ledger too
            report.notes.append(f"proposed {len(themes)} themes from a sample")

        results = await self._assign(selected, themes, params, ctx)

        outliers = [i for i in selected if _value(results[i.id]) == NONE]
        needed = max(params.outlier_min, OUTLIER_SHARE * len(selected))
        if not params.allow_new_themes or params.max_new_themes == 0:
            report.notes.append("refinement: off")
        elif len(outliers) < needed:
            report.notes.append(f"refinement: skipped ({len(outliers)} outliers, needs {needed:g})")
        else:
            new = await self._refine(outliers, themes, params, ctx, report)
            if new:
                themes = themes + new
                again = await self._assign(outliers, themes, params, ctx)
                for item_id, r in again.items():
                    prev = results[item_id]
                    if r.answer is None:  # keep the first-round answer rather than lose it
                        prev.asked += r.asked
                        continue
                    r.asked = prev.asked + r.asked
                    results[item_id] = r

        self._write(run, selected, results, themes, params, report)
        report.items_out = len(run.items)
        report.duration_ms = int((time.perf_counter() - t0) * 1000)
        return report

    async def _propose(
        self, selected: list[ItemState], params: GroupParams, ctx: RunContext
    ) -> list[Theme]:
        """One LLM call on a seeded sample; fewer than 2 usable themes is an error."""
        k = min(params.sample_size, len(selected))
        sample = random.Random(params.seed).sample(selected, k)
        texts = [_snippet(get_input(i, params.input_field)) for i in sample]
        system, user = propose_prompt(texts, params.n_themes, params.focus)
        try:
            raw, cost, latency = await _llm_json(ctx, system, user)
        except LLMError as e:
            raise GroupError(f"group: theme proposal failed: {e}") from e
        themes = validate_themes(raw, params.n_themes)
        self._ledger_proposal(ctx, "themes", themes, cost, latency)
        if len(themes) < 2:
            raise GroupError(
                f"group: the LLM proposed {len(themes)} usable themes; at least 2 are needed. "
                "Pass `themes` explicitly or change `focus`."
            )
        return themes

    async def _refine(
        self,
        outliers: list[ItemState],
        themes: list[Theme],
        params: GroupParams,
        ctx: RunContext,
        report: ToolReport,
    ) -> list[Theme]:
        """One LLM call for up to `max_new_themes` new themes. Failure keeps the outliers."""
        k = min(MAX_OUTLIER_SAMPLE, len(outliers))
        sample = random.Random(params.seed).sample(outliers, k)
        texts = [_snippet(get_input(i, params.input_field)) for i in sample]
        system, user = refine_prompt(texts, themes, params.max_new_themes, params.focus)
        try:
            raw, cost, latency = await _llm_json(ctx, system, user)
        except LLMError as e:
            report.notes.append(f"refinement: ran but the LLM failed ({e}); outliers kept")
            return []
        new = validate_themes(raw, params.max_new_themes, existing=themes)
        self._ledger_proposal(ctx, "new_themes", new, cost, latency)
        report.llm_decisions += 1
        names = ", ".join(t.name for t in new) or "none usable"
        report.notes.append(
            f"refinement: ran on {len(outliers)} outliers; {len(new)} new themes ({names})"
        )
        return new

    def _ledger_proposal(
        self, ctx: RunContext, qid: str, themes: list[Theme], cost: float, latency: int
    ) -> None:
        ctx.ledger.add(
            Decision(
                run_id=ctx.run_id,
                item_id="__run__",
                tool=self.name,
                question_id=qid,
                qtype="proposal",
                value=[t.model_dump() for t in themes],
                confidence=None,
                probabilities=None,
                decided_by="llm",
                kind="proposal",
                latency_ms=latency,
                cost_usd=cost,
            )
        )

    async def _assign(
        self, items: list[ItemState], themes: list[Theme], params: GroupParams, ctx: RunContext
    ) -> dict[str, _Assignment]:
        """One Choice per item over the themes plus `none_of_these`; failures are skipped."""
        used: set[str] = set()
        options: dict[str, str | None] = {slugify(t.name, used): t.description for t in themes}
        options[NONE] = NONE_DESCRIPTION
        question = Choice(params.instructions, options)

        def progress(done: int, total: int) -> None:
            if ctx.progress is not None:
                ctx.progress(self.name, done, total)

        async def one(item: ItemState) -> _Assignment:
            # Wrapped, not returned bare: gather_bounded re-raises exceptions it gets back.
            try:
                answers = await ctx.decider.decide(
                    item_id=item.id,
                    tool=self.name,
                    state=get_input(item, params.input_field),
                    questions={"theme": question},
                )
            except (DecisionError, StateTooLarge) as e:
                return _Assignment(None, error=e)
            return _Assignment(answers["theme"], [answers["theme"]])

        results = await gather_bounded(
            [lambda item=item: one(item) for item in items],
            limit=get_settings().jev_max_concurrency,
            on_done=progress,
        )
        return {item.id: r for item, r in zip(items, results, strict=True)}

    def _write(
        self,
        run: RunState,
        selected: list[ItemState],
        results: dict[str, _Assignment],
        themes: list[Theme],
        params: GroupParams,
        report: ToolReport,
    ) -> None:
        """Set each item's theme name and build the `{out}_groups` aggregate."""
        out = params.output_field
        used: set[str] = set()
        by_slug = {slugify(t.name, used): t for t in themes}
        members: dict[str, list[str]] = {t.name: [] for t in themes}
        members[NONE] = []
        errors: list[str] = []
        for item in selected:
            r = results[item.id]
            a = r.answer
            if a is None:
                item.fields[f"{out}__error"] = str(r.error)
                errors.append(f"{item.id}: skipped ({type(r.error).__name__}: {r.error})")
                continue
            theme = by_slug.get(str(a.value))
            value = theme.name if theme is not None else NONE
            set_output(
                item,
                out,
                value,
                confidence=a.confidence,
                by=a.decided_by,
                low_confidence=a.low_confidence,
            )
            members[value].append(item.id)
            for x in r.asked:
                report.jev_decisions += x.decided_by == "jev"
                report.llm_decisions += x.decided_by == "llm"
                report.low_confidence += x.low_confidence

        groups = [
            {
                "theme": t.name,
                "description": t.description,
                "count": len(members[t.name]),
                "example_ids": members[t.name][:EXAMPLES],
            }
            for t in themes
        ]
        groups.sort(key=lambda g: -g["count"])
        if members[NONE]:
            groups.append(
                {
                    "theme": NONE,
                    "description": NONE_DESCRIPTION,
                    "count": len(members[NONE]),
                    "example_ids": members[NONE][:EXAMPLES],
                }
            )
        run.aggregates[f"{out}_groups"] = groups
        report.notes.extend(errors[:MAX_ITEM_NOTES])
        if len(errors) > MAX_ITEM_NOTES:
            report.notes.append(f"... and {len(errors) - MAX_ITEM_NOTES} more items skipped")
