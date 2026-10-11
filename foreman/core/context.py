"""RunContext passed to every tool.

One object carries the clients, the gate, the ledger and the threshold for a run, so a
tool's `run()` signature never changes when we add plumbing.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from foreman.config import get_settings
from foreman.core.decide import Decider, JevLike, LLMLike
from foreman.core.ledger import Ledger
from foreman.core.questions import Noul

log = logging.getLogger(__name__)


@dataclass
class RunContext:
    """Everything a tool needs besides the run state and its params."""

    run_id: str
    threshold: float
    jev: JevLike
    llm: LLMLike
    decider: Decider
    ledger: Ledger
    progress: Callable[[str, int, int], None] | None = None
    warmups: list[asyncio.Task[None]] = field(default_factory=list)

    async def aclose(self) -> None:
        """Cancel unfinished warm-ups, then close both clients' connection pools."""
        for task in self.warmups:
            if not task.done():
                task.cancel()
        if self.warmups:
            await asyncio.gather(*self.warmups, return_exceptions=True)
        for client in (self.jev, self.llm):
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()


async def warm_jev(jev: JevLike) -> None:
    """One tiny Noul so the first node's calls skip connection setup. Never raises.

    Its cost is not charged to the run's ledger.
    """
    try:
        await jev.ask("Hello", {"warm": Noul("Is the text a greeting?", fallback=False)})
    except Exception as e:  # noqa: BLE001 - warming is best-effort
        log.info("Jev warm-up call failed (ignored): %s", e)


def start_warmups(ctx: RunContext, *, jev: bool = True, llm: bool = True) -> None:
    """Warm Jev and/or the LLM in the background while the run loads, plans or starts."""
    if jev:
        ctx.warmups.append(asyncio.create_task(warm_jev(ctx.jev)))
    warm_llm = getattr(ctx.llm, "warm", None)
    if llm and warm_llm is not None:
        ctx.warmups.append(asyncio.create_task(warm_llm()))


def new_run_id() -> str:
    """Short, URL-safe run id such as `r_8f2c1a9b`."""
    return f"r_{uuid.uuid4().hex[:8]}"


async def make_context(
    run_id: str | None = None,
    threshold: float | None = None,
    fake: bool = False,
    jev: JevLike | None = None,
    llm: LLMLike | None = None,
    warm: bool = True,
) -> RunContext:
    """Build a context from settings: real clients, or FakeJev/FakeLLM when `fake=True`.

    Passing `jev`/`llm` overrides either client, which is how tests inject fakes with rules.
    Each live client built here gets one background warm-up call (`warm=False` turns that
    off), so the first node and the first fallback don't pay for connection setup and the
    provider's cold start. Injected clients are never warmed: their calls are the caller's.
    """
    settings = get_settings()
    run_id = run_id or new_run_id()
    threshold = settings.confidence_threshold if threshold is None else threshold
    live_jev = jev is None and not fake
    live_llm = llm is None and not fake
    if jev is None or llm is None:
        if fake:
            from foreman.testing.fakes import FakeJev, FakeLLM

            jev = jev or FakeJev()
            llm = llm or FakeLLM()
        else:
            from foreman.core.jev import JevClient
            from foreman.core.llm import LLMClient

            jev = jev or JevClient(settings)
            llm = llm or LLMClient(settings)
    ledger = Ledger(run_id)
    decider = Decider(jev, llm, ledger, run_id, threshold)
    ctx = RunContext(
        run_id=run_id, threshold=threshold, jev=jev, llm=llm, decider=decider, ledger=ledger
    )
    if warm and (live_jev or live_llm):
        start_warmups(ctx, jev=live_jev, llm=live_llm)
    return ctx
