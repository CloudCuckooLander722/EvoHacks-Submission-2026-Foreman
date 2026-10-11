"""Bounded gather with progress callback, and a token-bucket rate limiter.

Tools fan out thousands of per-item calls; these keep that fan-out within provider
limits while still reporting progress to the live meter.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Self


async def gather_bounded[T](
    factories: Sequence[Callable[[], Awaitable[T]]],
    limit: int,
    on_done: Callable[[int, int], None] | None = None,
) -> list[T]:
    """Run coroutine factories at most `limit` at a time, preserving order.

    Factories (not coroutines) are taken so nothing starts before a slot is free. Every
    task is allowed to finish before the first error is re-raised, so one bad item never
    leaves orphaned requests running.
    """
    total = len(factories)
    sem = asyncio.Semaphore(max(1, limit))
    done = 0

    async def run(factory: Callable[[], Awaitable[T]]) -> T:
        nonlocal done
        async with sem:
            try:
                return await factory()
            finally:
                done += 1
                if on_done is not None:
                    on_done(done, total)

    results: list[Any] = await asyncio.gather(*(run(f) for f in factories), return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r
    return results


class RateLimiter:
    """Async token bucket: `async with limiter:` waits until a request may start.

    Capacity equals one second of tokens, so short bursts are allowed but the sustained
    rate never exceeds `rps`.
    """

    def __init__(self, rps: float) -> None:
        if rps <= 0:
            raise ValueError("rps must be positive")
        self.rps = float(rps)
        self.capacity = max(1.0, self.rps)
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Take one token, sleeping until one is available."""
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rps)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                await asyncio.sleep((1.0 - self._tokens) / self.rps)

    async def __aenter__(self) -> Self:
        await self.acquire()
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None
