"""Classify: flat, tournament and taxonomy beam search, all offline on FakeJev/FakeLLM."""

import pytest
from pydantic import ValidationError

from foreman.core.context import make_context
from foreman.core.questions import Choice
from foreman.core.state import ItemState, RunState
from foreman.testing.fakes import FakeAnswer, FakeJev, FakeLLM
from foreman.tools import catalog, get_tool
from foreman.tools.classify import Classify, ClassifyParams, chunks

OPTIONS = {
    "bug": "App crashes, freezes or is broken",
    "feature_request": "Asks to add new functionality",
    "praise": "Love it, great, positive feedback",
}


def _run(texts: dict[str, str]) -> RunState:
    return RunState(run_id="r", job="j", items=[ItemState(id=k, text=v) for k, v in texts.items()])


def _by_id(run: RunState) -> dict[str, ItemState]:
    return {i.id: i for i in run.items}


def _choice(state, qid, q, probs: dict[str, dict[str, float]], conf: dict[str, float]):
    """Rule helper: answer question `qid` with the given distribution and confidence."""
    if qid not in probs:
        return None
    p = probs[qid]
    return FakeAnswer(max(p, key=p.__getitem__), confidence=conf.get(qid, 0.9), probabilities=p)


async def test_flat_keyword_default() -> None:
    ctx = await make_context(threshold=0.7, jev=FakeJev(), llm=FakeLLM())
    run = _run(
        {
            "a": "The app crashes every time I open the camera.",
            "b": "Since the update it crashes on launch.",
            "c": "Feature request: please add a dark mode.",
            "d": "I love it, great app.",
        }
    )
    report = await Classify().run(run, ClassifyParams(options=OPTIONS), ctx)

    items = _by_id(run)
    assert [items[k].fields["category"] for k in "abcd"] == [
        "bug",
        "bug",
        "feature_request",
        "praise",
    ]
    a = items["a"].fields
    assert a["category__by"] == "jev" and a["category__confidence"] == 0.9
    assert next(iter(a["category__probs"])) == "bug" and a["category__probs"]["bug"] == 0.8
    assert len(a["category__probs"]) == 3
    assert "category_path" not in a
    assert run.aggregates["category_counts"] == {"bug": 2, "feature_request": 1, "praise": 1}
    assert list(run.aggregates["category_counts"]) == ["bug", "feature_request", "praise"]
    assert report.jev_decisions == 4 and report.llm_decisions == 0
    # `other` is added last.
    q = ctx.jev.calls[0][1]["category"]
    assert isinstance(q, Choice) and list(q.options)[-1] == "other"


async def test_picks_other() -> None:
    def rule(state, qid, q):
        return FakeAnswer("other", confidence=0.95) if "order" in state else None

    ctx = await make_context(jev=FakeJev(rule), llm=FakeLLM())
    run = _run({"a": "My order arrived two weeks late.", "b": "I love it, great app."})
    await Classify().run(run, ClassifyParams(options=OPTIONS), ctx)
    assert _by_id(run)["a"].fields["category"] == "other"
    assert run.aggregates["category_counts"] == {"other": 1, "praise": 1}


async def test_add_other_off() -> None:
    ctx = await make_context(jev=FakeJev(), llm=FakeLLM())
    await Classify().run(_run({"a": "x"}), ClassifyParams(options=OPTIONS, add_other=False), ctx)
    assert "other" not in ctx.jev.calls[0][1]["category"].options


async def test_tournament_two_calls() -> None:
    options = {f"opt_{i:03d}": f"Option number {i}" for i in range(600)}

    def rule(state, qid, q):
        if "opt_437" in q.options:
            return FakeAnswer("opt_437", confidence=0.8 if qid.startswith("chunk") else 0.95)
        return FakeAnswer(next(iter(q.options)), confidence=0.9)

    jev = FakeJev(rule)
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    run = _run({"a": "something"})
    await Classify().run(run, ClassifyParams(options=options, output_field="code"), ctx)

    assert len(jev.calls) == 2
    first, second = (qs for _, qs in jev.calls)
    assert list(first) == ["chunk_0", "chunk_1", "chunk_2"]
    assert all(len(q.options) <= 200 for q in first.values())
    assert list(second) == ["final"]
    assert list(second["final"].options) == ["opt_000", "opt_200", "opt_437", "other"]
    f = run.items[0].fields
    assert f["code"] == "opt_437"
    assert f["code__confidence"] == 0.8  # min(final 0.95, winning chunk 0.8)
    assert f["code__by"] == "jev"


def test_chunks_even() -> None:
    sizes = [len(c) for c in chunks([str(i) for i in range(450)])]
    assert sizes == [150, 150, 150]
    assert [len(c) for c in chunks([str(i) for i in range(600)])] == [200, 200, 200]


TAXONOMY = {
    "hardware": {
        "description": "Physical device problems",
        "children": {
            "camera": {"children": {"crash": "Camera crashes", "blurry": "Blurry photos"}},
            "battery": {"children": {"drain": "Drains fast", "charging": "Will not charge"}},
        },
    },
    "software": {
        "description": "App and account problems",
        "children": {
            "login": {"children": {"password": "Password reset", "two_factor": "2FA codes"}},
            "sync": {"children": {"lost_data": "Data lost", "slow_sync": "Sync is slow"}},
        },
    },
}

# Greedy would follow hardware (0.55) -> camera (0.5) -> crash (0.5): 0.1375.
# The beam keeps software (0.40) -> login (0.95) -> password (0.9): 0.342.
TREE_PROBS = {
    "n_root": {"hardware": 0.55, "software": 0.40, "other": 0.05},
    "n_hardware": {"camera": 0.5, "battery": 0.45, "other": 0.05},
    "n_software": {"login": 0.95, "sync": 0.04, "other": 0.01},
    "n_hardware__camera": {"crash": 0.5, "blurry": 0.45, "other": 0.05},
    "n_hardware__battery": {"drain": 0.5, "charging": 0.45, "other": 0.05},
    "n_software__login": {"password": 0.9, "two_factor": 0.05, "other": 0.05},
}


async def test_taxonomy_beam_beats_greedy() -> None:
    conf = {"n_software": 0.8}
    jev = FakeJev(lambda s, qid, q: _choice(s, qid, q, TREE_PROBS, conf))
    ctx = await make_context(threshold=0.7, jev=jev, llm=FakeLLM())
    run = _run({"a": "I cannot reset my password"})
    params = ClassifyParams(taxonomy=TAXONOMY, beam_width=2)
    report = await Classify().run(run, params, ctx)

    f = run.items[0].fields
    assert f["category"] == "password"
    assert f["category_path"] == "software > login > password"
    assert f["category__confidence"] == 0.8  # min along the chosen path
    assert f["category__by"] == "jev"
    assert f["category__probs"] == {"password": 0.9, "two_factor": 0.05, "other": 0.05}

    depth = 3
    assert len(jev.calls) == depth  # one Jev call per level
    assert all(len(qs) <= params.beam_width for _, qs in jev.calls)
    assert list(jev.calls[1][1]) == ["n_hardware", "n_software"]
    assert sorted(jev.calls[2][1]) == ["n_hardware__camera", "n_software__login"]
    assert report.jev_decisions == 5
    assert run.aggregates["category_counts"] == {"password": 1}


async def test_taxonomy_other_ends_path() -> None:
    probs = {"n_root": {"hardware": 0.05, "software": 0.05, "other": 0.9}}
    jev = FakeJev(lambda s, qid, q: _choice(s, qid, q, probs, {}))
    ctx = await make_context(jev=jev, llm=FakeLLM())
    run = _run({"a": "Where is my parcel?"})
    await Classify().run(run, ClassifyParams(taxonomy=TAXONOMY), ctx)
    f = run.items[0].fields
    assert f["category"] == "other" and f["category_path"] == "other"
    assert len(jev.calls) == 1  # both runners-up were pruned below 0.1


async def test_low_confidence_falls_back_to_llm() -> None:
    jev = FakeJev(lambda *_: FakeAnswer("praise", confidence=0.4))
    llm = FakeLLM(lambda *_: FakeAnswer("bug"))
    ctx = await make_context(threshold=0.7, jev=jev, llm=llm)
    run = _run({"a": "It crashes."})
    report = await Classify().run(run, ClassifyParams(options=OPTIONS), ctx)
    f = run.items[0].fields
    assert f["category"] == "bug" and f["category__by"] == "llm"
    assert f["category__probs"] == {"bug": 1.0}
    assert report.llm_decisions == 1


async def test_taxonomy_llm_answer_counts_as_certain() -> None:
    # Level 1 is unsure, so the LLM picks software; hardware gets probability 0.
    probs = {k: v for k, v in TREE_PROBS.items() if k != "n_root"}

    def rule(s, qid, q):
        if qid == "n_root":
            return FakeAnswer("hardware", confidence=0.3)
        return _choice(s, qid, q, probs, {})

    jev = FakeJev(rule)
    llm = FakeLLM(lambda *_: FakeAnswer("software"))
    ctx = await make_context(threshold=0.7, jev=jev, llm=llm)
    run = _run({"a": "x"})
    await Classify().run(run, ClassifyParams(taxonomy=TAXONOMY), ctx)
    f = run.items[0].fields
    assert f["category_path"] == "software > login > password"
    assert f["category__by"] == "llm"
    assert list(jev.calls[1][1]) == ["n_software"]


async def test_failed_items_get_error_field() -> None:
    ctx = await make_context(jev=FakeJev(max_state_tokens=5), llm=FakeLLM())
    run = _run({"a": "short", "b": "x" * 400})
    report = await Classify().run(run, ClassifyParams(options=OPTIONS), ctx)
    items = _by_id(run)
    assert "category" in items["a"].fields
    assert "category" not in items["b"].fields and "category__error" in items["b"].fields
    assert any(n.startswith("b: skipped (StateTooLarge") for n in report.notes)
    assert run.aggregates["category_counts"] == {"bug": 1}


def test_validator() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        ClassifyParams(options=OPTIONS, taxonomy=TAXONOMY)
    with pytest.raises(ValidationError, match="exactly one"):
        ClassifyParams()
    with pytest.raises(ValidationError, match="empty category key"):
        ClassifyParams(options={"": "blank", "bug": None})
    with pytest.raises(ValidationError, match="empty category key"):
        ClassifyParams(taxonomy={"hardware": {"children": {" ": "blank"}}})
    with pytest.raises(ValidationError, match="repeated"):
        ClassifyParams(options={"Bug": None, "bug ": None})
    with pytest.raises(ValidationError, match="empty"):
        ClassifyParams(options={})
    with pytest.raises(ValidationError):
        ClassifyParams(options=OPTIONS, beam_width=5)
    deep: dict = {"leaf": None}
    for i in range(7):
        deep = {f"l{i}": {"children": deep}}
    with pytest.raises(ValidationError, match="deeper"):
        ClassifyParams(taxonomy=deep)


def test_registered() -> None:
    tool = get_tool("classify")
    assert isinstance(tool, Classify) and tool.uses == "jev"
    entry = next(e for e in catalog() if e["name"] == "classify")
    assert "taxonomy" in entry["description"] and "boundaries" in entry["description"]
