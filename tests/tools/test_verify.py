"""Verify tool: sentence splitting, one Jev call per item, verdict logic, sources, skips."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from foreman import dev
from foreman.core.context import make_context
from foreman.core.jev import StateTooLarge
from foreman.core.questions import Choice
from foreman.core.state import ItemState, RunState
from foreman.testing.fakes import FakeAnswer, FakeJev, FakeLLM
from foreman.tools import catalog, get_tool
from foreman.tools.verify import (
    OPTIONS,
    VerifyParams,
    VerifyTool,
    extract_claims,
    overall_verdict,
    split_sentences,
)

SAMPLE = Path(__file__).resolve().parents[2] / "data" / "samples" / "verify.json"

# --------------------------------------------------------------------------- splitter


def test_split_basic_punctuation() -> None:
    text = "We refunded you. Is that okay? Great!\nThanks again."
    assert split_sentences(text) == ["We refunded you.", "Is that okay?", "Great!", "Thanks again."]


def test_split_keeps_abbreviations() -> None:
    text = (
        "Dr. Smith and Mr. Lee approved it, e.g. the refund. "
        "We ship in the U.S. and the U.K. only. Prices exclude tax, i.e. VAT."
    )
    assert split_sentences(text) == [
        "Dr. Smith and Mr. Lee approved it, e.g. the refund.",
        "We ship in the U.S. and the U.K. only.",
        "Prices exclude tax, i.e. VAT.",
    ]


def test_split_bullets_and_numbered_lines() -> None:
    text = (
        "Here is what we did:\n"
        "- Refunded the $12 charge. It shows in 3 days.\n"
        "* Cancelled the duplicate order\n"
        "• Sent a new jar\n"
        "1. Logged a bug\n"
        "2) Closed the ticket"
    )
    assert split_sentences(text) == [
        "Here is what we did:",
        "Refunded the $12 charge.",
        "It shows in 3 days.",
        "Cancelled the duplicate order",
        "Sent a new jar",
        "Logged a bug",
        "Closed the ticket",
    ]


def test_split_blank_lines_quotes_and_decimals() -> None:
    text = 'Hi Sam,\n\nYou paid $89.99 for it. He said "it works." Then it broke.'
    assert split_sentences(text) == [
        "Hi Sam,",
        "You paid $89.99 for it.",
        'He said "it works."',
        "Then it broke.",
    ]


def test_extract_claims_min_words_max_claims_and_ids() -> None:
    item = ItemState(
        id="x",
        text="src",
        fields={"reply": "Hi. We refunded the full amount. Thanks! Your new jar ships today."},
    )
    claims = extract_claims(item, VerifyParams(claim_field="reply"))
    assert claims == {"c1": "We refunded the full amount.", "c2": "Your new jar ships today."}
    capped = extract_claims(item, VerifyParams(claim_field="reply", max_claims=1, min_words=1))
    assert capped == {"c1": "Hi."}


def test_extract_claims_without_split_keeps_short_values() -> None:
    item = ItemState(id="x", text="Total due: $45.00", fields={"amount": 45.0})
    assert extract_claims(item, VerifyParams(claim_field="amount", split=False)) == {"c1": "45.0"}


# --------------------------------------------------------------------------- tool runs


def _run(replies: dict[str, str | None], text: str = "The customer message.") -> RunState:
    return RunState(
        run_id="r",
        job="j",
        items=[ItemState(id=k, text=text, fields={"reply": v}) for k, v in replies.items()],
    )


def _rule(confidence: float = 0.9, low_on: str | None = None):
    """Verdict from a keyword in the claim: 'invented' -> not_mentioned, 'wrong' -> contradicted."""

    def rule(state, qid, _q):
        claim = state["claims"][qid]
        if "wrong" in claim:
            verdict = "contradicted"
        elif "invented" in claim:
            verdict = "not_mentioned"
        else:
            verdict = "supported"
        conf = 0.2 if low_on is not None and low_on in claim else confidence
        return FakeAnswer(verdict, confidence=conf)

    return rule


async def _verify(run: RunState, jev: FakeJev, llm: FakeLLM | None = None, **params):
    ctx = await make_context(threshold=0.7, jev=jev, llm=llm or FakeLLM())
    report = await VerifyTool().run(run, VerifyParams(claim_field="reply", **params), ctx)
    return report, ctx


async def test_one_jev_call_per_item_one_question_per_claim() -> None:
    jev = FakeJev(_rule())
    run = _run(
        {
            "a": "We sent the new jar today. It ships by courier tomorrow.",
            "b": "Your refund is done now.",
        }
    )
    await _verify(run, jev)

    assert len(jev.calls) == 2
    by_claims = {len(q): (s, q) for s, q in jev.calls}
    state, questions = by_claims[2]
    assert set(questions) == {"c1", "c2"} == set(state["claims"])
    for cid, q in questions.items():
        assert isinstance(q, Choice)
        assert q.instructions == f"Using only source, is claim {cid} in claims supported?"
        assert q.options == OPTIONS
    assert list(OPTIONS) == ["supported", "contradicted", "not_mentioned"]
    assert state == {
        "source": "The customer message.",
        "claims": {"c1": "We sent the new jar today.", "c2": "It ships by courier tomorrow."},
    }


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        (["supported", "supported"], "supported"),
        (["supported", "not_mentioned"], "partially_supported"),
        (["not_mentioned", "not_mentioned"], "unsupported"),
        (["not_mentioned"], "unsupported"),
        (["not_mentioned", "contradicted"], "contradicted"),
        (["supported", "contradicted", "not_mentioned"], "contradicted"),
        ([], "no_claims"),
    ],
)
def test_overall_verdict(verdicts, expected) -> None:
    assert overall_verdict(verdicts) == expected


async def test_verdicts_claims_and_problem_claims() -> None:
    run = _run(
        {
            "good": "We refunded your order today. The jar ships tomorrow morning.",
            "partial": "We refunded your order today. You get an invented free month.",
            "bad": "We refunded the wrong amount here. You get an invented free month.",
            "made_up": "You get an invented free month. An invented gift card ships too.",
        }
    )
    report, _ = await _verify(run, FakeJev(_rule()))

    f = {i.id: i.fields for i in run.items}
    assert f["good"]["verified"] == "supported"
    assert f["good"]["verified_problem_claims"] == []
    assert f["partial"]["verified"] == "partially_supported"
    assert f["partial"]["verified_problem_claims"] == ["You get an invented free month."]
    assert f["bad"]["verified"] == "contradicted"
    assert f["bad"]["verified_problem_claims"] == [
        "We refunded the wrong amount here.",
        "You get an invented free month.",
    ]
    assert f["made_up"]["verified"] == "unsupported"
    assert f["made_up"]["verified_problem_claims"] == [
        "You get an invented free month.",
        "An invented gift card ships too.",
    ]
    assert f["bad"]["verified_claims"][0] == {
        "id": "c1",
        "text": "We refunded the wrong amount here.",
        "verdict": "contradicted",
        "confidence": 0.9,
        "by": "jev",
    }
    assert f["good"]["verified__by"] == "jev" and f["good"]["verified__confidence"] == 0.9
    assert run.aggregates["verified_counts"] == {
        "supported": 1,
        "partially_supported": 1,
        "unsupported": 1,
        "contradicted": 1,
        "no_claims": 0,
        "skipped_source_too_large": 0,
    }
    assert report.jev_decisions == 8 and report.llm_decisions == 0


async def test_by_llm_and_min_confidence_when_one_claim_falls_back() -> None:
    llm = FakeLLM(lambda *_: FakeAnswer("supported"))
    run = _run({"a": "We refunded your order today. The jar ships tomorrow, unsure."})
    report, _ = await _verify(run, FakeJev(_rule(confidence=0.8, low_on="unsure")), llm)

    f = run.items[0].fields
    assert f["verified"] == "supported"
    assert f["verified__by"] == "llm"
    assert f["verified__confidence"] == 0.8  # min(jev 0.8, llm 1.0)
    assert [c["by"] for c in f["verified_claims"]] == ["jev", "llm"]
    assert report.llm_decisions == 1


async def test_low_confidence_when_fallback_fails() -> None:
    run = _run({"a": "We refunded your order today. The jar ships tomorrow, unsure."})
    await _verify(run, FakeJev(_rule(low_on="unsure")), FakeLLM(fail=True))
    f = run.items[0].fields
    assert f["verified__low_confidence"] is True
    assert f["verified__confidence"] == 0.2


async def test_source_text_and_source_field_combined_state() -> None:
    jev = FakeJev(_rule())
    run = _run({"a": "Refunds take five business days."}, text="Where is my refund?")
    await _verify(run, jev, source_text="Refunds are issued within 5 business days.")

    state, _ = jev.calls[0]
    assert state == {
        "source": {
            "item_source": "Where is my refund?",
            "reference": "Refunds are issued within 5 business days.",
        },
        "claims": {"c1": "Refunds take five business days."},
    }


async def test_source_text_only_state() -> None:
    jev = FakeJev(_rule())
    run = _run({"a": "Refunds take five business days."})
    await _verify(run, jev, source_field=None, source_text="Refunds take 5 business days.")
    assert jev.calls[0][0]["source"] == "Refunds take 5 business days."


async def test_no_claims_makes_no_call() -> None:
    jev = FakeJev(_rule())
    run = _run(
        {
            "empty": "",
            "short": "Done. Thanks!",
            "missing": None,
            "real": "We refunded your order today.",
        }
    )
    report, ctx = await _verify(run, jev)

    assert len(jev.calls) == 1
    f = {i.id: i.fields for i in run.items}
    for k in ("empty", "short", "missing"):
        assert f[k]["verified"] == "no_claims"
        assert f[k]["verified__by"] == "code" and f[k]["verified__confidence"] == 1.0
        assert f[k]["verified_claims"] == [] and f[k]["verified_problem_claims"] == []
    assert f["real"]["verified"] == "supported"
    assert run.aggregates["verified_counts"]["no_claims"] == 3
    assert report.code_ops == 3
    assert ctx.ledger.stats().by["code"] == 3


async def test_source_too_large_is_skipped_not_crashed() -> None:
    run = _run({"big": "We refunded your order today.", "ok": "We refunded your order today."})
    run.items[0].text = "x" * 2000  # the source is the item text
    report, ctx = await _verify(run, FakeJev(_rule(), max_state_tokens=200))

    f = {i.id: i.fields for i in run.items}
    assert f["big"]["verified"] == "skipped_source_too_large"
    assert f["big"]["verified__by"] == "code"
    assert "verified__error" in f["big"]
    assert f["ok"]["verified"] == "supported"
    assert run.aggregates["verified_counts"]["skipped_source_too_large"] == 1
    assert any("find" in n for n in report.notes)
    assert ctx.ledger.stats().by["code"] == 1


async def test_state_too_large_raised_by_jev_is_skipped() -> None:
    class TooLargeJev(FakeJev):
        async def ask(self, state, questions):
            raise StateTooLarge("state is ~40000 tokens; the limit is 28000")

    run = _run({"a": "We refunded your order today."})
    report, _ = await _verify(run, TooLargeJev())
    assert run.items[0].fields["verified"] == "skipped_source_too_large"
    assert report.jev_decisions == 0


# --------------------------------------------------------------------------- params, registry, CLI


@pytest.mark.parametrize(
    "kw",
    [
        {"claim_field": "text"},  # same as default source_field
        {"claim_field": "reply", "source_field": "reply"},
        {"claim_field": "reply", "source_field": None},  # no source at all
        {"claim_field": "reply", "source_field": None, "source_text": "  "},
        {"claim_field": "reply", "max_claims": 0},
        {"claim_field": "reply", "min_words": 0},
    ],
)
def test_validation_rejects(kw) -> None:
    with pytest.raises(ValidationError):
        VerifyParams(**kw)


def test_validation_accepts_text_claims_with_reference_only() -> None:
    p = VerifyParams(claim_field="text", source_field=None, source_text="Policy.")
    assert p.output_field == "verified"


def test_registered_in_catalog() -> None:
    assert isinstance(get_tool("verify"), VerifyTool)
    entry = next(e for e in catalog() if e["name"] == "verify")
    assert entry["uses"] == "jev"
    assert {"claim_field", "source_field", "source_text"} <= set(
        entry["params_schema"]["properties"]
    )
    assert "find" in entry["description"]


def test_dev_cli_fake_inline_sample(tmp_path, monkeypatch) -> None:
    items = {
        "items": [
            {
                "id": "1",
                "text": "My jar arrived cracked, order 4821.",
                "fields": {"reply": "Sorry your jar arrived cracked. We added a free blender."},
            },
            {"id": "2", "text": "Cancel my account.", "fields": {"reply": "Done."}},
        ]
    }
    path = tmp_path / "items.json"
    path.write_text(json.dumps(items))
    monkeypatch.chdir(tmp_path)
    params = json.dumps({"claim_field": "reply"})
    assert dev.main(["tool", "verify", "--items", str(path), "--fake", "--params", params]) == 0
    out = json.loads((tmp_path / "out" / "verify_results.json").read_text())
    verdicts = {i["id"]: i["fields"]["verified"] for i in out["items"]}
    assert verdicts["2"] == "no_claims"
    assert verdicts["1"] in {"supported", "partially_supported", "unsupported", "contradicted"}


def test_sample_file_shape() -> None:
    data = json.loads(SAMPLE.read_text())
    assert all("reply" in i["fields"] and i["text"] for i in data["items"])
