"""Question validation, wire format and answer parsing."""

import pytest

from foreman.core.questions import (
    Choice,
    Noul,
    Score,
    answer_from_wire,
    to_wire,
    validate_question,
)


def test_to_wire_choice() -> None:
    q = Choice("Which type?", {"bug": "Something broken", "other": None})
    assert to_wire(q) == {
        "type": "choice",
        "instructions": "Which type?",
        "criteria": {"bug": "Something broken", "other": None},
    }


def test_to_wire_score() -> None:
    q = Score("How angry?", ["Calm", "Angry"])
    assert to_wire(q) == {
        "type": "score",
        "instructions": "How angry?",
        "criteria": ["Calm", "Angry"],
    }


def test_to_wire_noul() -> None:
    assert to_wire(Noul("Spam?")) == {"type": "noul", "instructions": "Spam?"}
    assert to_wire(Noul("Spam?", true="ad", false="real"))["criteria"] == {
        "true": "ad",
        "false": "real",
    }
    assert to_wire(Noul("Spam?", true="ad"))["criteria"] == {"true": "ad"}


@pytest.mark.parametrize(
    "q",
    [
        Choice("x", {}),
        Choice("x", {f"o{i}": None for i in range(256)}),
        Choice("x", {"": None}),
        Score("x", ["one"]),
        Score("x", [str(i) for i in range(11)]),
        Noul("  "),
    ],
)
def test_validate_rejects(q) -> None:
    with pytest.raises(ValueError):
        validate_question(q)


def test_validate_accepts_limits() -> None:
    validate_question(Choice("x", {f"o{i}": None for i in range(255)}))
    validate_question(Score("x", ["a", "b"]))
    validate_question(Score("x", [str(i) for i in range(10)]))


def test_answer_from_wire() -> None:
    a = answer_from_wire("noul", {"noul": 0.8})
    assert a.value == 0.8
    assert a.probabilities == pytest.approx({"true": 0.8, "false": 0.2})
    assert a.confidence == pytest.approx(0.6)
    assert a.decided_by == "jev"

    c = answer_from_wire(
        "choice", {"choice": "bug", "probabilities": {"bug": 0.9}, "confidence": 0.85}
    )
    assert (c.value, c.confidence, c.probabilities) == ("bug", 0.85, {"bug": 0.9})

    s = answer_from_wire(
        "score",
        {"score": 1.4, "legend": ["a", "b"], "probabilities": {0: 0.6, 1: 0.4}, "confidence": 0.5},
    )
    assert s.value == 1.4
    assert s.probabilities == {"0": 0.6, "1": 0.4}
