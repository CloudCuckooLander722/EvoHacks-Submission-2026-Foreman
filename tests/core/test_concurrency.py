"""Bounded gather and rate limiter."""

import asyncio
import time

import pytest

from foreman.core.concurrency import RateLimiter, gather_bounded


async def test_gather_bounded_order_limit_progress() -> None:
    running = 0
    peak = 0
    progress: list[tuple[int, int]] = []

    def make(i: int):
        async def f() -> int:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01 * (5 - i % 5))
            running -= 1
            return i

        return f

    out = await gather_bounded(
        [make(i) for i in range(10)], limit=3, on_done=lambda d, t: progress.append((d, t))
    )
    assert out == list(range(10))
    assert peak <= 3
    assert progress[-1] == (10, 10)
    assert len(progress) == 10


async def test_gather_bounded_reraises_after_all_finish() -> None:
    finished: list[int] = []

    def make(i: int):
        async def f() -> int:
            if i == 0:
                raise RuntimeError("boom")
            await asyncio.sleep(0.01)
            finished.append(i)
            return i

        return f

    with pytest.raises(RuntimeError, match="boom"):
        await gather_bounded([make(i) for i in range(4)], limit=4)
    assert sorted(finished) == [1, 2, 3]


async def test_rate_limiter_paces() -> None:
    limiter = RateLimiter(20)
    t0 = time.monotonic()
    for _ in range(30):
        async with limiter:
            pass
    # 20 burst tokens, then 10 more at 20/s => about 0.5s.
    assert time.monotonic() - t0 >= 0.4
