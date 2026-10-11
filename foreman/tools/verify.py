"""Verify: check each claim in some text against a source.

LLM-drafted replies and extracted values are where made-up details creep in. Asking a
model "is this reply accurate?" bundles every sentence into one judgment, so one invented
refund amount hides among five true sentences. Verify splits the claim text into
sentences in code and asks Jev one Choice per sentence (supported / contradicted /
not_mentioned), all in one call per item. The overall verdict is combined in code.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import Field, model_validator

from foreman.core.jev import StateTooLarge
from foreman.core.ledger import Decision
from foreman.core.questions import Answer, Choice, Question
from foreman.core.state import ItemState, RunState, set_output
from foreman.tools.base import PerItemTool, ToolParams, ToolReport, select

if TYPE_CHECKING:
    from foreman.core.context import RunContext

# One condition per option: Jev escalates when an option bundles two ("states or implies").
OPTIONS: dict[str, str | None] = {
    "supported": "The source says what this claim says, possibly in different words.",
    "contradicted": "The source says something that conflicts with this claim.",
    "not_mentioned": "The source does not address this claim.",
}
VERDICTS = (
    "supported",
    "partially_supported",
    "unsupported",
    "contradicted",
    "no_claims",
    "skipped_source_too_large",
)

_PLACEHOLDER = "\x00"
# Abbreviations whose dots must not end a sentence. Sentence-final words like "etc." are
# left out on purpose: merging two sentences is worse than splitting at a rare "etc.".
_ABBREVIATIONS = re.compile(
    r"\b(?:e\.g|i\.e|u\.s|u\.k|mrs|mr|ms|dr|prof|jr|sr|st|vs|inc|ltd)\.", re.IGNORECASE
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[.!?][\"'”’)\]])\s+")
_BULLET = re.compile(r"^\s*(?:[-*•‣◦]|\d+[.)])\s+")


class VerifyParams(ToolParams):
    """Which text holds the claims, which text is the evidence, and how to split claims."""

    claim_field: str = Field(
        description='Field holding the text to check, e.g. a drafted reply. "text" = item text.'
    )
    source_field: str | None = Field(
        "text",
        description='Field holding each item\'s evidence. "text" = item text. Null for none.',
    )
    source_text: str | None = Field(
        None, description="Fixed reference applied to every item, e.g. a refund policy."
    )
    split: bool = Field(
        True,
        description="Split the claim text into sentences; false checks it as one claim "
        "(use false for a short extracted value).",
    )
    min_words: int = Field(4, ge=1, description="Drop split sentences shorter than this.")
    max_claims: int = Field(30, ge=1, le=100, description="Check at most this many sentences.")
    output_field: str = "verified"

    @model_validator(mode="after")
    def _sources(self) -> VerifyParams:
        if self.source_text is not None and not self.source_text.strip():
            self.source_text = None
        if self.source_field is None and self.source_text is None:
            raise ValueError("set source_field, source_text or both")
        if self.claim_field == self.source_field:
            raise ValueError(
                f"claim_field and source_field are both {self.claim_field!r}; a text cannot "
                "verify itself (set source_field to null if only source_text is the source)"
            )
        return self


def split_sentences(text: str) -> list[str]:
    """Sentences and bullet points of `text`, in order, stripped of bullet markers.

    Splits on . ! ? followed by whitespace, on blank lines and at every bullet line.
    Common abbreviations (e.g., i.e., Mr., Dr., U.S.) do not end a sentence.
    """
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if current:
                blocks.append(current)
            current = []
            continue
        if _BULLET.match(line):
            if current:
                blocks.append(current)
            current = [_BULLET.sub("", line, count=1)]
        else:
            current.append(line.strip())
    if current:
        blocks.append(current)

    sentences: list[str] = []
    for block in blocks:
        joined = " ".join(block)
        protected = _ABBREVIATIONS.sub(lambda m: m.group(0)[:-1] + _PLACEHOLDER, joined)
        for part in _SENTENCE_END.split(protected):
            s = part.replace(_PLACEHOLDER, ".").strip()
            if s:
                sentences.append(s)
    return sentences


def field_text(item: ItemState, field: str) -> str:
    """A field as text: "text" is the item text, None is empty, dicts/lists become JSON."""
    if field == "text":
        return item.text
    v = item.fields.get(field)
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


def extract_claims(item: ItemState, params: VerifyParams) -> dict[str, str]:
    """Claim id ("c1", "c2"...) -> claim text, after splitting and filtering."""
    text = field_text(item, params.claim_field).strip()
    if not text:
        return {}
    if not params.split:
        return {"c1": text}
    kept = [s for s in split_sentences(text) if len(s.split()) >= params.min_words]
    return {f"c{n}": s for n, s in enumerate(kept[: params.max_claims], start=1)}


def overall_verdict(verdicts: list[str]) -> str:
    """Any contradiction wins; all supported is supported; none supported is unsupported.

    "unsupported" is separate from "partially_supported" so a reply whose every claim is
    absent from the source is not reported as half right.
    """
    if not verdicts:
        return "no_claims"
    if "contradicted" in verdicts:
        return "contradicted"
    if all(v == "supported" for v in verdicts):
        return "supported"
    if "supported" not in verdicts:
        return "unsupported"
    return "partially_supported"


def _write_code_verdict(item: ItemState, out: str, verdict: str) -> None:
    set_output(item, out, verdict, confidence=1.0, by="code")
    item.fields[f"{out}_claims"] = []
    item.fields[f"{out}_problem_claims"] = []


class VerifyTool(PerItemTool):
    """One Jev Choice per claim sentence, all in one call per item; verdict combined in code."""

    name: ClassVar[str] = "verify"
    description: ClassVar[str] = (
        "Checks whether each sentence of a text (claim_field) is backed by a source: the "
        "item's own field (source_field), a fixed reference such as a policy (source_text), "
        "or both. Each sentence is judged supported / contradicted / not_mentioned and the "
        "item gets an overall verdict plus the list of problem sentences. Use it after any "
        "step that generated or extracted text, to catch made-up or wrong details. Keep the "
        "source short and on-topic (filter a long document with find first), and check text "
        "where each sentence states concrete facts the source could show (names, amounts, "
        "dates, promises); for a single extracted value set split=false."
    )
    Params: ClassVar[type[ToolParams]] = VerifyParams
    uses: ClassVar[Literal["jev", "jev+llm", "code"]] = "jev"

    def state(self, item: ItemState, params: VerifyParams) -> dict[str, Any]:
        """Only the source and the numbered claims, so Jev can refer to both by name."""
        source: Any
        if params.source_field is not None and params.source_text is not None:
            source = {
                "item_source": field_text(item, params.source_field),
                "reference": params.source_text,
            }
        elif params.source_field is not None:
            source = field_text(item, params.source_field)
        else:
            source = params.source_text
        return {"source": source, "claims": extract_claims(item, params)}

    def questions(self, item: ItemState, params: VerifyParams) -> dict[str, Question]:
        """One Choice per claim, keyed by the claim id the state uses."""
        return {
            cid: Choice(
                instructions=f"Using only source, is claim {cid} in claims supported?",
                options=dict(OPTIONS),
            )
            for cid in extract_claims(item, params)
        }

    async def _one(
        self, item: ItemState, params: Any, ctx: RunContext
    ) -> tuple[dict[str, Answer] | None, Exception | None]:
        # No claims: decide in code without a call. Too-large source: mark the item here
        # (the base class then records the skip) so the run carries on.
        if not extract_claims(item, params):
            self._code_decision(ctx, item, "no_claims")
            return {}, None
        answers, err = await super()._one(item, params, ctx)
        if isinstance(err, StateTooLarge):
            _write_code_verdict(item, params.output_field, "skipped_source_too_large")
            self._code_decision(ctx, item, "skipped_source_too_large")
        return answers, err

    def _code_decision(self, ctx: RunContext, item: ItemState, verdict: str) -> None:
        ctx.ledger.add(
            Decision(
                run_id=ctx.run_id,
                item_id=item.id,
                tool=self.name,
                question_id="verdict",
                qtype="choice",
                value=verdict,
                confidence=1.0,
                probabilities=None,
                decided_by="code",
            )
        )

    def apply(self, item: ItemState, answers: dict[str, Answer], params: VerifyParams) -> None:
        """Per-claim results, the overall verdict with provenance, and the problem claims."""
        out = params.output_field
        claims = extract_claims(item, params)
        if not answers:  # no claims, decided in `_one`
            _write_code_verdict(item, out, "no_claims")
            return
        results = [
            {
                "id": cid,
                "text": text,
                "verdict": str(answers[cid].value),
                "confidence": answers[cid].confidence,
                "by": answers[cid].decided_by,
            }
            for cid, text in claims.items()
        ]
        used = [answers[cid] for cid in claims]
        set_output(
            item,
            out,
            overall_verdict([r["verdict"] for r in results]),
            confidence=min(a.confidence for a in used),
            by="llm" if any(a.decided_by == "llm" for a in used) else "jev",
            low_confidence=any(a.low_confidence for a in used),
        )
        item.fields[f"{out}_claims"] = results
        item.fields[f"{out}_problem_claims"] = [
            r["text"] for r in results if r["verdict"] != "supported"
        ]

    def after_all(self, run: RunState, params: VerifyParams, report: ToolReport) -> None:
        """Count overall verdicts and point the planner at Find when sources were too big."""
        out = params.output_field
        verdicts = [i.fields[out] for i in select(run, params.where) if out in i.fields]
        counts = Counter(verdicts)
        run.aggregates[f"{out}_counts"] = {v: counts.get(v, 0) for v in VERDICTS}
        report.code_ops += counts["no_claims"] + counts["skipped_source_too_large"]
        if too_large := counts["skipped_source_too_large"]:
            report.notes.append(
                f"{too_large} items skipped because the source was too large for Jev; "
                "add a find step before verify to narrow the source to the relevant passage"
            )
