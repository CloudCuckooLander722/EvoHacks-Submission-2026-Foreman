"""RunContext passed to every tool.

One object carries the clients, the gate, the ledger and the threshold for a run, so a
tool's `run()` signature never changes when we add plumbing.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

from foreman.config import get_settings
from foreman.core.decide import Decider, JevLike, LLMLike
from foreman.core.ledger import Ledger


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

    async def aclose(self) -> None:
        """Close both clients' connection pools."""
        for client in (self.jev, self.llm):
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()


def new_run_id() -> str:
    """Short, URL-safe run id such as `r_8f2c1a9b`."""
    return f"r_{uuid.uuid4().hex[:8]}"


async def make_context(
    run_id: str | None = None,
    threshold: float | None = None,
    fake: bool = False,
    jev: JevLike | None = None,
    llm: LLMLike | None = None,
) -> RunContext:
    """Build a context from settings: real clients, or FakeJev/FakeLLM when `fake=True`.

    Passing `jev`/`llm` overrides either client, which is how tests inject fakes with rules.
    """
    settings = get_settings()
    run_id = run_id or new_run_id()
    threshold = settings.confidence_threshold if threshold is None else threshold
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
    return RunContext(
        run_id=run_id, threshold=threshold, jev=jev, llm=llm, decider=decider, ledger=ledger
    )
