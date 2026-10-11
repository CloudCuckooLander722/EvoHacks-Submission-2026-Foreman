"""LLM client: JSON parsing, JSON-mode negotiation, fallback validation."""

from types import SimpleNamespace

import httpx2
import openai
import pytest
from pydantic import SecretStr

from foreman.config import Settings, get_settings
from foreman.core.llm import LLMClient, LLMError, parse_json_object, track_usage
from foreman.core.questions import Choice, Noul, Score


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
        if isinstance(out, Exception):
            raise out
        return _resp(out)

    async def close(self) -> None:
        self.closed = True


def _client(*outputs) -> tuple[LLMClient, FakeSDK]:
    s = Settings(
        _env_file=None,
        fireworks_api_key=SecretStr("k"),
        llm_price_per_m_input=1.0,
        llm_price_per_m_output=2.0,
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
    assert "crash" in prompt and "- bug: Broken" in prompt and "- praise" in prompt


async def test_decide_score_and_noul() -> None:
    llm, _ = _client('{"level": 2}')
    a = await llm.decide("x", "s", Score("How bad?", ["low", "mid", "high"]))
    assert a is not None and a.value == 2.0 and a.qtype == "score"

    llm, _ = _client('{"level": 7}', '{"level": 9}')
    assert await llm.decide("x", "s", Score("How bad?", ["low", "high"])) is None

    llm, _ = _client('{"answer": false}')
    a = await llm.decide("x", "n", Noul("Spam?"))
    assert a is not None and a.value == 0.0 and a.probabilities == {"true": 0.0, "false": 1.0}


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
