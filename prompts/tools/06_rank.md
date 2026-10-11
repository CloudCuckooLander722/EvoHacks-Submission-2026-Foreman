# Tool 06 — rank

Paste everything below the line into Claude Code. Prompts 00 and 01 must already be done.

---

Build the **rank** tool.

**Read first:**
- `CLAUDE.md`
- `docs/ARCHITECTURE.md` §3–6
- `foreman/tools/base.py`
- `foreman/core/*`
- `foreman/testing/fakes.py`

**Only create or edit:**
- `foreman/tools/rank.py`
- `tests/tools/test_rank.py`

## What it does

Rank sorts items by a fuzzy quality such as urgency, fit or quality. It works in three steps:
1. Score every item, or reuse a numeric field an earlier node already computed (usually Score's output).
2. Sort in code.
3. Resolve near-ties with cheap pairwise Jev comparisons, asked in both option orders to cancel Jev's slight bias toward the first option.

`name = "rank"`, `uses = "jev"`.

The planner-facing `description` should say: if a Score node already produced a number, pass it as `score_field`.

## Params

| Param | Type | Default | Meaning |
|---|---|---|---|
| `criterion` | `str` | required | The quality to rank by, as a noun phrase, e.g. "urgency of fixing this issue". |
| `levels` | `list[str] \| None` | `None` | 2–10 levels, low → high. |
| `score_field` | `str \| None` | `None` | If set, read this numeric field instead of asking Jev. |
| `descending` | `bool` | `True` | |
| `top_k` | `int \| None` | `None` | Only tie-break within the top k. |
| `tie_break` | `bool` | `True` | |
| `tie_margin` | `float` | `0.05` | Normalized 0–1 score difference treated as a tie. |
| `max_tiebreaks` | `int` | `200` | |
| `output_field` | | `"rank"` | |

Default `levels`, when none are given:
1. f"Very low {criterion}"
2. f"Low {criterion}"
3. f"Moderate {criterion}"
4. f"High {criterion}"
5. f"Very high {criterion}"

## Algorithm (override `run()`)

1. **Select items** matching `where`.

2. **Scores.**
   - **If `score_field` is set:** parse it as a float in code. Non-numeric values sort last. No Jev calls.
   - **Otherwise:** per item, one Score question through `ctx.decider.decide`, with instructions f"How much {criterion} does the text show?". Normalize each score to 0–1.

3. **Sort.** Stable sort by score, honouring `descending`.

4. **Tie-break** (if enabled). Walk the sorted list from the top, within `top_k` if set.
   - For each adjacent pair whose normalized scores differ by at most `tie_margin`, up to `max_tiebreaks` pairs, make **one** Jev call with:
     - State: `{"item_a": text_a[:2000], "item_b": text_b[:2000]}`.
     - Two Choice questions, both with `fallback=False`:
       - `ab`: options `{"item_a": f"item_a shows more {criterion}", "item_b": f"item_b shows more {criterion}", "equal": "They are about the same"}`.
       - `ba`: the same three options with `item_b` listed first, then `item_a`, then `equal`.
   - **Swap** the pair only if both questions pick the lower-ranked item. If the two answers disagree, keep the current order.
   - Do a single top-to-bottom pass. Don't re-check after a swap.
   - Record each tie-break in the ledger. The Decider does this automatically; use `item_id=f"{a.id}|{b.id}"`.

5. **Outputs.**
   - `fields[out]` = rank (1-based) via `set_output`, with the confidence of the item's score answer (1.0 when `score_field` was used, and `by="code"` in that case).
   - `fields[f"{out}_score"]` = the normalized score, rounded to 3 decimals.
   - `run.aggregates[f"{out}_order"]` = the ids in rank order.

6. **Report.** Notes give the number of tie-breaks run and the number of swaps.

## Tests (offline)

- Ordering from `FakeJev` score rules.
- `score_field` reuse makes zero Jev calls and gives `by == "code"`.
- A tie-break swaps when both orders agree.
- No swap when they disagree.
- `top_k` limits the tie-breaks.
- `max_tiebreaks` is respected.
- Non-numeric `score_field` values go last.

## Done when

- `pytest -q` passes.
- The dev CLI runs with `--fake` and `{"criterion": "urgency of fixing this issue for users", "top_k": 10}`.
- You've committed with `feat(tools): rank`.
