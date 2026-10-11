"""make_context warm-ups: live clients get one background call each; injected ones none."""

import asyncio

import foreman.core.jev as jev_module
import foreman.core.llm as llm_module
from foreman.core.context import make_context, start_warmups
from foreman.core.questions import Noul
from foreman.testing.fakes import FakeJev, FakeLLM


class WarmLLM(FakeLLM):
    def __init__(self, *_args, **_kwargs) -> None:
        super().__init__()
        self.warmed = 0

    async def warm(self) -> None:
        self.warmed += 1


class LiveJev(FakeJev):
    def __init__(self, *_args, **_kwargs) -> None:
        super().__init__()


async def test_live_context_warms_jev_and_llm(monkeypatch) -> None:
    monkeypatch.setattr(jev_module, "JevClient", LiveJev)
    monkeypatch.setattr(llm_module, "LLMClient", WarmLLM)
    ctx = await make_context()
    assert len(ctx.warmups) == 2
    await asyncio.gather(*ctx.warmups)
    await ctx.aclose()
    [(_state, questions)] = ctx.jev.calls
    assert isinstance(questions["warm"], Noul) and questions["warm"].fallback is False
    assert ctx.llm.warmed == 1
    assert ctx.ledger.decisions == [] and ctx.ledger.stats().jev_calls == 0  # not charged


async def test_injected_and_fake_clients_are_not_warmed() -> None:
    jev = FakeJev()
    ctx = await make_context(jev=jev, llm=FakeLLM())
    assert ctx.warmups == []
    ctx = await make_context(fake=True)
    assert ctx.warmups == []
    assert jev.calls == []


async def test_failed_warmup_is_ignored() -> None:
    ctx = await make_context(jev=FakeJev(fail=True), llm=WarmLLM())
    start_warmups(ctx)
    await asyncio.gather(*ctx.warmups)  # raises if a warm-up leaked its error
    await ctx.aclose()
    assert ctx.llm.warmed == 1


async def test_aclose_cancels_unfinished_warmups() -> None:
    ctx = await make_context(jev=FakeJev(latency_ms=10_000), llm=FakeLLM())
    start_warmups(ctx)
    await asyncio.sleep(0)
    await asyncio.wait_for(ctx.aclose(), 1)
    assert all(t.cancelled() for t in ctx.warmups)
