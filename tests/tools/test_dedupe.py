"""Dedupe tool: key normalization, candidates, clustering, canonical choice, conflicts, caps."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from foreman.core.context import make_context
from foreman.core.questions import Noul, Score
from foreman.core.state import Condition, ItemState, RunState
from foreman.testing.fakes import FakeAnswer, FakeJev, FakeLLM
from foreman.tools import catalog, get_tool
from foreman.tools import dedupe as dd
from foreman.tools.dedupe import DedupeParams, DedupeTool, build_key, candidate_pairs, normalize_key

SAMPLE = Path(__file__).parents[2] / "data" / "samples" / "dedupe.json"

VENDORS = [
    ("a", "ADOBE *CREATIVE CLD 800-833-6687", "345 Park Ave, San Jose"),
    ("b", "SQ *BLUE BOTTLE 0423", "66 Mint St"),
    ("c", "Adobe Systems Inc", "345 Park Avenue, San Jose, CA 95110"),
    ("d", "Blue Bottle Coffee", None),
    ("e", "Starbucks #05812", "1 Market St"),
    ("f", "Starbucks #11290", "2200 Mission College Blvd"),
    ("g", "Zoom Video Communications", "55 Almaden Blvd"),
]


def _run() -> RunState:
    items = [
        ItemState(id=i, text=f"{v} {a or ''}", fields={"vendor": v, "address": a})
        for i, v, a in VENDORS
    ]
    return RunState(run_id="r", job="j", items=items)


def _vendor_rule(levels: dict[frozenset[str], float], conflict: float = 0.1):
    """Same-entity level per pair of vendor names (default: clearly different)."""
    by_vendor = {v: i for i, v, _ in VENDORS}

    def rule(state, qid, q):
        pair = frozenset(by_vendor[state[r]["vendor"]] for r in ("record_a", "record_b"))
        if isinstance(q, Score):
            return FakeAnswer(levels.get(pair, 0.0), confidence=0.9)
        return FakeAnswer(conflict)

    return rule


async def _dedupe(run: RunState, rule, threshold: float = 0.7, **params):
    jev = FakeJev(rule)
    ctx = await make_context(threshold=threshold, jev=jev, llm=FakeLLM())
    p = DedupeParams(key_fields=["vendor"], compare_fields=["vendor", "address"], **params)
    report = await DedupeTool().run(run, p, ctx)
    return report, jev, ctx


def test_normalize_key() -> None:
    assert normalize_key("ADOBE *CREATIVE CLD 800-833-6687") == "adobe creative cld"
    assert normalize_key("SQ *BLUE BOTTLE 0423") == "sq blue bottle"
    assert normalize_key("  Joe's   Diner #12 ") == "joe s diner 12"  # 2-digit tokens stay
    assert normalize_key("ref_2026-09-30") == "ref 09 30"
    item = ItemState(id="x", text="Body", fields={"vendor": "Acme, Inc.", "city": None})
    assert build_key(item, ["vendor", "city", "text"]) == "acme inc body"


def test_candidates_find_expected_pairs() -> None:
    keys = [build_key(i, ["vendor"]) for i in _run().items]
    pairs = {(i, j) for i, j, _ in candidate_pairs(keys, 55)}
    assert (0, 2) in pairs  # Adobe
    assert (1, 3) in pairs  # Blue Bottle
    assert (4, 5) in pairs  # the two Starbucks
    assert not any(6 in p for p in pairs)  # Zoom matches nothing
    assert (0, 1) not in pairs
    scores = [s for _, _, s in candidate_pairs(keys, 55)]
    assert scores == sorted(scores, reverse=True)
    assert candidate_pairs(["", "", "acme"], 0) == []  # empty keys never pair


async def test_clusters_canonical_and_aggregates() -> None:
    levels = {frozenset("ac"): 4.0, frozenset("bd"): 3.6}
    run = _run()
    report, jev, _ = await _dedupe(run, _vendor_rule(levels))
    f = {i.id: i.fields for i in run.items}

    assert f["a"]["dup_group"] == f["c"]["dup_group"] == "dup-1"
    assert f["b"]["dup_group"] == f["d"]["dup_group"] == "dup-2"
    for k in "efg":
        assert f[k]["dup_group"] is None
        assert "dup_group__confidence" not in f[k] and "dup_group_canonical" not in f[k]
    # a and c both fill vendor+address -> tie -> earliest; d has no address -> b wins.
    assert f["a"]["dup_group_canonical"] is True and f["c"]["dup_group_canonical"] is False
    assert f["b"]["dup_group_canonical"] is True and f["d"]["dup_group_canonical"] is False
    assert f["a"]["dup_group__by"] == "jev" and f["a"]["dup_group__confidence"] == 0.9

    assert run.aggregates["dup_group_clusters"] == [
        {"group": "dup-1", "canonical_id": "a", "member_ids": ["a", "c"], "conflicts": []},
        {"group": "dup-2", "canonical_id": "b", "member_ids": ["b", "d"], "conflicts": []},
    ]
    assert report.items_in == 7 and report.items_out == 5
    assert "checked 3 candidate pairs" in report.notes
    assert "found 2 duplicate clusters" in report.notes
    assert len(jev.calls) == 3  # never n² pairs

    # The state carries only the compare fields of both records.
    state, qs = jev.calls[0]
    assert set(state) == {"record_a", "record_b"}
    assert set(state["record_a"]) == {"vendor", "address"}
    assert isinstance(qs["same"], Score) and len(qs["same"].levels) == 5


async def test_canonical_prefers_more_complete_record() -> None:
    run = _run()
    run.items[2].fields["email"] = "ap@adobe.com"  # c now has more filled fields than a
    await _dedupe(run, _vendor_rule({frozenset("ac"): 4.0}))
    assert run.aggregates["dup_group_clusters"][0]["canonical_id"] == "c"


async def test_below_threshold_pairs_do_not_cluster() -> None:
    # 2.8 / 4 = 0.7 < 0.75; Blue Bottle passes at exactly 0.75.
    run = _run()
    report, _, _ = await _dedupe(run, _vendor_rule({frozenset("ac"): 2.8, frozenset("bd"): 3.0}))
    f = {i.id: i.fields for i in run.items}
    assert f["a"]["dup_group"] is None and f["c"]["dup_group"] is None
    assert f["b"]["dup_group"] == "dup-1"
    assert report.items_out == 6


async def test_conflicts_recorded() -> None:
    run = _run()
    _, jev, _ = await _dedupe(run, _vendor_rule({frozenset("ef"): 3.2}, conflict=0.9))
    [cluster] = run.aggregates["dup_group_clusters"]
    assert cluster["member_ids"] == ["e", "f"] and cluster["conflicts"] == ["address"]

    asked = {frozenset(s[r]["vendor"] for r in ("record_a", "record_b")): q for s, q in jev.calls}
    starbucks = asked[frozenset({"Starbucks #05812", "Starbucks #11290"})]
    assert isinstance(starbucks["conflict_address"], Noul)
    assert starbucks["conflict_address"].fallback is False
    assert "conflict_vendor" not in starbucks  # both normalize to "starbucks": decided in code
    # Blue Bottle: d has no address, so no address question is asked.
    blue = asked[frozenset({"SQ *BLUE BOTTLE 0423", "Blue Bottle Coffee"})]
    assert "conflict_address" not in blue


async def test_conflicts_can_be_disabled() -> None:
    run = _run()
    _, jev, _ = await _dedupe(run, _vendor_rule({}), check_conflicts=False)
    assert all(set(q) == {"same"} for _, q in jev.calls)


async def test_llm_fallback_sets_by() -> None:
    def rule(state, qid, q):
        return FakeAnswer(4.0, confidence=0.3) if isinstance(q, Score) else FakeAnswer(0.0)

    run = _run()
    jev = FakeJev(rule)
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM(rule))
    await DedupeTool().run(run, DedupeParams(key_fields=["vendor"]), ctx)
    a = run.items[0].fields
    assert a["dup_group__by"] == "llm" and a["dup_group__confidence"] == 1.0


async def test_max_pairs_cap_adds_note() -> None:
    run = _run()
    report, jev, _ = await _dedupe(run, _vendor_rule({}), max_pairs=1)
    assert len(jev.calls) == 1
    assert any("only the 1 most similar" in n for n in report.notes)


async def test_failed_pair_is_skipped_with_note() -> None:
    run = _run()
    ctx = await make_context(threshold=0.7, jev=FakeJev(fail=True), llm=FakeLLM(fail=True))
    report = await DedupeTool().run(run, DedupeParams(key_fields=["vendor"]), ctx)
    assert all(i.fields["dup_group"] is None for i in run.items)
    assert sum("skipped" in n for n in report.notes) == 3


async def test_where_limits_items() -> None:
    run = _run()
    run.items[2].fields["skip"] = True  # c (Adobe Systems) is excluded
    p = DedupeParams(key_fields=["vendor"], where=Condition(field="skip", op="falsy"))
    ctx = await make_context(threshold=0.7, jev=FakeJev(_vendor_rule({})), llm=FakeLLM())
    report = await DedupeTool().run(run, p, ctx)
    assert "dup_group" not in run.items[2].fields
    assert any("6 of 7 items matched" in n for n in report.notes)


async def test_blocking_path_avoids_n_squared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dd, "CDIST_MAX_N", 50)
    sizes: list[int] = []
    real = dd.score_pairs

    def spy(keys, idx, cutoff):
        sizes.append(len(idx))
        return real(keys, idx, cutoff)

    monkeypatch.setattr(dd, "score_pairs", spy)
    # 300 items: 150 distinct names, each appearing twice; plus a token in every item that
    # would form one giant block if common tokens were not skipped.
    names = [f"{chr(97 + n // 26)}{chr(97 + n % 26)}x{n:03d}" for n in range(150)]
    items = [
        ItemState(id=f"i{k}", text="", fields={"vendor": f"{names[k % 150]} common"})
        for k in range(300)
    ]

    def rule(state, qid, q):
        same = state["record_a"]["vendor"] == state["record_b"]["vendor"]
        return FakeAnswer(4.0 if same else 0.0, confidence=0.9)

    run = RunState(run_id="r", job="j", items=items)
    ctx = await make_context(threshold=0.7, jev=FakeJev(rule), llm=FakeLLM())
    report = await DedupeTool().run(
        run, DedupeParams(key_fields=["vendor"], min_similarity=10), ctx
    )

    assert max(sizes) <= dd.MAX_BLOCK_SIZE
    assert sum(s * s for s in sizes) < 300 * 300 / 4
    assert len(run.aggregates["dup_group_clusters"]) == 150
    assert report.items_out == 150


def test_params_validation_and_catalog() -> None:
    p = DedupeParams()
    assert p.key_fields == ["text"] and p.shown_fields() == ["text"]
    assert p.conflict_fields() == []  # free text is never conflict-checked
    with pytest.raises(ValidationError):
        DedupeParams(compare_fields=[f"f{i}" for i in range(9)])
    with pytest.raises(ValidationError):
        DedupeParams(min_similarity=101)
    p = DedupeParams(compare_fields=["text", "a", "b", "c", "d", "e", "f"])
    assert p.conflict_fields() == ["a", "b", "c", "d", "e"]
    assert isinstance(get_tool("dedupe"), DedupeTool)
    entry = next(e for e in catalog() if e["name"] == "dedupe")
    assert entry["uses"] == "jev" and "key_fields" in entry["description"]


def test_sample_file_shape() -> None:
    data = json.loads(SAMPLE.read_text())
    assert 15 <= len(data["items"]) <= 25
    assert all({"vendor", "address"} <= set(i["fields"]) for i in data["items"])
