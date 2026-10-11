# Tool 01 — filter

Paste everything below the line into Claude Code. Prompts 00 and 01 must already be done.

---

Build the **filter** tool.

**Read first:**
- `CLAUDE.md`
- `docs/ARCHITECTURE.md` §3–6
- `foreman/tools/base.py`
- `foreman/core/questions.py`
- `foreman/core/decide.py`
- `foreman/testing/fakes.py`

**Only create or edit these files:**
- `foreman/tools/filter.py`
- `tests/tools/test_filter.py`
- `data/samples/filter.json` (you may reuse `reviews_small.json` instead)

The registry auto-discovers the tool, so you do not need to edit any other file.

## What it does

Filter keeps only the items that match a yes/no condition, for example "Is this a genuine product review, not spam or an ad?". It is usually the first node in a plan, because dropping junk early saves every later tool work.

`name = "filter"`, `uses = "jev"`.

The `description` is for the planner. Write 2–3 sentences that say:
- use it to remove irrelevant items, or to split items by a yes/no property;
- phrase `condition` so that "yes" means keep.

## Params (subclass `ToolParams`)

| Param | Type | Default | Meaning |
|---|---|---|---|
| `condition` | `str` | required | A yes/no question about one item, phrased so yes = keep. Validate that it is non-empty. |
| `true_means` | `str \| None` | `None` | Optional description of what qualifies. |
| `false_means` | `str \| None` | `None` | Optional description of what does not. |
| `cutoff` | `float` | `0.5` | Keep when P(yes) is at or above this. |
| `drop` | `bool` | `True` | Remove non-kept items from `run.items` into `run.dropped`. |
| `output_field` | | `"keep"` | |

## Algorithm

Subclass `PerItemTool`.

1. `questions()` returns `{"keep": Noul(instructions=<condition, rewritten to refer to "the text">, true=true_means, false=false_means)}`.
   - Write a small helper that prefixes the condition with "About the text:" if it doesn't already mention "text". Jev does better when the question names the state.
2. `apply()` computes `keep = answer.value >= cutoff`. It writes `set_output(item, out, keep, confidence=answer.confidence, by=answer.decided_by, low_confidence=answer.low_confidence)` and also `fields[f"{out}__p"] = round(answer.value, 3)`.
3. Override `run()`. Call `super().run()`, then, if `drop` is true, move the processed items whose output is False from `run.items` into `run.dropped`.
   - Set `item.meta["drop_reason"] = f"filter: {condition}"` on each dropped item.
   - Items that did not match `where` are never dropped.
   - Set `report.items_out` to the number of items remaining from the processed set.

## Edge cases

- Empty text: skip the model call. Set `keep=False`, `by="code"` and confidence 1.0.
- An item with an `__error` field from the base class stays in `run.items`. Never drop on an error.

## Tests (offline, using `make_context(fake=True)` plus `FakeJev` rules)

- Spam vs review: a `FakeJev` rule returns p = 0.05 for items containing "buy followers" and 0.95 otherwise. Assert the counts in `run.items` and `run.dropped`, and the drop reason.
- `drop=False` keeps every item and still writes the field.
- `where` is respected: unmatched items have no output field and are not dropped.
- Low confidence (a rule with p = 0.55) triggers the LLM fallback. Assert `__by == "llm"`.
- `FakeLLM(fail=True)` produces `__low_confidence`.
- Empty text follows the code path.

## Done when

- `pytest -q` passes.
- This command prints a sensible table:

  ```
  python -m foreman.dev tool filter --items data/samples/reviews_small.json --params '{"condition":"Is this a genuine review of the app (not spam or an ad)?"}' --fake
  ```

- You've committed with `feat(tools): filter`.
