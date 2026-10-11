"""Noul / Choice / Score question types + Answer.

These are the only three judgments Jev can make. Tools build them; the Decider sends
them to Jev and, when Jev is unsure, to the LLM. Keeping one typed shape for both
models is what lets the confidence gate swap one answer for the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

MAX_OPTIONS = 255
MIN_LEVELS = 2
MAX_LEVELS = 10


@dataclass(frozen=True)
class Noul:
    """A yes/no question. Jev returns P(yes)."""

    instructions: str
    true: str | None = None
    false: str | None = None
    fallback: bool = True


@dataclass(frozen=True)
class Choice:
    """Pick one option key. Descriptions carry the boundary cases Jev needs."""

    instructions: str
    options: dict[str, str | None]
    fallback: bool = True


@dataclass(frozen=True)
class Score:
    """Rate on 2..10 ordered levels, low -> high."""

    instructions: str
    levels: list[str]
    fallback: bool = True


Question = Noul | Choice | Score
QType = Literal["noul", "choice", "score"]


@dataclass
class Answer:
    """One decision, from Jev or the LLM, in a shape tools can apply without caring which."""

    qtype: QType
    value: str | float
    probabilities: dict[str, float]
    confidence: float
    decided_by: Literal["jev", "llm"]
    low_confidence: bool = False
    jev_value: str | float | None = None


def qtype_of(q: Question) -> QType:
    """The wire name of a question's type."""
    if isinstance(q, Noul):
        return "noul"
    if isinstance(q, Choice):
        return "choice"
    if isinstance(q, Score):
        return "score"
    raise TypeError(f"not a question: {q!r}")


def validate_question(q: Question) -> None:
    """Reject questions Jev would refuse, before paying for a round trip."""
    if not q.instructions or not q.instructions.strip():
        raise ValueError("question instructions are empty")
    if isinstance(q, Choice):
        n = len(q.options)
        if n == 0:
            raise ValueError("Choice has no options")
        if n > MAX_OPTIONS:
            raise ValueError(f"Choice has {n} options; the maximum is {MAX_OPTIONS}")
        if any(not k or not str(k).strip() for k in q.options):
            raise ValueError("Choice has an empty option key")
    elif isinstance(q, Score):
        n = len(q.levels)
        if not MIN_LEVELS <= n <= MAX_LEVELS:
            raise ValueError(f"Score has {n} levels; it needs {MIN_LEVELS} to {MAX_LEVELS}")
    elif not isinstance(q, Noul):
        raise TypeError(f"not a question: {q!r}")


def to_wire(q: Question) -> dict[str, Any]:
    """Build the TypeSafe request shape for one question."""
    wire: dict[str, Any] = {"type": qtype_of(q), "instructions": q.instructions}
    if isinstance(q, Choice):
        wire["criteria"] = dict(q.options)
    elif isinstance(q, Score):
        wire["criteria"] = list(q.levels)
    else:
        criteria = {k: v for k, v in (("true", q.true), ("false", q.false)) if v is not None}
        if criteria:
            wire["criteria"] = criteria
    return wire


def answer_from_wire(qtype: QType, raw: dict[str, Any]) -> Answer:
    """Turn one entry of Jev's `answers` map into an `Answer`."""
    if qtype == "noul":
        p = float(raw["noul"])
        return Answer(
            qtype="noul",
            value=p,
            probabilities={"true": p, "false": 1.0 - p},
            confidence=abs(2.0 * p - 1.0),
            decided_by="jev",
        )
    probs = {str(k): float(v) for k, v in (raw.get("probabilities") or {}).items()}
    confidence = float(raw["confidence"])
    if qtype == "choice":
        return Answer("choice", str(raw["choice"]), probs, confidence, "jev")
    if qtype == "score":
        return Answer("score", float(raw["score"]), probs, confidence, "jev")
    raise ValueError(f"unknown question type {qtype!r}")
