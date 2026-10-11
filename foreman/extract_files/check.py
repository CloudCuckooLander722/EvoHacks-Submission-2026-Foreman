"""Optional readability check after extraction (MINH_PLAN §2 point 2).

"Is this readable text or garbage?" is a single gut-check judgment, which is what Jev is
good at. It goes through `Decider.decide()` like every other model decision, so the
confidence bar, the LLM fallback and the ledger all apply.
"""

from __future__ import annotations

import asyncio
import logging

from foreman.contracts import ItemsFile
from foreman.core.context import RunContext
from foreman.core.questions import Noul

log = logging.getLogger(__name__)

READABLE = Noul(
    "Is the text in the state readable words or table data?",
    true="Mostly readable words, sentences or table values",
    false="Mostly garbled characters, binary noise or broken OCR",
)
ACCEPT = 0.75  # P(readable) above this: fine
REJECT = 0.25  # below this: garbage; in between: a warning
STATE_CHARS = 8000


async def readability(text: str, item_id: str, ctx: RunContext) -> float:
    """P(readable) for one item; gate, fallback and ledger are handled by the decider."""
    answers = await ctx.decider.decide(
        item_id=item_id,
        tool="extract_files",
        state=text[:STATE_CHARS],
        questions={"readable": READABLE},
    )
    return float(answers["readable"].value)


async def check_readability(items: ItemsFile, ctx: RunContext, sample: int = 20) -> list[str]:
    """Check up to `sample` items spread across the file; return warnings (empty if fine)."""
    pool = items.items
    if not pool:
        return []
    step = max(1, len(pool) // sample)
    chosen = pool[::step][:sample]
    probs = await asyncio.gather(*(readability(i.text, i.id, ctx) for i in chosen))
    bad = [i.id for i, p in zip(chosen, probs, strict=True) if p < REJECT]
    unsure = [i.id for i, p in zip(chosen, probs, strict=True) if REJECT <= p <= ACCEPT]
    warnings: list[str] = []
    if bad:
        warnings.append(
            f"{len(bad)} of {len(chosen)} sampled items look unreadable: {', '.join(bad[:5])}"
        )
    if unsure:
        warnings.append(
            f"{len(unsure)} of {len(chosen)} sampled items may be partly garbled: "
            f"{', '.join(unsure[:5])}"
        )
    return warnings
