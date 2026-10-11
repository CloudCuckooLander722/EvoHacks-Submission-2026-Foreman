"""LLM client: JSON parsing, JSON-mode negotiation, fallback validation."""

import asyncio
import time
from types import SimpleNamespace

import httpx2
import openai
import pytest
from pydantic import SecretStr

from foreman.config import Settings, get_settings
from foreman.core.decide import Decider
from foreman.core.ledger import Ledger
from foreman.core.llm import (
    DECIDE_SYSTEM,
    SCORE_GUIDE,
    LLMClient,
    LLMError,
    LLMTimeout,
    _decide_prompt,
    parse_json_object,
    track_usage,
)
from foreman.core.questions import Choice, Noul, Score
from foreman.testing.fakes import FakeAnswer, FakeJev


def _resp(content: str | None, pt: int = 100, ct: int = 10):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=pt, completion_tokens=ct),
    )


def _bad_request() -> openai.BadRequestError:
    req = httpx2.Request("POST", "https://llm.test/v1/chat/completions")
    resp = httpx2.Response(
        400, request=req, json={"error": {"message": "response_format unsupported"}}
    )
    return openai.BadRequestError("response_format unsupported", response=resp, body=None)


class Slow:
    """A queued output that only arrives after `seconds` (a stalled provider)."""

    def __init__(self, seconds: float, content: str) -> None:
        self.seconds = seconds
        self.content = content


class FakeSDK:
    """Stands in for `openai.AsyncOpenAI`; replays queued responses or exceptions."""

    def __init__(self, *outputs) -> None:
        self.outputs = list(outputs)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        self.closed = False

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        out = self.outputs.pop(0)
        if isinstance(out, Slow):
            await asyncio.sleep(out.seconds)
            out = out.content
        if isinstance(out, Exception):
            raise out
        return _resp(out)

    async def close(self) -> None:
        self.closed = True


def _client(*outputs, **settings) -> tuple[LLMClient, FakeSDK]:
    s = Settings(
        _env_file=None,
        fireworks_api_key=SecretStr("k"),
        llm_price_per_m_input=1.0,
        llm_price_per_m_output=2.0,
        **settings,
    )
    sdk = FakeSDK(*outputs)
    return LLMClient(s, client=sdk), sdk


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        'Sure! Here you go:\n{"a": 1}\nHope that helps {not json}',
        '<think>maybe {"a": 2}? no wait</think>{"a": 1}',
        'noise {broken} then {"a": 1}',
    ],
)
def test_parse_json_object(text) -> None:
    assert parse_json_object(text) == {"a": 1}


def test_parse_json_object_nested_and_braces_in_strings() -> None:
    assert parse_json_object('x {"a": {"b": "}"}} y') == {"a": {"b": "}"}}


@pytest.mark.parametrize("text", ["", None, "no json here", "[1, 2]"])
def test_parse_json_object_rejects(text) -> None:
    with pytest.raises(LLMError):
        parse_json_object(text)


async def test_json_request_and_tracking() -> None:
    llm, sdk = _client('{"ok": true}')
    with track_usage() as usage:
        assert await llm.json("sys", "user") == {"ok": True}
    req = sdk.requests[0]
    assert req["temperature"] == 0
    assert req["response_format"] == {"type": "json_object"}
    assert req["messages"][0] == {"role": "system", "content": "sys"}
    assert llm.calls == 1 and llm.prompt_tokens == 100 and llm.completion_tokens == 10
    assert llm.cost_usd == pytest.approx(100 / 1e6 + 20 / 1e6)
    assert usage.calls == 1 and usage.cost_usd == pytest.approx(llm.cost_usd)


async def test_json_mode_rejected_is_remembered() -> None:
    llm, sdk = _client(_bad_request(), '{"a": 1}', '{"b": 2}')
    assert await llm.json("s", "u") == {"a": 1}
    assert await llm.json("s", "u") == {"b": 2}
    assert "response_format" in sdk.requests[0]
    assert "response_format" not in sdk.requests[1]
    assert "response_format" not in sdk.requests[2]


async def test_json_retries_parse_failure_once() -> None:
    llm, _ = _client("garbage", '{"a": 1}')
    assert await llm.json("s", "u") == {"a": 1}
    llm, _ = _client("garbage", "still garbage")
    with pytest.raises(LLMError):
        await llm.json("s", "u")


def _timeout() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=httpx2.Request("POST", "https://llm.test/v1"))


async def test_json_and_decide_get_their_own_limits() -> None:
    llm, sdk = _client('{"a": 1}', '{"choice": "bug"}')
    await llm.json("s", "u")
    await llm.decide("state", "q", Choice("Type?", {"bug": None, "other": None}))
    j, d = sdk.requests
    assert (j["timeout"], j["max_tokens"]) == (90, 4096)
    assert (d["timeout"], d["max_tokens"]) == (8, 256)
    assert j["reasoning_effort"] == d["reasoning_effort"] == "none"


async def test_reasoning_effort_can_be_omitted() -> None:
    s = Settings(_env_file=None, fireworks_api_key=SecretStr("k"), llm_reasoning_effort="")
    sdk = FakeSDK('{"a": 1}')
    await LLMClient(s, client=sdk).json("s", "u")
    assert "reasoning_effort" not in sdk.requests[0]


async def test_timeout_retried_once() -> None:
    llm, sdk = _client(_timeout(), '{"a": 1}')
    assert await llm.json("s", "u") == {"a": 1}
    assert len(sdk.requests) == 2
    llm, sdk = _client(_timeout(), _timeout(), '{"a": 1}')
    with pytest.raises(LLMError, match="LLM call failed"):
        await llm.json("s", "u")
    assert len(sdk.requests) == 2


async def test_decide_timeout_returns_none_without_retry() -> None:
    q = Choice("Type?", {"bug": None, "other": None})
    llm, sdk = _client(_timeout(), '{"choice": "bug"}')
    assert await llm.decide("state", "q", q) is None
    assert len(sdk.requests) == 1  # the SDK's own timeout: no retry

    llm, sdk = _client(Slow(5, '{"choice": "bug"}'), llm_decide_timeout_s=0.05)
    t0 = time.perf_counter()
    assert await llm.decide("state", "q", q) is None
    assert time.perf_counter() - t0 < 1  # wall-clock bound, not the provider's pace
    assert len(sdk.requests) == 1


async def test_json_wall_clock_timeout_retried_once() -> None:
    llm, sdk = _client(Slow(5, '{"a": 0}'), '{"a": 1}', llm_json_timeout_s=0.05)
    assert await llm.json("s", "u") == {"a": 1}
    assert len(sdk.requests) == 2
    llm, sdk = _client(Slow(5, "{}"), Slow(5, "{}"), llm_json_timeout_s=0.05)
    with pytest.raises(LLMTimeout, match="timed out after 0.05s"):
        await llm.json("s", "u")
    assert len(sdk.requests) == 2


async def test_decider_keeps_jev_answer_when_fallback_times_out() -> None:
    llm, sdk = _client(Slow(5, '{"answer": true}'), llm_decide_timeout_s=0.05)
    jev = FakeJev(lambda *_: FakeAnswer(0.6))  # confidence 0.2 -> fallback
    ledger = Ledger("r")
    decider = Decider(jev, llm, ledger, "r", threshold=0.7)
    t0 = time.perf_counter()
    answers = await decider.decide(item_id="i", tool="t", state="x", questions={"q": Noul("Bug?")})
    assert time.perf_counter() - t0 < 1
    a = answers["q"]
    assert (a.decided_by, a.value, a.low_confidence) == ("jev", 0.6, True)
    assert len(sdk.requests) == 1


async def test_output_cut_at_max_tokens_is_an_error() -> None:
    llm, sdk = _client()
    cut = _resp('{"themes": [')
    cut.choices[0].finish_reason = "length"

    async def create(**kw):
        sdk.requests.append(kw)
        return cut

    sdk.chat.completions.create = create
    with pytest.raises(LLMError, match="max_tokens"):
        await llm.json("s", "u")


async def test_decide_choice_validates_keys() -> None:
    q = Choice("Type?", {"bug": "Broken", "praise": None})
    llm, sdk = _client('{"choice": "feature"}', '```json\n{"choice": "bug"}\n```')
    a = await llm.decide({"review": "crash"}, "type", q)
    assert a is not None
    assert (a.value, a.probabilities, a.confidence, a.decided_by) == (
        "bug",
        {"bug": 1.0},
        1.0,
        "llm",
    )
    prompt = sdk.requests[0]["messages"][1]["content"]
    assert "crash" in prompt
    assert "\nbug: Broken\n" in prompt and "\npraise: (no description)\n" in prompt
    assert '{"choice": "<key>"}, where <key> is one of: bug, praise.' in prompt


async def test_decide_score_and_noul() -> None:
    llm, _ = _client('{"level": 2}')
    a = await llm.decide("x", "s", Score("How bad?", ["low", "mid", "high"]))
    assert a is not None and a.value == 2.0 and a.qtype == "score"

    llm, _ = _client('{"level": 7}', '{"level": 9}')
    assert await llm.decide("x", "s", Score("How bad?", ["low", "high"])) is None

    llm, _ = _client('{"answer": false}')
    a = await llm.decide("x", "n", Noul("Spam?"))
    assert a is not None and a.value == 0.0 and a.probabilities == {"true": 0.0, "false": 1.0}


def test_prompt_system_forbids_inference() -> None:
    system, _ = _decide_prompt("x", Noul("Spam?"))
    assert system == DECIDE_SYSTEM
    assert "only what the TEXT explicitly states" in system
    assert "Do not infer" in system and "JSON object" in system


def test_prompt_score_numbers_levels_low_to_high() -> None:
    q = Score("How severe is the problem?", ["No problem", "Minor", "Broken", "Data lost"])
    _, user = _decide_prompt("Support fixed my billing issue.", q)
    lines = user.splitlines()
    assert lines[:4] == ["TEXT:", '"""', "Support fixed my billing issue.", '"""']
    assert "QUESTION: How severe is the problem?" in lines
    start = lines.index("LEVELS (0 = lowest, 3 = highest):")
    assert lines[start + 1 : start + 5] == [
        "0: No problem",
        "1: Minor",
        "2: Broken",
        "3: Data lost",
    ]
    assert "Question again: How severe is the problem?" in lines and SCORE_GUIDE in lines
    assert lines.index("Question again: How severe is the problem?") > start + 4
    assert lines[-1] == 'Reply with exactly {"level": <number from the list: 0, 1, 2, 3>}.'


def test_prompt_choice_lists_keys_and_descriptions() -> None:
    q = Choice("Which type?", {"bug": "Something is broken", "other": None})
    _, user = _decide_prompt("crash", q)
    assert "OPTIONS (key: description):\nbug: Something is broken\nother: (no description)" in user
    assert user.endswith(
        'Reply with exactly {"choice": "<key>"}, where <key> is one of: bug, other.'
    )


@pytest.mark.parametrize(
    ("q", "true", "false"),
    [
        (Noul("Spam?", true="An ad", false="A real review"), "An ad", "A real review"),
        (
            Noul("Spam?", true="An ad"),
            "An ad",
            "The description for true does not apply to what the TEXT states.",
        ),
        (
            Noul("Spam?"),
            "Yes: the TEXT states that the answer to the question is yes.",
            "No: the answer is no, or the TEXT does not state it.",
        ),
    ],
)
def test_prompt_noul_defines_true_and_false(q, true, false) -> None:
    _, user = _decide_prompt("buy now", q)
    assert f"true means: {true}\nfalse means: {false}" in user
    assert user.endswith('Reply with exactly {"answer": true} or {"answer": false}.')


def test_prompt_dict_state_is_json() -> None:
    _, user = _decide_prompt({"record_a": "x"}, Noul("Same?"))
    assert user.startswith('TEXT (JSON; parts are named by key):\n"""\n{"record_a": "x"}\n"""')


async def test_decide_returns_none_on_garbage_and_errors() -> None:
    llm, _ = _client("nope", "still nope")
    assert await llm.decide("x", "n", Noul("Spam?")) is None

    llm, _ = _client(RuntimeError("network down"))
    assert await llm.decide("x", "n", Noul("Spam?")) is None

    s = Settings(_env_file=None, fireworks_api_key=None)
    assert await LLMClient(s).decide("x", "n", Noul("Spam?")) is None


async def test_aclose() -> None:
    llm, sdk = _client()
    await llm.aclose()
    assert sdk.closed


@pytest.mark.live
async def test_live_json() -> None:
    if get_settings().fireworks_api_key is None:
        pytest.skip("FIREWORKS_API_KEY not set")
    async with LLMClient() as llm:
        out = await llm.json("Reply with only JSON.", 'Return {"ok": true}.')
    assert out.get("ok") is True
