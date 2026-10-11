# Tool 03 — group

Paste everything below the line into Claude Code. Prompts 00 and 01 must already be done.

---

Build the **group** tool.

**Read first:**
- `CLAUDE.md`
- `docs/ARCHITECTURE.md` §3–6
- `foreman/tools/base.py`
- `foreman/core/*`
- `foreman/testing/fakes.py`

**Only create or edit:**
- `foreman/tools/group.py`
- `tests/tools/test_group.py`
- `data/samples/group.json` (optional)

## What it does

Group discovers themes in a pile of items and assigns every item to one theme, for example "10,000 reviews → 8 themes with counts". The LLM is used **only** to propose theme names from a small sample (at most two calls per run). Jev assigns every item.

`name = "group"`, `uses = "jev+llm"`.

The planner-facing `description` should say:
- Use Group when the categories aren't known in advance.
- Use Classify when they are.

## Params

| Param | Type | Default | Meaning |
|---|---|---|---|
| `themes` | `list[Theme] \| None` | `None` | `Theme(name: str, description: str)`. If given, skip theme discovery. |
| `n_themes` | `int` | `8` | Range 2–30. |
| `focus` | `str \| None` | `None` | What the themes should be about, e.g. "the product problem described". |
| `sample_size` | `int` | `60` | |
| `allow_new_themes` | `bool` | `True` | |
| `max_new_themes` | `int` | `3` | |
| `outlier_min` | `int` | `10` | |
| `seed` | `int` | `7` | |
| `output_field` | | `"theme"` | |

## Algorithm (override `run()`)

1. **Select items** matching `where`.

2. **Propose themes** (only if `themes` is None).
   - Take a seeded random sample of `min(sample_size, n)` items, each truncated to 300 characters.
   - Call `ctx.llm.json(system, user)`. The system prompt asks for exactly `n_themes` themes covering the sample, about `focus` when given:
     - Names: short, 1–4 words, distinct.
     - Descriptions: one sentence stating what belongs, plus one boundary ("not X").
     - Format: `{"themes": [{"name": ..., "description": ...}]}`.
   - Validate the result: dedupe names case-insensitively and trim to `n_themes`. If fewer than 2 are valid, raise a clear error.
   - Add a ledger `Decision` with `item_id="__run__"`, `tool="group"`, `question_id="themes"`, `decided_by="llm"`, `kind="proposal"`, the themes as `value`, and `cost_usd` from the LLM usage if available (else 0).

3. **Assign.** Per item, one Choice through `ctx.decider.decide`, run with `gather_bounded`.
   - Option keys = slugified theme names (keep a slug → name map); option descriptions = the theme descriptions.
   - Add `"none_of_these": "The text fits none of the themes above"` last.
   - Instructions: "Which theme best describes {focus or 'the text'}?"

4. **Refine** (one round only).
   - Outliers = items whose answer is `none_of_these`.
   - Refine only if `allow_new_themes` is set and the outlier count ≥ `max(outlier_min, 3% of selected items)`.
   - Send the LLM up to 40 outlier texts plus the existing theme names. Ask for at most `max_new_themes` NEW themes that don't overlap the existing ones. Ledger it as a proposal.
   - Re-assign **only the outliers** against the full theme list (old + new + `none_of_these`).

5. **Write outputs.** `set_output(item, out, <theme display name or "none_of_these">, confidence, by)`.

6. **Aggregates.** Set `run.aggregates[f"{out}_groups"]` to a list of `{theme, description, count, example_ids (first 3)}` sorted by count descending, with `none_of_these` last if present.

7. **Report.** Include in the notes how many themes were proposed and whether refinement ran.

## Tests (offline)

Use a `FakeLLM` with `json_responses`: one for the initial themes, one for the refinement themes. Key them by substrings of the prompts you write.

- Themes are proposed, then items are assigned (a `FakeJev` rule maps keywords to slugs). Assert the counts in the aggregates and that the LLM was called once.
- With provided `themes`, `FakeLLM.json` is never called.
- Refinement triggers when enough outliers exist, only outliers are re-asked (count the `FakeJev` calls), and the new themes appear in the aggregates.
- Refinement doesn't trigger below the minimum.
- The validator dedupes theme names.

## Done when

- `pytest -q` passes.
- The dev CLI runs on `reviews_small.json` with `--fake` (the fake has no JSON response for the dev CLI, so the CLI with `--fake` should pass `themes` in `--params`).
- You've committed with `feat(tools): group`.
