"""Jev client: request shape, parsing, retries, limits."""

import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from foreman.config import Settings, get_settings
from foreman.core.jev import JevClient, JevError, StateTooLarge
from foreman.core.questions import Choice, Noul, Score

URL = "https://jev.test/typesafe/v1/systemone"


def _settings(**kw) -> Settings:
    base = {
        "ai_gateway_api_key": SecretStr("test-key"),
        "jev_base_url": "https://jev.test/typesafe",
        "jev_max_rps": 1000,
    }
    base.update(kw)
    return Settings(_env_file=None, **base)


QUESTIONS = {
    "spam": Noul("Is the text in `review` spam?", true="An ad or gibberish", false="A real review"),
    "type": Choice("What kind of review is it?", {"bug": "Something broken", "other": None}),
    "anger": Score("How angry is the reviewer?", ["Calm", "Annoyed", "Furious"]),
}

RESPONSE = {
    "answers": {
        "spam": {"noul": 0.1},
        "type": {"choice": "bug", "probabilities": {"bug": 0.95, "other": 0.05}, "confidence": 0.9},
        "anger": {
            "score": 1.6,
            "legend": ["Calm", "Annoyed", "Furious"],
            "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
            "confidence": 0.6,
        },
    },
    "usage": {"input_tokens": 120},
    "provider_metadata": {"gateway": {"cost": "0.00000504"}},
}


@respx.mock
async def test_request_body_and_parsing() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=RESPONSE))
    async with JevClient(_settings()) as jev:
        res = await jev.ask({"review": "App crashes"}, QUESTIONS)

    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer test-key"
    body = json.loads(req.content)
    assert body["model"] == "typesafe-ai/jev"
    assert body["state"] == {"review": "App crashes"}
    assert body["questions"] == {
        "spam": {
            "type": "noul",
            "instructions": "Is the text in `review` spam?",
            "criteria": {"true": "An ad or gibberish", "false": "A real review"},
        },
        "type": {
            "type": "choice",
            "instructions": "What kind of review is it?",
            "criteria": {"bug": "Something broken", "other": None},
        },
        "anger": {
            "type": "score",
            "instructions": "How angry is the reviewer?",
            "criteria": ["Calm", "Annoyed", "Furious"],
        },
    }

    assert res.answers["spam"].value == pytest.approx(0.1)
    assert res.answers["spam"].confidence == pytest.approx(0.8)
    assert res.answers["type"].value == "bug"
    assert res.answers["type"].confidence == 0.9
    assert res.answers["anger"].value == 1.6
    assert res.answers["anger"].probabilities["2"] == 0.7
    assert all(a.decided_by == "jev" for a in res.answers.values())
    assert res.input_tokens == 120
    assert res.cost_usd == pytest.approx(0.00000504)


@respx.mock
async def test_cost_falls_back_to_price() -> None:
    body = {**RESPONSE, "provider_metadata": {}}
    respx.post(URL).mock(return_value=httpx.Response(200, json=body))
    async with JevClient(_settings()) as jev:
        res = await jev.ask("x", QUESTIONS)
    assert res.cost_usd == pytest.approx(120 * 0.042 / 1e6)


@respx.mock
async def test_retries_429_then_succeeds() -> None:
    route = respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "0"}, json={"message": "slow down"}),
            httpx.Response(529, json={"message": "overloaded"}),
            httpx.Response(200, json=RESPONSE),
        ]
    )
    async with JevClient(_settings(), backoff_base_s=0.001) as jev:
        res = await jev.ask("x", QUESTIONS)
    assert route.call_count == 3
    assert res.answers["type"].value == "bug"


@respx.mock
async def test_retries_exhausted() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(503, json={"message": "down"}))
    async with JevClient(_settings(), backoff_base_s=0.001) as jev:
        with pytest.raises(JevError, match="5 attempts"):
            await jev.ask("x", QUESTIONS)
    assert route.call_count == 5


@respx.mock
async def test_timeout_is_retried() -> None:
    route = respx.post(URL).mock(
        side_effect=[httpx.ReadTimeout("slow"), httpx.Response(200, json=RESPONSE)]
    )
    async with JevClient(_settings(), backoff_base_s=0.001) as jev:
        await jev.ask("x", QUESTIONS)
    assert route.call_count == 2


@respx.mock
async def test_4xx_raises_with_message() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(
            422, json={"message": "bad criteria", "error_type": "invalid_request"}
        )
    )
    async with JevClient(_settings()) as jev:
        with pytest.raises(JevError) as ei:
            await jev.ask("x", QUESTIONS)
    assert ei.value.status == 422
    assert ei.value.message == "bad criteria"
    assert ei.value.error_type == "invalid_request"
    assert route.call_count == 1


async def test_state_too_large() -> None:
    async with JevClient(_settings(jev_max_state_tokens=10)) as jev:
        with pytest.raises(StateTooLarge):
            await jev.ask("x" * 100, QUESTIONS)


@pytest.mark.parametrize(
    "q",
    [Choice("x", {}), Score("x", ["only"]), Noul("")],
)
async def test_validation_errors(q) -> None:
    async with JevClient(_settings()) as jev:
        with pytest.raises(ValueError):
            await jev.ask("x", {"q": q})


async def test_missing_key() -> None:
    async with JevClient(_settings(ai_gateway_api_key=None)) as jev:
        with pytest.raises(JevError, match="AI_GATEWAY_API_KEY"):
            await jev.ask("x", QUESTIONS)


@pytest.mark.live
async def test_live_noul() -> None:
    if get_settings().ai_gateway_api_key is None:
        pytest.skip("AI_GATEWAY_API_KEY not set")
    async with JevClient() as jev:
        res = await jev.ask(
            "The app crashes every time I open the camera.",
            {"bug": Noul("Does the text describe a software bug?")},
        )
    assert res.answers["bug"].value > 0.5
