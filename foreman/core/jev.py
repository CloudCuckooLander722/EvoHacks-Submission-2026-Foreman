"""Async Jev client (Vercel AI Gateway), rate limit, retries, cost.

A thin client of our own rather than the TypeSafe SDK, so we control batching,
pacing, retries and cost accounting in one place.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Self

import httpx

from foreman.config import Settings, get_settings
from foreman.core.concurrency import RateLimiter
from foreman.core.questions import (
    Answer,
    Question,
    answer_from_wire,
    qtype_of,
    to_wire,
    validate_question,
)

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 0.5
BACKOFF_CAP_S = 20.0


class JevError(Exception):
    """Jev refused or kept failing after retries. The Decider falls back to the LLM on this."""

    def __init__(self, message: str, *, status: int | None = None, error_type: str | None = None):
        super().__init__(message if status is None else f"[{status}] {message}")
        self.message = message
        self.status = status
        self.error_type = error_type


class StateTooLarge(ValueError):
    """The state would not fit Jev's context. Not retried and not sent to the LLM either."""


@dataclass
class JevResult:
    """One Jev call: every question's answer plus what it cost."""

    answers: dict[str, Answer]
    input_tokens: int
    cost_usd: float
    latency_ms: int
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def estimate_tokens(state: Any) -> int:
    """Cheap chars/4 estimate, good enough to refuse obviously oversized states."""
    return int(len(json.dumps(state, ensure_ascii=False, default=str)) / 4)


def _retry_after_s(resp: httpx.Response) -> float | None:
    v = resp.headers.get("retry-after")
    if v is None:
        return None
    try:
        return max(0.0, float(v))
    except ValueError:
        return None


def _error_detail(resp: httpx.Response) -> tuple[str, str | None]:
    """Pull `message` and `error_type` out of an error body, whatever its nesting."""
    try:
        body = resp.json()
    except ValueError:
        return (resp.text[:500] or resp.reason_phrase), None
    if not isinstance(body, dict):
        return str(body)[:500], None
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    message = body.get("message") or err.get("message") or json.dumps(body)[:500]
    error_type = (
        body.get("error_type") or err.get("type") or err.get("error_type") or body.get("type")
    )
    return str(message), (str(error_type) if error_type is not None else None)


class JevClient:
    """Sends batches of typed questions about one state to Jev.

    One call carries every question for an item, so the per-item cost is one round trip.
    Concurrency is capped by a semaphore and paced by a token bucket so a 10k-item run
    stays under the gateway's rate limit instead of fighting it with retries.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http: httpx.AsyncClient | None = None,
        backoff_base_s: float = BACKOFF_BASE_S,
    ) -> None:
        self.settings = settings or get_settings()
        s = self.settings
        self.url = f"{s.jev_base_url.rstrip('/')}/v1/systemone"
        self._http = http or httpx.AsyncClient(timeout=s.jev_timeout_s)
        self._sem = asyncio.Semaphore(s.jev_max_concurrency)
        self._limiter = RateLimiter(s.jev_max_rps)
        self._backoff_base_s = backoff_base_s
        self.calls = 0
        self.input_tokens = 0
        self.cost_usd = 0.0

    def _headers(self) -> dict[str, str]:
        key = self.settings.ai_gateway_api_key
        if key is None:
            raise JevError("AI_GATEWAY_API_KEY is not set in .env", error_type="missing_key")
        return {"Authorization": f"Bearer {key.get_secret_value()}"}

    def _check(self, state: Any, questions: dict[str, Question]) -> None:
        if not questions:
            raise ValueError("ask() needs at least one question")
        for qid, q in questions.items():
            try:
                validate_question(q)
            except ValueError as e:
                raise ValueError(f"question {qid!r}: {e}") from e
        tokens = estimate_tokens(state)
        if tokens > self.settings.jev_max_state_tokens:
            raise StateTooLarge(
                f"state is ~{tokens} tokens; the limit is {self.settings.jev_max_state_tokens}"
            )

    def _backoff(self, attempt: int, resp: httpx.Response | None) -> float:
        retry_after = _retry_after_s(resp) if resp is not None else None
        if retry_after is not None:
            return retry_after
        return random.uniform(0, min(BACKOFF_CAP_S, self._backoff_base_s * 2**attempt))

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = self._headers()
        last: str = "no attempt made"
        for attempt in range(MAX_ATTEMPTS):
            resp: httpx.Response | None = None
            try:
                async with self._limiter:
                    resp = await self._http.post(self.url, json=body, headers=headers)
            except httpx.TransportError as e:
                last = f"{type(e).__name__}: {e}"
            else:
                if resp.status_code < 400:
                    try:
                        return resp.json()
                    except ValueError as e:
                        raise JevError(f"invalid JSON from Jev: {resp.text[:200]}") from e
                message, error_type = _error_detail(resp)
                retryable = resp.status_code in (429, 529) or resp.status_code >= 500
                if not retryable:
                    raise JevError(message, status=resp.status_code, error_type=error_type)
                last = f"[{resp.status_code}] {message}"
            if attempt + 1 < MAX_ATTEMPTS:
                delay = self._backoff(attempt, resp)
                log.warning(
                    "Jev attempt %d failed (%s); retrying in %.2fs", attempt + 1, last, delay
                )
                await asyncio.sleep(delay)
        raise JevError(
            f"gave up after {MAX_ATTEMPTS} attempts: {last}", error_type="retries_exhausted"
        )

    async def ask(self, state: str | dict | list, questions: dict[str, Question]) -> JevResult:
        """Ask every question about `state` in one call.

        Raises `ValueError` for malformed questions, `StateTooLarge` before sending an
        oversized state, and `JevError` when Jev refuses or keeps failing.
        """
        self._check(state, questions)
        body = {
            "model": self.settings.jev_model,
            "state": state,
            "questions": {qid: to_wire(q) for qid, q in questions.items()},
        }
        t0 = time.perf_counter()
        async with self._sem:
            data = await self._post(body)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        raw_answers = data.get("answers") or {}
        answers: dict[str, Answer] = {}
        for qid, q in questions.items():
            if qid not in raw_answers:
                raise JevError(
                    f"Jev returned no answer for question {qid!r}", error_type="bad_response"
                )
            try:
                answers[qid] = answer_from_wire(qtype_of(q), raw_answers[qid])
            except (KeyError, TypeError, ValueError) as e:
                raise JevError(f"unparseable answer for {qid!r}: {raw_answers[qid]!r}") from e

        input_tokens = int((data.get("usage") or {}).get("input_tokens") or 0)
        cost = ((data.get("provider_metadata") or {}).get("gateway") or {}).get("cost")
        if cost is not None:
            cost_usd = float(cost)
        else:
            cost_usd = input_tokens * self.settings.jev_price_per_m_input / 1e6

        self.calls += 1
        self.input_tokens += input_tokens
        self.cost_usd += cost_usd
        return JevResult(answers, input_tokens, cost_usd, latency_ms, raw=data)

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
