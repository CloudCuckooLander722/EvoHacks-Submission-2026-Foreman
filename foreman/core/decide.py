"""Decider: confidence gate + single LLM fallback + ledger writes.

Every model decision in every tool goes through `Decider.decide()`. Keeping the gate in
one place is what makes the threshold, the fallback and the audit trail behave the same
for all eleven tools.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any, Protocol

from foreman.core.jev import JevError, JevResult
from foreman.core.ledger import Decision, Ledger
from foreman.core.llm import track_usage
from foreman.core.questions import Answer, Question, qtype_of

log = logging.getLogger(__name__)


class JevLike(Protocol):
    """What the Decider needs from Jev; `JevClient` and `FakeJev` both satisfy it."""

    async def ask(self, state: Any, questions: dict[str, Question]) -> JevResult: ...


class LLMLike(Protocol):
    """What the Decider needs from the LLM; `LLMClient` and `FakeLLM` both satisfy it."""

    async def decide(self, state: Any, qid: str, question: Question) -> Answer | None: ...

    async def json(self, system: str, user: str) -> dict[str, Any]: ...


class DecisionError(Exception):
    """Neither Jev nor the LLM could answer. Tools usually skip the item and add a note."""

    def __init__(self, item_id: str, tool: str, failed: list[str], cause: BaseException | None):
        detail = f": {cause}" if cause else ""
        super().__init__(f"{tool} could not decide {failed} for item {item_id}{detail}")
        self.item_id = item_id
        self.tool = tool
        self.failed = failed
        self.cause = cause


class Decider:
    """The confidence gate: accept Jev when sure, ask the LLM once when not."""

    def __init__(self, jev: JevLike, llm: LLMLike, ledger: Ledger, run_id: str, threshold: float):
        self.jev = jev
        self.llm = llm
        self.ledger = ledger
        self.run_id = run_id
        self.threshold = threshold

    async def _fallback(
        self, state: Any, qid: str, q: Question
    ) -> tuple[Answer | None, float, int]:
        """One LLM attempt with its own cost and latency (tracked per task)."""
        with track_usage() as usage:
            ans = await self.llm.decide(state, qid, q)
        if usage.calls:
            self.ledger.record_call("llm", usage.cost_usd, n=usage.calls)
        return ans, usage.cost_usd, usage.latency_ms

    def _decision(
        self, item_id: str, tool: str, qid: str, q: Question, a: Answer, **kw: Any
    ) -> Decision:
        return Decision(
            run_id=self.run_id,
            item_id=item_id,
            tool=tool,
            question_id=qid,
            qtype=qtype_of(q),
            value=a.value,
            confidence=a.confidence,
            probabilities=a.probabilities,
            decided_by=a.decided_by,
            jev_value=a.jev_value,
            low_confidence=a.low_confidence,
            **kw,
        )

    async def decide(
        self, *, item_id: str, tool: str, state: Any, questions: dict[str, Question]
    ) -> dict[str, Answer]:
        """Answer every question about `state`, writing one ledger Decision per question.

        Raises `StateTooLarge` (from Jev, before any call) and `DecisionError` when Jev is
        down and the LLM cannot answer some question either.
        """
        try:
            res = await self.jev.ask(state, questions)
        except JevError as e:
            log.warning("Jev failed for %s/%s, falling back to LLM: %s", tool, item_id, e)
            return await self._decide_without_jev(item_id, tool, state, questions, e)
        self.ledger.record_call("jev", res.cost_usd)

        share = res.cost_usd / len(questions)
        unsure = [
            qid
            for qid, q in questions.items()
            if q.fallback and res.answers[qid].confidence < self.threshold
        ]
        fallbacks = await asyncio.gather(
            *(self._fallback(state, qid, questions[qid]) for qid in unsure)
        )
        by_qid = dict(zip(unsure, fallbacks, strict=True))

        final: dict[str, Answer] = {}
        for qid, q in questions.items():
            jev_ans = res.answers[qid]
            if qid not in by_qid:
                final[qid] = jev_ans
                d = self._decision(
                    item_id, tool, qid, q, jev_ans, latency_ms=res.latency_ms, cost_usd=share
                )
            else:
                llm_ans, llm_cost, llm_ms = by_qid[qid]
                if llm_ans is not None:
                    final[qid] = replace(llm_ans, decided_by="llm", jev_value=jev_ans.value)
                else:
                    final[qid] = replace(jev_ans, low_confidence=True)
                d = self._decision(
                    item_id,
                    tool,
                    qid,
                    q,
                    final[qid],
                    jev_confidence=jev_ans.confidence if llm_ans is not None else None,
                    latency_ms=res.latency_ms + llm_ms,
                    cost_usd=share + llm_cost,
                )
            self.ledger.add(d)
        return final

    async def _decide_without_jev(
        self, item_id: str, tool: str, state: Any, questions: dict[str, Question], cause: JevError
    ) -> dict[str, Answer]:
        """Jev is down: try the LLM on every question; any miss fails the whole item."""
        qids = list(questions)
        results = await asyncio.gather(
            *(self._fallback(state, qid, questions[qid]) for qid in qids)
        )
        failed = [qid for qid, (a, _, _) in zip(qids, results, strict=True) if a is None]
        if failed:
            raise DecisionError(item_id, tool, failed, cause)
        final: dict[str, Answer] = {}
        for qid, (a, cost, ms) in zip(qids, results, strict=True):
            assert a is not None
            final[qid] = replace(a, decided_by="llm")
            self.ledger.add(
                self._decision(
                    item_id, tool, qid, questions[qid], final[qid], latency_ms=ms, cost_usd=cost
                )
            )
        return final
