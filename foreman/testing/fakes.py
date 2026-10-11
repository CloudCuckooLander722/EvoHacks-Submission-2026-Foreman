"""FakeJev, FakeLLM (deterministic, offline; also used by `dev --fake`).

Every tool test and `dev --fake` runs on these, so their behaviour is part of the
contract: keyword-overlap heuristics that are predictable enough to assert on, plus
`rule` hooks so a test can force any answer or confidence it needs.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from foreman.core.jev import JevError, JevResult, StateTooLarge, estimate_tokens
from foreman.core.llm import LLMError, record_usage
from foreman.core.questions import (
    Answer,
    Choice,
    Noul,
    Question,
    Score,
    to_wire,
    validate_question,
)

DEFAULT_PRICE_PER_M_INPUT = 0.042
DEFAULT_MAX_STATE_TOKENS = 28_000


@dataclass
class FakeAnswer:
    """What a test `rule` returns to force an answer.

    `value` is the option key (choice), level index (score) or P(yes) (noul). Missing
    probabilities and confidence are derived the same way the defaults are.
    """

    value: str | float
    confidence: float | None = None
    probabilities: dict[str, float] | None = None


Rule = Callable[[Any, str, Question], "FakeAnswer | Answer | None"]


def state_text(state: Any) -> str:
    """Lowercased text of a state, JSON-dumped when it is not a string."""
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, default=str)
    return text.lower()


def keywords(text: str | None) -> set[str]:
    """Words longer than 3 characters; underscores split keys like `feature_request`."""
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 3}


def _overlap(words: set[str], text: str) -> int:
    return sum(1 for w in words if w in text)


def heuristic_choice(text: str, q: Choice) -> tuple[str, int]:
    """Best option by keyword overlap (ties -> first option) and its score."""
    best, best_score = next(iter(q.options)), -1
    for key, desc in q.options.items():
        score = _overlap(keywords(f"{key} {desc or ''}"), text)
        if score > best_score:
            best, best_score = key, score
    return best, best_score


def heuristic_level(text: str, q: Score) -> int:
    """Level with the most keyword overlap; the middle level when nothing overlaps."""
    scores = [_overlap(keywords(lvl), text) for lvl in q.levels]
    top = max(scores)
    return scores.index(top) if top > 0 else len(q.levels) // 2


def heuristic_noul(text: str, q: Noul) -> float:
    """0.9 if any word of `true` (or the instructions) appears in the state, else 0.1."""
    words = keywords(q.true if q.true is not None else q.instructions)
    return 0.9 if _overlap(words, text) > 0 else 0.1


def _spread(keys: list[str], chosen: str, p: float) -> dict[str, float]:
    if len(keys) == 1:
        return {chosen: 1.0}
    rest = (1.0 - p) / (len(keys) - 1)
    return {k: (p if k == chosen else rest) for k in keys}


def jev_answer(q: Question, fa: FakeAnswer) -> Answer:
    """Turn a FakeAnswer into a Jev-shaped Answer, filling in derived fields."""
    if isinstance(q, Noul):
        p = float(fa.value)
        conf = fa.confidence if fa.confidence is not None else abs(2 * p - 1)
        return Answer("noul", p, fa.probabilities or {"true": p, "false": 1 - p}, conf, "jev")
    if isinstance(q, Choice):
        key = str(fa.value)
        probs = fa.probabilities or _spread(list(q.options), key, 0.8)
        return Answer(
            "choice", key, probs, fa.confidence if fa.confidence is not None else 0.9, "jev"
        )
    level = float(fa.value)
    keys = [str(i) for i in range(len(q.levels))]
    probs = fa.probabilities or _spread(keys, str(round(level)), 0.7)
    return Answer("score", level, probs, fa.confidence if fa.confidence is not None else 0.8, "jev")


def default_jev_answer(state: Any, q: Question) -> Answer:
    """The deterministic keyword heuristic FakeJev uses when no rule applies."""
    text = state_text(state)
    if isinstance(q, Choice):
        key, score = heuristic_choice(text, q)
        return jev_answer(q, FakeAnswer(key, confidence=0.9 if score > 0 else 0.3))
    if isinstance(q, Score):
        return jev_answer(q, FakeAnswer(float(heuristic_level(text, q)), confidence=0.8))
    return jev_answer(q, FakeAnswer(heuristic_noul(text, q)))


class FakeJev:
    """Offline stand-in for `JevClient` with the same `ask()` contract.

    Validates questions and the state limit like the real client, so tools that would
    be refused by Jev fail in tests too. `fail=True` simulates Jev being down.
    """

    def __init__(
        self,
        rule: Rule | None = None,
        latency_ms: int = 1,
        *,
        fail: bool = False,
        max_state_tokens: int = DEFAULT_MAX_STATE_TOKENS,
        price_per_m_input: float = DEFAULT_PRICE_PER_M_INPUT,
    ) -> None:
        self.rule = rule
        self.latency_ms = latency_ms
        self.fail = fail
        self.max_state_tokens = max_state_tokens
        self.price_per_m_input = price_per_m_input
        self.calls: list[tuple[Any, dict[str, Question]]] = []

    async def ask(self, state: str | dict | list, questions: dict[str, Question]) -> JevResult:
        """Answer with `rule` where it returns something, else the keyword heuristic."""
        if not questions:
            raise ValueError("ask() needs at least one question")
        for qid, q in questions.items():
            try:
                validate_question(q)
            except ValueError as e:
                raise ValueError(f"question {qid!r}: {e}") from e
        tokens = estimate_tokens(state)
        if tokens > self.max_state_tokens:
            raise StateTooLarge(f"state is ~{tokens} tokens; the limit is {self.max_state_tokens}")
        self.calls.append((state, questions))
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000)
        if self.fail:
            raise JevError("FakeJev is down", status=529, error_type="overloaded")

        answers: dict[str, Answer] = {}
        for qid, q in questions.items():
            forced = self.rule(state, qid, q) if self.rule else None
            if isinstance(forced, Answer):
                answers[qid] = forced
            elif isinstance(forced, FakeAnswer):
                answers[qid] = jev_answer(q, forced)
            else:
                answers[qid] = default_jev_answer(state, q)

        body = {"state": state, "questions": {k: to_wire(q) for k, q in questions.items()}}
        input_tokens = int(len(json.dumps(body, ensure_ascii=False, default=str)) / 4)
        cost = input_tokens * self.price_per_m_input / 1e6
        return JevResult(answers, input_tokens, cost, self.latency_ms)

    async def aclose(self) -> None:
        return None


def llm_answer(q: Question, value: str | float) -> Answer:
    """An LLM-shaped Answer: one-hot probabilities and confidence 1.0."""
    if isinstance(q, Noul):
        p = 1.0 if float(value) >= 0.5 else 0.0
        return Answer("noul", p, {"true": p, "false": 1 - p}, 1.0, "llm")
    if isinstance(q, Choice):
        return Answer("choice", str(value), {str(value): 1.0}, 1.0, "llm")
    level = round(float(value))
    return Answer("score", float(level), {str(level): 1.0}, 1.0, "llm")


class FakeLLM:
    """Offline stand-in for `LLMClient` with the same `decide()` and `json()` contracts.

    `decide` uses `rule` (a rule that raises makes that call return None), then the same
    keyword heuristics as FakeJev. `json` answers from `json_responses`, a map from a
    substring of the prompt to the dict to return.
    """

    def __init__(
        self,
        rule: Rule | None = None,
        json_responses: dict[str, dict[str, Any]] | None = None,
        fail: bool = False,
    ) -> None:
        self.rule = rule
        self.json_responses = json_responses or {}
        self.fail = fail
        self.calls: list[tuple] = []

    async def decide(self, state: Any, qid: str, question: Question) -> Answer | None:
        """Never raises; returns None when `fail=True` or the rule raises."""
        self.calls.append(("decide", state, qid, question))
        record_usage(0, 0, 0.0, 0)
        if self.fail:
            return None
        try:
            forced = self.rule(state, qid, question) if self.rule else None
        except Exception:  # noqa: BLE001 - mirror LLMClient.decide: never raise
            return None
        if isinstance(forced, Answer):
            return forced
        if isinstance(forced, FakeAnswer):
            return llm_answer(question, forced.value)
        text = state_text(state)
        if isinstance(question, Choice):
            return llm_answer(question, heuristic_choice(text, question)[0])
        if isinstance(question, Score):
            return llm_answer(question, heuristic_level(text, question))
        if isinstance(question, Noul):
            return llm_answer(question, heuristic_noul(text, question))
        return None

    async def json(self, system: str, user: str) -> dict[str, Any]:
        """Return the first canned response whose key appears in the prompt."""
        self.calls.append(("json", system, user))
        record_usage(0, 0, 0.0, 0)
        if self.fail:
            raise LLMError("FakeLLM is set to fail")
        for needle, response in self.json_responses.items():
            if needle in system or needle in user:
                return copy.deepcopy(response)
        raise LLMError("FakeLLM has no json_response matching this prompt")

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """Echo the last user message, so a chat UI can be clicked through offline."""
        self.calls.append(("chat", messages))
        record_usage(0, 0, 0.0, 0)
        if self.fail:
            raise LLMError("FakeLLM is set to fail")
        last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        return f"(FakeLLM) You said: {last}"

    async def aclose(self) -> None:
        return None
