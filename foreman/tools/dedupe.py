"""Dedupe: entity resolution with cheap candidate pairs, Jev judgments and union-find.

Asking Jev about every pair of n items is n² calls, which is too slow and costly beyond a
few dozen items. So code proposes candidate pairs first (rapidfuzz `token_set_ratio` on a
normalized key, with token blocking once n is large), Jev judges only those pairs, and
union-find turns the matched pairs into clusters.

Union-find is transitive: if A~B and B~C match, A, B and C land in one cluster even when
A and C were never compared (or would not have matched). That chaining is acceptable for
this build; a stricter version would re-check clusters before merging.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import Field, field_validator
from rapidfuzz import fuzz, process

from foreman.config import get_settings
from foreman.core.concurrency import gather_bounded
from foreman.core.decide import DecisionError
from foreman.core.jev import StateTooLarge
from foreman.core.questions import Answer, Noul, Question, Score
from foreman.core.state import ItemState, RunState, set_output
from foreman.tools.base import MAX_ITEM_NOTES, Tool, ToolParams, ToolReport, select

if TYPE_CHECKING:
    from foreman.core.context import RunContext

try:  # rapidfuzz's cdist needs numpy; fall back to process.extract without it.
    import numpy as np
except ImportError:  # pragma: no cover - depends on the environment
    np = None

MAX_COMPARE_FIELDS = 8
MAX_CONFLICT_FIELDS = 5
MAX_VALUE_CHARS = 1500
CDIST_MAX_N = 3000  # above this, compare within token blocks only
BLOCK_PREFIX = 5  # block key = first 5 characters of a token
BLOCK_MIN_TOKEN = 3  # tokens shorter than this don't form blocks
MAX_BLOCK_SIZE = 200  # tokens this common ("inc", "store") say nothing about identity
CONFLICT_P = 0.5

SAME_INSTRUCTIONS = (
    "Do record_a and record_b describe the same real-world entity (the same person, company, "
    "product, account, or the same reported issue)? Formatting differences, abbreviations and "
    "typos do not make them different."
)
SAME_LEVELS = [
    "Clearly different entities",
    "Probably different entities",
    "Cannot tell from the information given",
    "Probably the same entity",
    "Clearly the same entity",
]

_PUNCT = re.compile(r"[^\w\s]|_")
_LONG_NUMBER = re.compile(r"^\d{3,}$")


class DedupeParams(ToolParams):
    """Which fields identify an entity, which ones Jev sees, and the match cutoffs."""

    key_fields: list[str] = Field(
        default_factory=lambda: ["text"],
        min_length=1,
        description='Identifying fields used to find candidate pairs ("text" = the item text).',
    )
    compare_fields: list[str] | None = Field(
        default=None,
        max_length=MAX_COMPARE_FIELDS,
        description="Fields shown to the judge for each pair. Defaults to key_fields; at most 8.",
    )
    min_similarity: int = Field(
        default=55, ge=0, le=100, description="Fuzzy-match cutoff (0-100) for candidate pairs."
    )
    max_pairs: int = Field(default=20000, ge=1, description="At most this many pairs are judged.")
    match_threshold: float = Field(
        default=0.75, ge=0, le=1, description="Cutoff on the 0-1 same-entity score."
    )
    check_conflicts: bool = Field(
        default=True, description="Also ask, per compare field, whether the values conflict."
    )
    output_field: str = "dup_group"

    @field_validator("key_fields", "compare_fields")
    @classmethod
    def _no_blank_fields(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any(not f.strip() for f in v):
            raise ValueError("field names must not be blank")
        return v

    def shown_fields(self) -> list[str]:
        """The fields Jev sees: `compare_fields`, else the first 8 key fields."""
        return list(self.compare_fields or self.key_fields[:MAX_COMPARE_FIELDS])

    def conflict_fields(self) -> list[str]:
        """Fields to check for conflicts. "text" is skipped: free text always differs."""
        if not self.check_conflicts:
            return []
        return [f for f in self.shown_fields() if f != "text"][:MAX_CONFLICT_FIELDS]


# --------------------------------------------------------------------------- keys + candidates


def field_value(item: ItemState, field: str) -> Any:
    """`"text"` is the item text; anything else is a field (None when missing)."""
    return item.text if field == "text" else item.fields.get(field)


def is_empty(v: Any) -> bool:
    """None, blank strings and empty containers count as no value."""
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, (list, dict, tuple, set)):
        return not v
    return False


def normalize_key(s: str) -> str:
    """Lowercase, punctuation to spaces, drop 3+ digit numbers (store ids, dates, phones).

    Long numbers vary between copies of the same entity ("SQ *BLUE BOTTLE 0423") and would
    otherwise drag fuzzy similarity down or make unrelated records look alike.
    """
    tokens = _PUNCT.sub(" ", s.lower()).split()
    return " ".join(t for t in tokens if not _LONG_NUMBER.match(t))


def build_key(item: ItemState, key_fields: list[str]) -> str:
    """The normalized identity string candidates are generated from."""
    parts = ["" if is_empty(v := field_value(item, f)) else str(v) for f in key_fields]
    return normalize_key(" | ".join(parts))


def score_pairs(keys: list[str], idx: list[int], cutoff: float) -> dict[tuple[int, int], float]:
    """Fuzzy-score every pair within `idx` (i < j), keeping those at or above `cutoff`."""
    sub = [keys[i] for i in idx]
    out: dict[tuple[int, int], float] = {}
    if np is not None:
        m = process.cdist(sub, sub, scorer=fuzz.token_set_ratio, score_cutoff=cutoff, workers=-1)
        rows, cols = np.nonzero(np.triu(m >= cutoff, k=1))
        for r, c in zip(rows.tolist(), cols.tolist(), strict=True):
            out[(idx[r], idx[c])] = float(m[r, c])
        return out
    for r, key in enumerate(sub[:-1]):
        hits = process.extract(
            key, sub[r + 1 :], scorer=fuzz.token_set_ratio, score_cutoff=cutoff, limit=None
        )
        for _, score, c in hits:
            out[(idx[r], idx[r + 1 + c])] = float(score)
    return out


def blocks(keys: list[str], idx: Iterable[int]) -> list[list[int]]:
    """Group indices sharing a token prefix, skipping blocks too common to be informative."""
    index: dict[str, list[int]] = defaultdict(list)
    for i in idx:
        for prefix in {t[:BLOCK_PREFIX] for t in keys[i].split() if len(t) >= BLOCK_MIN_TOKEN}:
            index[prefix].append(i)
    return [b for b in index.values() if 2 <= len(b) <= MAX_BLOCK_SIZE]


def candidate_pairs(keys: list[str], min_similarity: int) -> list[tuple[int, int, float]]:
    """Similar-looking pairs (i < j), most similar first; empty keys never pair."""
    idx = [i for i, k in enumerate(keys) if k]
    if len(idx) <= CDIST_MAX_N:
        scored = score_pairs(keys, idx, min_similarity)
    else:
        scored = {}
        for block in blocks(keys, idx):
            scored.update(score_pairs(keys, block, min_similarity))
    return sorted(((i, j, s) for (i, j), s in scored.items()), key=lambda p: (-p[2], p[0], p[1]))


# --------------------------------------------------------------------------- clustering


class UnionFind:
    """Disjoint sets over 0..n-1; the root of a set is its smallest index."""

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)

    def clusters(self) -> list[list[int]]:
        """Sets of size >= 2, each sorted, ordered by their earliest member."""
        groups: dict[int, list[int]] = defaultdict(list)
        for i in range(len(self.parent)):
            groups[self.find(i)].append(i)
        return sorted((g for g in groups.values() if len(g) > 1), key=lambda g: g[0])


def filled_count(item: ItemState) -> int:
    """Non-empty original fields; the most complete record becomes the canonical one."""
    return sum(1 for k, v in item.fields.items() if "__" not in k and not is_empty(v))


# --------------------------------------------------------------------------- the tool


class DedupeTool(Tool):
    """Entity resolution: fuzzy candidates in code, one Jev call per pair, union-find."""

    name: ClassVar[str] = "dedupe"
    description: ClassVar[str] = (
        "Finds items that describe the same real-world entity (the same customer, vendor, "
        "product, account, or the same bug reported twice), e.g. 'ADOBE *CREATIVE CLD' and "
        "'Adobe Systems Inc', and labels each cluster of duplicates with a group id and a "
        "canonical member. Set `key_fields` to the identifying fields (name, vendor, email, "
        "title), not dates, amounts or notes. `compare_fields` (default: key_fields) are what "
        "each pair is judged on: list fields whose values are written in the records, such as "
        "address or email, one field per entry. Use it before counting or totalling so one "
        "entity is not counted twice."
    )
    Params: ClassVar[type[ToolParams]] = DedupeParams
    uses: ClassVar[Literal["jev", "jev+llm", "code"]] = "jev"

    def state(self, a: ItemState, b: ItemState, params: DedupeParams) -> dict[str, Any]:
        """Only the compare fields of both records, each value capped at 1,500 characters."""

        def record(item: ItemState) -> dict[str, Any]:
            rec: dict[str, Any] = {}
            for f in params.shown_fields():
                v = field_value(item, f)
                rec[f] = v[:MAX_VALUE_CHARS] if isinstance(v, str) else v
            return rec

        return {"record_a": record(a), "record_b": record(b)}

    def questions(self, a: ItemState, b: ItemState, params: DedupeParams) -> dict[str, Question]:
        """The same-entity Score plus a conflict Noul per field both records actually fill.

        A conflict question is only asked when both values are present and differ after
        normalization; otherwise "no conflict" is already known, and asking Jev about a
        value the state doesn't contain only invites an unsure answer.
        """
        qs: dict[str, Question] = {"same": Score(SAME_INSTRUCTIONS, list(SAME_LEVELS))}
        for f in params.conflict_fields():
            va, vb = field_value(a, f), field_value(b, f)
            if is_empty(va) or is_empty(vb) or normalize_key(str(va)) == normalize_key(str(vb)):
                continue
            qs[f"conflict_{f}"] = Noul(
                f"Do record_a and record_b give genuinely conflicting values for {f} "
                "(not just different formatting or one being empty)?",
                fallback=False,
            )
        return qs

    async def _judge(
        self, a: ItemState, b: ItemState, params: DedupeParams, ctx: RunContext
    ) -> tuple[dict[str, Answer] | None, Exception | None]:
        # A failed pair is reported, not raised, so one pair can't fail the node.
        try:
            answers = await ctx.decider.decide(
                item_id=f"{a.id}|{b.id}",
                tool=self.name,
                state=self.state(a, b, params),
                questions=self.questions(a, b, params),
            )
        except (DecisionError, StateTooLarge) as e:
            return None, e
        return answers, None

    async def run(self, run: RunState, params: ToolParams, ctx: RunContext) -> ToolReport:
        """Propose pairs, judge them with bounded concurrency, cluster, write the outputs."""
        assert isinstance(params, DedupeParams)
        t0 = time.perf_counter()
        items_in = len(run.items)
        selected = select(run, params.where)
        report = ToolReport(tool=self.name, items_in=items_in, items_out=items_in)

        keys = [build_key(item, params.key_fields) for item in selected]
        pairs = candidate_pairs(keys, params.min_similarity)
        report.code_ops += 1
        if len(pairs) > params.max_pairs:
            report.notes.append(
                f"{len(pairs)} candidate pairs found; only the {params.max_pairs} most similar "
                "were checked (raise max_pairs or min_similarity)"
            )
            pairs = pairs[: params.max_pairs]

        def progress(done: int, total: int) -> None:
            if ctx.progress is not None:
                ctx.progress(self.name, done, total)

        results = await gather_bounded(
            [
                lambda i=i, j=j: self._judge(selected[i], selected[j], params, ctx)
                for i, j, _ in pairs
            ],
            limit=get_settings().jev_max_concurrency,
            on_done=progress,
        )

        uf = UnionFind(len(selected))
        edges: dict[int, list[Answer]] = defaultdict(list)  # item index -> its matched `same`
        conflicts: dict[int, set[str]] = defaultdict(set)  # item index -> conflicting fields
        errors: list[str] = []
        for (i, j, _), (answers, err) in zip(pairs, results, strict=True):
            if answers is None:
                errors.append(
                    f"{selected[i].id}|{selected[j].id}: skipped ({type(err).__name__}: {err})"
                )
                continue
            for a in answers.values():
                report.jev_decisions += a.decided_by == "jev"
                report.llm_decisions += a.decided_by == "llm"
                report.low_confidence += a.low_confidence
            same = answers["same"]
            if float(same.value) / (len(SAME_LEVELS) - 1) < params.match_threshold:
                continue
            uf.union(i, j)
            edges[i].append(same)
            edges[j].append(same)
            bad = {
                qid.removeprefix("conflict_")
                for qid, a in answers.items()
                if qid.startswith("conflict_") and float(a.value) >= CONFLICT_P
            }
            conflicts[i] |= bad
            conflicts[j] |= bad

        clusters = uf.clusters()
        report.code_ops += 1
        self._write(selected, clusters, edges, conflicts, run, params)

        report.notes.append(f"checked {len(pairs)} candidate pairs")
        report.notes.append(f"found {len(clusters)} duplicate clusters")
        report.notes.extend(errors[:MAX_ITEM_NOTES])
        if len(errors) > MAX_ITEM_NOTES:
            report.notes.append(f"... and {len(errors) - MAX_ITEM_NOTES} more pairs skipped")
        if len(selected) < items_in:
            report.notes.append(f"{len(selected)} of {items_in} items matched `where`")
        report.items_out = len(run.items) - sum(len(c) - 1 for c in clusters)
        report.duration_ms = int((time.perf_counter() - t0) * 1000)
        return report

    def _write(
        self,
        selected: list[ItemState],
        clusters: list[list[int]],
        edges: dict[int, list[Answer]],
        conflicts: dict[int, set[str]],
        run: RunState,
        params: DedupeParams,
    ) -> None:
        """Group ids, canonical flags and provenance on items; cluster list in aggregates."""
        out = params.output_field
        canonical_counts = [filled_count(item) for item in selected]  # before outputs exist
        clustered: set[int] = set()
        summary: list[dict[str, Any]] = []
        for n, members in enumerate(clusters, start=1):
            group = f"dup-{n}"
            canonical = max(members, key=lambda m: (canonical_counts[m], -m))
            for m in members:
                ans = edges[m]
                set_output(
                    selected[m],
                    out,
                    group,
                    confidence=min(a.confidence for a in ans),
                    by="llm" if any(a.decided_by == "llm" for a in ans) else "jev",
                    low_confidence=any(a.low_confidence for a in ans),
                )
                selected[m].fields[f"{out}_canonical"] = m == canonical
            clustered.update(members)
            summary.append(
                {
                    "group": group,
                    "canonical_id": selected[canonical].id,
                    "member_ids": [selected[m].id for m in members],
                    "conflicts": sorted(set().union(*(conflicts[m] for m in members))),
                }
            )
        for i, item in enumerate(selected):
            if i not in clustered:
                item.fields[out] = None
        run.aggregates[f"{out}_clusters"] = summary
