"""Async LLM client (Fireworks, OpenAI-compatible), JSON helpers.

The light LLM is the single fallback when Jev is unsure, and the proposer/planner
elsewhere. Everything it returns is forced through strict JSON and validated, because a
fallback that can return junk is worse than keeping Jev's low-confidence answer.

Written against openai 3.x: `AsyncOpenAI` refuses to construct without an API key, so
the SDK client is built lazily on first use; that keeps `make_context()` and offline runs
working without `FIREWORKS_API_KEY`. The SDK uses its own `httpx2` transport, so tests
mock the SDK client object rather than HTTP. `openai` is not imported at module level
because the 3.x package is large (~2k modules, 15-40 s to import on a busy laptop) and
offline runs with `FakeLLM` never need it. A live `LLMClient` imports it when constructed,
so that cost lands in `make_context()` instead of blocking the event loop mid-run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Self

from foreman.config import Settings, get_settings
from foreman.core.questions import Answer, Choice, Noul, Question, Score

log = logging.getLogger(__name__)


class LLMError(Exception):
    """The LLM call failed or returned output we could not use."""


class LLMUnavailable(LLMError):
    """The call itself failed (missing key, HTTP error). Retrying the same prompt won't help."""


class LLMTimeout(LLMUnavailable):
    """The call exceeded its wall-clock limit (`_Limits.timeout_s`)."""


@dataclass
class CallUsage:
    """Usage of the LLM within one `track_usage()` scope (one task's worth of calls)."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


_usage_var: ContextVar[CallUsage | None] = ContextVar("foreman_llm_usage", default=None)


@contextmanager
def track_usage() -> Iterator[CallUsage]:
    """Collect the cost of LLM calls made inside this block, in this task only.

    The Decider runs fallbacks concurrently; a context variable lets it charge each call's
    cost to the right decision without changing `decide()`'s return type.
    """
    usage = CallUsage()
    token = _usage_var.set(usage)
    try:
        yield usage
    finally:
        _usage_var.reset(token)


def record_usage(
    prompt_tokens: int, completion_tokens: int, cost_usd: float, latency_ms: int
) -> None:
    """Add one call to the active `track_usage()` scope, if any (fakes call this too)."""
    usage = _usage_var.get()
    if usage is not None:
        usage.calls += 1
        usage.prompt_tokens += prompt_tokens
        usage.completion_tokens += completion_tokens
        usage.cost_usd += cost_usd
        usage.latency_ms += latency_ms


_THINK_RE = re.compile(r"<think>.*?(</think>|$)", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _balanced_objects(text: str) -> Iterator[str]:
    """Yield each balanced top-level `{...}` span, skipping braces inside strings."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        end = -1
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end == -1:
            return
        yield text[start : end + 1]
        start = text.find("{", end + 1)


def parse_json_object(text: str | None) -> dict[str, Any]:
    """Pull the first JSON object out of model output.

    Handles reasoning blocks (`<think>...</think>`), code fences, and prose around the
    JSON. Raises `LLMError` when there is no parseable object.
    """
    if not text:
        raise LLMError("empty response")
    cleaned = _THINK_RE.sub("", text)
    candidates: list[str] = [m.group(1) for m in _FENCE_RE.finditer(cleaned)] + [cleaned]
    for chunk in candidates:
        for blob in _balanced_objects(chunk):
            try:
                obj = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    raise LLMError(f"no JSON object in response: {text[:200]!r}")


def _state_text(state: Any) -> str:
    return state if isinstance(state, str) else json.dumps(state, ensure_ascii=False, default=str)


DECIDE_SYSTEM = (
    "You answer one question about the TEXT you are given. Judge only what the TEXT "
    "explicitly states. Do not infer, guess or assume anything the TEXT does not say: "
    "if the TEXT does not mention something, treat it as absent. Reply with only a JSON "
    "object, no explanation."
)

# The model matched "I'd use it every day" to "every time" on a frequency-of-problem
# scale; making the "question doesn't apply" case explicit fixed it.
SCORE_GUIDE = (
    "First check whether the TEXT describes the thing the question asks about (for "
    "example, if the question asks about a problem, does the TEXT describe a problem?). "
    "If it does not, pick the level for that case, even when other words in the TEXT "
    "resemble a level's description. Otherwise pick the level whose description "
    "matches what the TEXT states."
)


def _score_lines(q: Score) -> list[str]:
    top = len(q.levels) - 1
    lines = [f"LEVELS (0 = lowest, {top} = highest):"]
    lines += [f"{i}: {desc}" for i, desc in enumerate(q.levels)]
    numbers = ", ".join(str(i) for i in range(len(q.levels)))
    return [
        *lines,
        "",
        f"Question again: {q.instructions}",
        SCORE_GUIDE,
        f'Reply with exactly {{"level": <number from the list: {numbers}>}}.',
    ]


def _choice_lines(q: Choice) -> list[str]:
    lines = ["OPTIONS (key: description):"]
    lines += [f"{key}: {desc or '(no description)'}" for key, desc in q.options.items()]
    keys = ", ".join(q.options)
    return [
        *lines,
        "",
        f'Reply with exactly {{"choice": "<key>"}}, where <key> is one of: {keys}.',
    ]


def _noul_lines(q: Noul) -> list[str]:
    true = q.true or "Yes: the TEXT states that the answer to the question is yes."
    if q.false:
        false = q.false
    elif q.true:
        false = "The description for true does not apply to what the TEXT states."
    else:
        false = "No: the answer is no, or the TEXT does not state it."
    return [
        f"true means: {true}",
        f"false means: {false}",
        "",
        'Reply with exactly {"answer": true} or {"answer": false}.',
    ]


def _decide_prompt(state: Any, question: Question) -> tuple[str, str]:
    """Fallback prompt: numbered levels / keyed options / defined true-false, JSON only.

    The model must see exactly the scale Jev saw and nothing to read between the lines;
    a fallback that infers ("billing issue" -> "charged wrongly") is worse than Jev.
    """
    label = "TEXT:" if isinstance(state, str) else "TEXT (JSON; parts are named by key):"
    lines = [label, '"""', _state_text(state), '"""', "", f"QUESTION: {question.instructions}"]
    if isinstance(question, Choice):
        lines += _choice_lines(question)
    elif isinstance(question, Score):
        lines += _score_lines(question)
    elif isinstance(question, Noul):
        lines += _noul_lines(question)
    else:
        raise TypeError(f"not a question: {question!r}")
    return DECIDE_SYSTEM, "\n".join(lines)


def _to_answer(question: Question, obj: dict[str, Any]) -> Answer:
    """Validate the LLM's JSON against the question. Raises `LLMError` on anything off."""
    if isinstance(question, Choice):
        key = obj.get("choice")
        if not isinstance(key, str) or key not in question.options:
            raise LLMError(f"choice {key!r} is not one of the options")
        return Answer("choice", key, {key: 1.0}, 1.0, "llm")
    if isinstance(question, Score):
        level = obj.get("level")
        if isinstance(level, bool) or not isinstance(level, (int, float)) or level != int(level):
            raise LLMError(f"level {level!r} is not an integer")
        level = int(level)
        if not 0 <= level < len(question.levels):
            raise LLMError(f"level {level} is out of range")
        return Answer("score", float(level), {str(level): 1.0}, 1.0, "llm")
    if isinstance(question, Noul):
        ans = obj.get("answer")
        if not isinstance(ans, bool):
            raise LLMError(f"answer {ans!r} is not a boolean")
        p = 1.0 if ans else 0.0
        return Answer("noul", p, {"true": p, "false": 1.0 - p}, 1.0, "llm")
    raise LLMError(f"not a question: {question!r}")


@dataclass(frozen=True)
class _Limits:
    """Per-call budget: json() (proposals, planner) gets more time and tokens than decide()."""

    timeout_s: float
    max_tokens: int
    retry_on_timeout: bool = True


def _transient(e: Exception) -> bool:
    """Worth one retry: timeouts, dropped connections, rate limits and 5xx."""
    import openai

    if isinstance(e, (openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError)):
        return True
    return isinstance(e, openai.APIStatusError) and e.status_code >= 500


class LLMClient:
    """Light LLM over Fireworks' OpenAI-compatible API, with JSON-only helpers."""

    def __init__(self, settings: Settings | None = None, *, client: Any = None) -> None:
        self.settings = settings or get_settings()
        self._client = client
        if client is None:
            import openai  # noqa: F401 - pay the slow import now, not inside the first call

            if self.settings.fireworks_api_key is not None:
                # Building the client and touching `.chat.completions` loads more SDK modules
                # synchronously (seconds here). Inside the first call that would block the
                # event loop, where decide()'s timeout cannot interrupt it.
                _ = self._sdk().chat.completions
        self._sem = asyncio.Semaphore(self.settings.llm_max_concurrency)
        # None = untested, True = provider accepts response_format, False = it rejects it.
        self._json_mode: bool | None = None
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.cost_usd = 0.0

    @property
    def model(self) -> str:
        return self.settings.llm_model

    def _sdk(self) -> Any:
        if self._client is None:
            import openai

            key = self.settings.fireworks_api_key
            if key is None:
                raise LLMUnavailable("FIREWORKS_API_KEY is not set in .env")
            # SDK retries are off: `_create_retrying` retries once itself, so a timeout
            # costs at most two attempts instead of the SDK's silent three.
            self._client = openai.AsyncOpenAI(
                base_url=self.settings.llm_base_url,
                api_key=key.get_secret_value(),
                timeout=self.settings.llm_timeout_s,
                max_retries=0,
            )
        return self._client

    def _record(self, resp: Any, latency_ms: int) -> None:
        usage = getattr(resp, "usage", None)
        pt = int(getattr(usage, "prompt_tokens", 0) or 0)
        ct = int(getattr(usage, "completion_tokens", 0) or 0)
        details = getattr(usage, "completion_tokens_details", None)
        reasoning = int(getattr(details, "reasoning_tokens", 0) or 0)
        log.debug(
            "LLM call: %d ms, %d prompt + %d completion tokens (%d reasoning)",
            latency_ms,
            pt,
            ct,
            reasoning,
        )
        s = self.settings
        cost = pt * s.llm_price_per_m_input / 1e6 + ct * s.llm_price_per_m_output / 1e6
        self.calls += 1
        self.prompt_tokens += pt
        self.completion_tokens += ct
        self.cost_usd += cost
        record_usage(pt, ct, cost, latency_ms)

    async def _create(
        self, messages: list[dict[str, str]], json_mode: bool, limits: _Limits
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": limits.max_tokens,
            "timeout": limits.timeout_s,
        }
        if self.settings.llm_reasoning_effort:
            kwargs["reasoning_effort"] = self.settings.llm_reasoning_effort
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        import openai

        t0 = time.perf_counter()
        # The SDK timeout bounds each network phase, not the whole call; wait_for does.
        try:
            resp = await asyncio.wait_for(
                self._sdk().chat.completions.create(**kwargs), limits.timeout_s
            )
        except (TimeoutError, openai.APITimeoutError) as e:
            raise LLMTimeout(f"LLM call failed: timed out after {limits.timeout_s:g}s") from e
        self._record(resp, int((time.perf_counter() - t0) * 1000))
        return resp

    async def _create_retrying(
        self, messages: list[dict[str, str]], json_mode: bool, limits: _Limits
    ) -> Any:
        """`_create`, retried once on a transient provider error, and on a timeout if allowed."""
        import openai

        try:
            return await self._create(messages, json_mode, limits)
        except LLMTimeout as e:
            if not limits.retry_on_timeout:
                raise
            log.warning("%s; retrying once", e)
        except openai.OpenAIError as e:
            if not _transient(e):
                raise
            log.warning("LLM call failed (%s: %s); retrying once", type(e).__name__, e)
        return await self._create(messages, json_mode, limits)

    async def _complete(self, system: str, user: str, limits: _Limits) -> str:
        """One completion, learning once whether the provider accepts JSON mode."""
        import openai

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        async with self._sem:
            try:
                if self._json_mode is False:
                    resp = await self._create_retrying(messages, False, limits)
                else:
                    try:
                        resp = await self._create_retrying(messages, True, limits)
                        self._json_mode = True
                    except (openai.BadRequestError, openai.UnprocessableEntityError) as e:
                        if self._json_mode is True:
                            raise
                        log.info("LLM rejected response_format (%s); retrying without it", e)
                        resp = await self._create_retrying(messages, False, limits)
                        self._json_mode = False
            except openai.OpenAIError as e:
                status = getattr(e, "status_code", None)
                raise LLMUnavailable(
                    f"LLM call failed{f' [{status}]' if status else ''}: {e}"
                ) from e
        if not resp.choices:
            raise LLMError("LLM returned no choices")
        choice = resp.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            raise LLMError(f"LLM output hit max_tokens ({limits.max_tokens})")
        return choice.message.content or ""

    @property
    def _json_limits(self) -> _Limits:
        s = self.settings
        return _Limits(s.llm_json_timeout_s, s.llm_json_max_tokens)

    @property
    def _decide_limits(self) -> _Limits:
        s = self.settings
        return _Limits(s.llm_decide_timeout_s, s.llm_decide_max_tokens, retry_on_timeout=False)

    async def json(self, system: str, user: str) -> dict[str, Any]:
        """Ask for a JSON object; retry once if the output does not parse."""
        last: LLMError | None = None
        for _ in range(2):
            text = await self._complete(system, user, self._json_limits)
            try:
                return parse_json_object(text)
            except LLMError as e:
                last = e
                log.warning("LLM JSON parse failed: %s", e)
        raise LLMError(f"LLM output did not parse twice: {last}")

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """Free-text reply to a conversation (role/content dicts), for the chat UI.

        json() and decide() force JSON because code parses their output; a person reads
        this one, so JSON mode stays off. It gets json()'s budget: both are long outputs.
        """
        import openai

        async with self._sem:
            try:
                resp = await self._create_retrying(messages, False, self._json_limits)
            except openai.OpenAIError as e:
                status = getattr(e, "status_code", None)
                raise LLMUnavailable(
                    f"LLM call failed{f' [{status}]' if status else ''}: {e}"
                ) from e
        if not resp.choices:
            raise LLMError("LLM returned no choices")
        choice = resp.choices[0]
        if getattr(choice, "finish_reason", None) == "length":
            # A cut-off reply is still worth showing a person, unlike cut-off JSON.
            log.warning("LLM chat reply hit max_tokens (%d)", self._json_limits.max_tokens)
        return choice.message.content or ""

    async def warm(self) -> None:
        """One tiny call so the first real fallback skips connection setup. Never raises.

        Run it in the background at context creation: the first call pays for TLS, HTTP/2
        and the provider's cold start, which would otherwise eat a fallback's short timeout.
        Its cost is not charged to the run's ledger.
        """
        try:
            await self._complete(
                "Reply with only a JSON object.",
                'Return exactly {"ok": true}.',
                _Limits(self.settings.llm_json_timeout_s, 16, retry_on_timeout=False),
            )
        except Exception as e:  # noqa: BLE001 - warming is best-effort
            log.info("LLM warm-up call failed (ignored): %s", e)

    async def decide(self, state: Any, qid: str, question: Question) -> Answer | None:
        """Answer one question as the fallback. Returns None instead of raising.

        Bad output (unparseable, unknown key, out-of-range level) is retried once; after
        that the Decider keeps Jev's answer and flags it low-confidence. A timeout
        (`LLM_DECIDE_TIMEOUT_S`) is not retried: a fallback is optional, a stalled run is not.
        """
        try:
            system, user = _decide_prompt(state, question)
        except Exception:
            log.exception("could not build fallback prompt for %s", qid)
            return None
        for attempt in range(2):
            try:
                text = await self._complete(system, user, self._decide_limits)
                return _to_answer(question, parse_json_object(text))
            except LLMTimeout as e:
                log.warning("LLM fallback for %s gave up, keeping Jev's answer: %s", qid, e)
                return None
            except LLMUnavailable as e:
                log.warning("LLM fallback for %s unavailable: %s", qid, e)
                return None
            except LLMError as e:
                log.warning(
                    "LLM fallback for %s gave bad output (attempt %d): %s", qid, attempt + 1, e
                )
            except Exception:
                log.exception("unexpected LLM fallback error for %s", qid)
                return None
        return None

    async def aclose(self) -> None:
        """Close the SDK client if one was created."""
        if self._client is not None and hasattr(self._client, "close"):
            await self._client.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
