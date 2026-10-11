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
    for k in "efg":  # singletons: no group, but each is the one copy of itself
        assert f[k]["dup_group"] is None and "dup_group__confidence" not in f[k]
        assert f[k]["dup_group_canonical"] is True and f[k]["dup_group_count"] == 1
    # a and c both fill vendor+address -> tie -> earliest; d has no address -> b wins.
    assert f["a"]["dup_group_canonical"] is True and f["c"]["dup_group_canonical"] is False
    assert f["b"]["dup_group_canonical"] is True and f["d"]["dup_group_canonical"] is False
    assert [f[k]["dup_group_count"] for k in "abcd"] == [2, 2, 2, 2]
    assert f["a"]["dup_group__by"] == "jev" and f["a"]["dup_group__confidence"] == 0.9

    assert run.aggregates["dup_group_clusters"] == [
        {"group": "dup-1", "canonical_id": "a", "member_ids": ["a", "c"], "conflicts": []},
        {"group": "dup-2", "canonical_id": "b", "member_ids": ["b", "d"], "conflicts": []},
    ]
    assert report.items_in == report.items_out == 7  # duplicates are labelled, not removed
    assert "5 unique of 7 items" in report.notes
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
    assert report.items_out == 7 and "6 unique of 7 items" in report.notes


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
    assert "dup_group_canonical" not in run.items[2].fields  # only processed items are marked
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
    assert report.items_out == 300 and "150 unique of 300 items" in report.notes


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


# --------------------------------------------------------------------------- issues mode

REPORTS = {
    "cam1": ("App crashes when I open the camera on my Pixel 8.", "camera"),
    "cam2": ("Camera crashes the whole app on Pixel 8 since the update.", "camera"),
    "freeze": ("App freezes on the loading screen on iPhone 13.", "stability"),
    "love1": ("Love this app, the new editor is fast.", "praise"),
    "love2": ("Love this app, the filters look great.", "praise"),
}


def _reports(themes: bool = True) -> RunState:
    items = [
        ItemState(id=k, text=t, fields={"theme": th} if themes else {})
        for k, (t, th) in REPORTS.items()
    ]
    return RunState(run_id="r", job="j", items=items)


def _issue_rule(levels: dict[frozenset[str], float], problem: dict[str, float] | None = None):
    """Same-problem level per pair of report ids; is_problem P(yes) per text_a id."""
    by_text = {t: k for k, (t, _) in REPORTS.items()}
    problem = problem or {}

    def rule(state, qid, q):
        a, b = (by_text[state[k].split(" | ")[0]] for k in ("text_a", "text_b"))
        if qid == "same":
            return FakeAnswer(levels.get(frozenset((a, b)), 0.0), confidence=0.9)
        return FakeAnswer(problem.get(a, 0.95))

    return rule


async def _issues(run: RunState, rule, **params):
    jev = FakeJev(rule)
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    report = await DedupeTool().run(run, DedupeParams(mode="issues", **params), ctx)
    return report, jev


async def test_issues_mode_questions_and_state() -> None:
    run = _reports()
    _, jev = await _issues(run, _issue_rule({}), min_similarity=0, compare_fields=["text", "theme"])
    state, qs = jev.calls[0]
    assert set(state) == {"text_a", "text_b"}
    assert set(qs) == {"same", "is_problem"}  # no conflict checks in issues mode
    assert qs["same"].instructions == dd.ISSUE_INSTRUCTIONS
    assert qs["same"].levels[0] == "Different features or different problems"
    assert qs["same"].levels[-1] == "Clearly the same problem" and len(qs["same"].levels) == 4
    assert isinstance(qs["is_problem"], Noul)
    assert qs["is_problem"].instructions == "Does text_a report a problem with the app?"
    assert " | " in state["text_a"]  # text and theme joined


async def test_issues_match_only_top_two_levels() -> None:
    levels = {frozenset(("cam1", "cam2")): 2.0, frozenset(("cam1", "freeze")): 1.9}
    run = _reports()
    report, _ = await _issues(run, _issue_rule(levels), min_similarity=0)
    f = {i.id: i.fields for i in run.items}
    assert f["cam1"]["dup_group"] == f["cam2"]["dup_group"] == "dup-1"
    assert f["freeze"]["dup_group"] is None
    assert f["cam1"]["dup_group__confidence"] == pytest.approx(0.9)  # min(same, is_problem)
    assert report.items_out == report.items_in


async def test_issues_praise_never_merges() -> None:
    levels = {frozenset(("love1", "love2")): 3.0, frozenset(("cam1", "cam2")): 3.0}
    run = _reports()
    await _issues(run, _issue_rule(levels, problem={"love1": 0.1, "love2": 0.1}), min_similarity=0)
    f = {i.id: i.fields for i in run.items}
    assert f["love1"]["dup_group"] is None and f["love2"]["dup_group"] is None
    assert f["cam1"]["dup_group"] == "dup-1"


def test_issues_min_similarity_default() -> None:
    assert DedupeParams().min_similarity == 55
    assert DedupeParams(mode="issues").min_similarity == 50
    assert DedupeParams(mode="issues", min_similarity=70).min_similarity == 70
    assert DedupeParams(mode="issues", min_similarity=55).min_similarity == 55
    assert DedupeParams.model_validate_json('{"mode": "issues"}').min_similarity == 50


async def test_block_by_compares_within_groups_only() -> None:
    run = _reports()
    run.items[2].fields.pop("theme")  # freeze has no theme: never compared
    report, jev = await _issues(run, _issue_rule({}), min_similarity=0, block_by="theme")
    compared = {frozenset((s["text_a"], s["text_b"])) for s, _ in jev.calls}
    text = {k: t for k, (t, _) in REPORTS.items()}
    assert compared == {
        frozenset((text["cam1"], text["cam2"])),
        frozenset((text["love1"], text["love2"])),
    }
    assert any("1 items have no `theme` value" in n for n in report.notes)
    assert run.items[2].fields["dup_group"] is None


async def test_block_by_in_records_mode() -> None:
    run = _run()
    for item in run.items:
        item.fields["city"] = "SJ" if item.id in "ac" else "SF"
    _, jev, _ = await _dedupe(run, _vendor_rule({}), block_by="city", min_similarity=0)
    pairs = {frozenset(s[r]["vendor"] for r in ("record_a", "record_b")) for s, _ in jev.calls}
    assert len(pairs) == 1 + 10  # a-c, plus all 5 SF records pairwise


def test_description_mentions_issues_and_block_by() -> None:
    desc = DedupeTool.description
    assert '"issues"' in desc and "always set `block_by`" in desc


# --------------------------------------------------------------------------- no chaining, top-k, skip

ACME = [("x", "Acme Corp"), ("y", "Acme Corporation"), ("z", "Acme Corporation Ltd")]


def _acme(extra_fields: dict[str, dict] | None = None) -> RunState:
    extra_fields = extra_fields or {}
    items = [
        ItemState(id=k, text=v, fields={"vendor": v, **extra_fields.get(k, {})}) for k, v in ACME
    ]
    return RunState(run_id="r", job="j", items=items)


def _acme_rule(levels: dict[frozenset[str], float]):
    by_vendor = {v: k for k, v in ACME}

    def rule(state, qid, q):
        pair = frozenset(by_vendor[state[r]["vendor"]] for r in ("record_a", "record_b"))
        return FakeAnswer(levels.get(pair, 0.0), confidence=0.9)

    return rule


async def _acme_dedupe(run: RunState, levels: dict[frozenset[str], float]):
    ctx = await make_context(threshold=0.7, jev=FakeJev(_acme_rule(levels)), llm=FakeLLM())
    params = DedupeParams(key_fields=["vendor"], min_similarity=0, check_conflicts=False)
    report = await DedupeTool().run(run, params, ctx)
    return report, {i.id: i.fields for i in run.items}


async def test_no_transitive_chaining() -> None:
    # x~y and y~z match, x and z do not: z must not be pulled into x's cluster through y.
    levels = {frozenset("xy"): 4.0, frozenset("yz"): 3.6, frozenset("xz"): 0.0}
    report, f = await _acme_dedupe(_acme(), levels)
    assert f["x"]["dup_group"] == f["y"]["dup_group"] == "dup-1"
    assert f["x"]["dup_group_canonical"] is True and f["y"]["dup_group_canonical"] is False
    assert f["x"]["dup_group_count"] == f["y"]["dup_group_count"] == 2
    assert f["z"]["dup_group"] is None
    assert f["z"]["dup_group_canonical"] is True and f["z"]["dup_group_count"] == 1
    assert "1 matched pairs not merged" in " ".join(report.notes)
    assert "2 unique of 3 items" in report.notes


async def test_member_joins_through_the_canonical() -> None:
    # Same matches, but y is the most complete record: it founds the cluster as canonical,
    # and z matched y directly, so z joins. Every member matched the canonical itself.
    levels = {frozenset("xy"): 4.0, frozenset("yz"): 3.6, frozenset("xz"): 0.0}
    run = _acme({"y": {"email": "ap@acme.com"}})
    _, f = await _acme_dedupe(run, levels)
    assert f["x"]["dup_group"] == f["y"]["dup_group"] == f["z"]["dup_group"] == "dup-1"
    assert [f[k]["dup_group_canonical"] for k in "xyz"] == [False, True, False]
    assert all(f[k]["dup_group_count"] == 3 for k in "xyz")
    assert run.aggregates["dup_group_clusters"][0]["canonical_id"] == "y"


async def test_strongest_match_founds_the_cluster() -> None:
    # y~z is the strongest match, so y and z cluster first (y canonical, as the earlier of
    # two equally complete records); x matched y more weakly and joins through y.
    levels = {frozenset("yz"): 4.0, frozenset("xy"): 3.2, frozenset("xz"): 3.2}
    run = _acme()
    await _acme_dedupe(run, levels)
    [cluster] = run.aggregates["dup_group_clusters"]
    assert cluster["canonical_id"] == "y" and cluster["member_ids"] == ["x", "y", "z"]


def test_top_neighbors_keeps_k_per_item() -> None:
    pairs = [(0, 1, 99.0), (0, 2, 90.0), (0, 3, 80.0), (1, 2, 70.0), (2, 3, 60.0)]
    kept = dd.top_neighbors(pairs, 1)
    # 0's best is 1, 1's best is 0, 2's best is 0, 3's best is 0.
    assert kept == [(0, 1, 99.0), (0, 2, 90.0), (0, 3, 80.0)]
    assert dd.top_neighbors(pairs, None) == pairs
    # k=2: 2's top two are 0 and 1, but (2, 3) stays because it is in 3's top two.
    assert dd.top_neighbors(pairs, 2) == pairs
    assert dd.top_neighbors(pairs + [(1, 3, 10.0)], 2) == pairs  # 10.0 is nobody's top two


async def test_max_neighbors_bounds_pairs_per_item() -> None:
    items = [ItemState(id=f"i{k}", text="", fields={"vendor": f"acme store {k}"}) for k in range(8)]

    async def judged(**params) -> tuple[int, list[str]]:
        run = RunState(run_id="r", job="j", items=[i.model_copy(deep=True) for i in items])
        jev = FakeJev(lambda s, qid, q: FakeAnswer(0.0, confidence=0.9))
        ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
        p = DedupeParams(key_fields=["vendor"], min_similarity=0, check_conflicts=False, **params)
        report = await DedupeTool().run(run, p, ctx)
        return len(jev.calls), report.notes

    n_all, _ = await judged(max_neighbors=None)
    n_top, notes = await judged(max_neighbors=2)
    assert n_all == 28  # every pair of 8
    assert n_top <= 8 * 2 and n_top < n_all
    assert any(f"{n_top} of 28 similar pairs" in n for n in notes)
    assert DedupeParams().max_neighbors == 5


async def test_skip_blocks_are_not_deduped() -> None:
    run = _reports()
    for k in ("love1", "love2"):
        run.items[list(REPORTS).index(k)].fields["theme"] = dd.NO_THEME
    levels = {frozenset(("love1", "love2")): 3.0, frozenset(("cam1", "cam2")): 3.0}
    report, jev = await _issues(run, _issue_rule(levels), min_similarity=0, block_by="theme")
    text = {k: t for k, (t, _) in REPORTS.items()}
    compared = {frozenset((s["text_a"], s["text_b"])) for s, _ in jev.calls}
    assert compared == {frozenset((text["cam1"], text["cam2"]))}
    f = {i.id: i.fields for i in run.items}
    for k in ("love1", "love2"):  # processed singletons, so rank's canonical filter keeps them
        assert f[k]["dup_group"] is None
        assert f[k]["dup_group_canonical"] is True and f[k]["dup_group_count"] == 1
    assert any("2 items in none_of_these were not deduped" in n for n in report.notes)

    run = _reports()
    for k in ("love1", "love2"):
        run.items[list(REPORTS).index(k)].fields["theme"] = dd.NO_THEME
    _, jev = await _issues(
        run, _issue_rule(levels), min_similarity=0, block_by="theme", skip_blocks=[]
    )
    assert len(jev.calls) == 2  # now the none_of_these pair is compared too
