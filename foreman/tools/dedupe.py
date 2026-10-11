"""Dedupe: entity resolution with cheap candidate pairs, Jev judgments and union-find.

Asking Jev about every pair of n items is n² calls, which is too slow and costly beyond a
few dozen items. So code proposes candidate pairs first (rapidfuzz `token_set_ratio` on a
normalized key, with token blocking once n is large), Jev judges only those pairs, and
union-find turns the matched pairs into clusters.

Union-find is transitive: if A~B and B~C match, A, B and C land in one cluster even when
A and C were never compared (or would not have matched). That chaining is acceptable for
this build; a stricter version would re-check clusters before merging.

Two modes. "records" asks whether two structured records are the same real-world entity.
"issues" is for free-text reports (reviews, tickets): "the same entity" is too vague for
those, so it asks whether both texts report the same feature failing in the same way, and
separately whether text_a reports a problem at all, so praise and feature requests never
merge. Free text shares too many words for fuzzy matching alone to filter pairs, so
`block_by` restricts comparisons to items with the same value in a field (e.g. a theme or
category written by an earlier node).
"""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from pydantic import Field, field_validator, model_validator
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

ISSUE_INSTRUCTIONS = (
    "Do text_a and text_b report the same specific problem: the same feature failing in the "
    "same way? Different devices or wording do not matter; a different feature or a different "
    "failure does."
)
ISSUE_LEVELS = [
    "Different features or different problems",
    "Same feature, different problem",
    "Probably the same problem",
    "Clearly the same problem",
]
ISSUE_MATCH_LEVEL = 2.0  # expected level at or above "Probably the same problem"
PROBLEM_INSTRUCTIONS = "Does text_a report a problem with the app?"
PROBLEM_P = 0.5
RECORDS_MIN_SIMILARITY = 55
ISSUES_MIN_SIMILARITY = 50

_PUNCT = re.compile(r"[^\w\s]|_")
_LONG_NUMBER = re.compile(r"^\d{3,}$")


class DedupeParams(ToolParams):
    """Which fields identify an entity, which ones Jev sees, and the match cutoffs."""

    mode: Literal["records", "issues"] = Field(
        default="records",
        description='"records": structured records of the same entity (vendors, customers). '
        '"issues": free-text reports (reviews, tickets) of the same specific problem.',
    )
    block_by: str | None = Field(
        default=None,
        description="Only compare items with the same value in this field (e.g. a theme or "
        "category from an earlier node). Items without a value are not compared.",
    )

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
        default=RECORDS_MIN_SIMILARITY,
        ge=0,
        le=100,
        description="Fuzzy-match cutoff (0-100) for candidate pairs; 50 in issues mode unless set.",
    )
    max_pairs: int = Field(default=20000, ge=1, description="At most this many pairs are judged.")
    match_threshold: float = Field(
        default=0.75,
        ge=0,
        le=1,
        description="Records mode: cutoff on the 0-1 same-entity score. Issues mode matches "
        "at the top two levels instead.",
    )
    check_conflicts: bool = Field(
        default=True,
        description="Records mode: also ask, per compare field, whether the values conflict.",
    )
    output_field: str = "dup_group"

    @field_validator("key_fields", "compare_fields")
    @classmethod
    def _no_blank_fields(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and any(not f.strip() for f in v):
            raise ValueError("field names must not be blank")
        return v

    @model_validator(mode="after")
    def _issues_similarity(self) -> DedupeParams:
        # Issue reports reword the same problem more than records do, so cast a wider net.
        if self.mode == "issues" and "min_similarity" not in self.model_fields_set:
            self.min_similarity = ISSUES_MIN_SIMILARITY
        return self

    def shown_fields(self) -> list[str]:
        """The fields Jev sees: `compare_fields`, else the first 8 key fields."""
        return list(self.compare_fields or self.key_fields[:MAX_COMPARE_FIELDS])

    def conflict_fields(self) -> list[str]:
        """Fields to check for conflicts. "text" is skipped: free text always differs."""
        if not self.check_conflicts or self.mode == "issues":
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


def candidate_pairs(
    keys: list[str], min_similarity: int, within: Iterable[int] | None = None
) -> list[tuple[int, int, float]]:
    """Similar-looking pairs (i < j), most similar first; empty keys never pair.

    `within` limits the search to those indices (one `block_by` group).
    """
    idx = [i for i in (range(len(keys)) if within is None else within) if keys[i]]
    if len(idx) <= CDIST_MAX_N:
        scored = score_pairs(keys, idx, min_similarity)
    else:
        scored = {}
        for block in blocks(keys, idx):
            scored.update(score_pairs(keys, block, min_similarity))
    return sorted(((i, j, s) for (i, j), s in scored.items()), key=lambda p: (-p[2], p[0], p[1]))


def block_by_groups(items: list[ItemState], field: str | None) -> tuple[list[list[int]], int]:
    """Indices grouped by their `field` value, and how many items had no value to group by."""
    if field is None:
        return [list(range(len(items)))], 0
    groups: dict[str, list[int]] = defaultdict(list)
    missing = 0
    for i, item in enumerate(items):
        v = field_value(item, field)
        if is_empty(v):
            missing += 1
        else:
            groups[json.dumps(v, sort_keys=True, default=str)].append(i)
    return list(groups.values()), missing


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
        "address or email, one field per entry. For free-text reports (reviews, tickets, bug "
        'reports) set `mode: "issues"`: it merges only texts reporting the same feature '
        "failing in the same way, never praise or feature requests. In issues mode always set "
        "`block_by` to a category or theme field written by an earlier classify or group node, "
        "so only reports in the same category are compared. Use it before counting or "
        "totalling so one entity is not counted twice."
    )
    Params: ClassVar[type[ToolParams]] = DedupeParams
    uses: ClassVar[Literal["jev", "jev+llm", "code"]] = "jev"

    def state(self, a: ItemState, b: ItemState, params: DedupeParams) -> dict[str, Any]:
        """Only the compare fields of both records, each value capped at 1,500 characters.

        Issues mode sends the two texts under `text_a` / `text_b`, the names its questions use.
        """
        if params.mode == "issues":
            return {"text_a": self._text(a, params), "text_b": self._text(b, params)}

        def record(item: ItemState) -> dict[str, Any]:
            rec: dict[str, Any] = {}
            for f in params.shown_fields():
                v = field_value(item, f)
                rec[f] = v[:MAX_VALUE_CHARS] if isinstance(v, str) else v
            return rec

        return {"record_a": record(a), "record_b": record(b)}

    def _text(self, item: ItemState, params: DedupeParams) -> str:
        values = [field_value(item, f) for f in params.shown_fields()]
        return " | ".join(str(v) for v in values if not is_empty(v))[:MAX_VALUE_CHARS]

    def questions(self, a: ItemState, b: ItemState, params: DedupeParams) -> dict[str, Question]:
        """The same-entity Score plus a conflict Noul per field both records actually fill.

        A conflict question is only asked when both values are present and differ after
        normalization; otherwise "no conflict" is already known, and asking Jev about a
        value the state doesn't contain only invites an unsure answer.

        Issues mode asks the same-problem Score and whether text_a reports a problem at all.
        """
        if params.mode == "issues":
            return {
                "same": Score(ISSUE_INSTRUCTIONS, list(ISSUE_LEVELS)),
                "is_problem": Noul(PROBLEM_INSTRUCTIONS),
            }
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
        groups, unblocked = block_by_groups(selected, params.block_by)
        pairs = sorted(
            (p for g in groups for p in candidate_pairs(keys, params.min_similarity, g)),
            key=lambda p: (-p[2], p[0], p[1]),
        )
        report.code_ops += 1
        if unblocked:
            report.notes.append(
                f"{unblocked} items have no `{params.block_by}` value and were not compared"
            )
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
        edges: dict[int, list[Answer]] = defaultdict(list)  # item -> answers its matches used
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
            if not self._matches(answers, params):
                continue
            uf.union(i, j)
            used = [answers["same"]] + ([answers["is_problem"]] if "is_problem" in answers else [])
            edges[i].extend(used)
            edges[j].extend(used)
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

    def _matches(self, answers: dict[str, Answer], params: DedupeParams) -> bool:
        """Records: normalized same-entity score vs the threshold. Issues: top two levels
        and text_a is a problem (p >= 0.5)."""
        same = float(answers["same"].value)
        if params.mode == "issues":
            return same >= ISSUE_MATCH_LEVEL and float(answers["is_problem"].value) >= PROBLEM_P
        return same / (len(SAME_LEVELS) - 1) >= params.match_threshold

    def _write(
        self,
        selected: list[ItemState],
        clusters: list[list[int]],
        edges: dict[int, list[Answer]],
        conflicts: dict[int, set[str]],
        run: RunState,
        params: DedupeParams,
    ) -> None:
        """Group ids, canonical flags and provenance on items; cluster list in aggregates.

        A member's confidence is the minimum over the answers its matched pairs relied on.
        """
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
