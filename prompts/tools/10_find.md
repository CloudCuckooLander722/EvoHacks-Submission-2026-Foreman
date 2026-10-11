# Tool 10 — find

Paste everything below the line into Claude Code. Prompts 00 and 01 must already be done.

---

Build the **find** tool.

**Read first:**
- `CLAUDE.md`
- `docs/ARCHITECTURE.md` §3–6
- `foreman/tools/base.py`
- `foreman/core/*`
- `foreman/testing/fakes.py`

**Only create or edit:**
- `foreman/tools/find.py`
- `tests/tools/test_find.py`
- `data/samples/find.json`

## What it does

Find locates the passages in a long document that answer a question, such as "the termination clause" or "what the refund window is". It works in four steps:
1. Code splits the document into numbered units.
2. Jev picks the best unit ids with a Choice over the unit ids, window by window, and a Noul says whether the answer is present at all.
3. A final Choice ranks the best candidates.
4. Downstream tools such as Verify or Extract can then work on the short passage instead of the whole document.

Each item is one document.

`name = "find"`, `uses = "jev"`.

## Params

| Param | Type | Default | Meaning |
|---|---|---|---|
| `query` | `str` | required | |
| `unit` | `Literal["line", "sentence", "paragraph"]` | `"paragraph"` | |
| `top_k` | `int` | `3` | |
| `window_units` | `int` | `200` | Maximum 250, because the Choice limit is 255. |
| `context_units` | `int` | `1` | Neighbours included in the returned snippet. |
| `output_field` | | `"matches"` | |

## Algorithm

Override `run()`. Process items concurrently.

1. **Split** the input text into units in code:
   - `line`: non-empty lines.
   - `paragraph`: split on blank lines, merging fragments under 40 characters into the next unit.
   - `sentence`: the same splitter rules Verify uses (write your own small one here; don't import Verify's).
   - Ids are `u1…uN`. Keep each unit's start character offset.

2. **Windows:** consecutive units with at most `window_units` per window and at most about 24k estimated tokens (characters / 4).

3. **Per window**, make one `ctx.decider.decide` call:
   - **State:** `{"units": {"u12": "...", ...}}`.
   - **Question `best`:** a Choice with `fallback=False`.
     - Instructions: f"Which unit in `units` best answers or matches this request: {query}"
     - Options: each unit id with a `null` description, plus `none: "No unit answers the request"` last.
   - **Question `present`:** a Noul with fallback allowed.
     - Instructions: f"Does any unit in `units` contain information that answers this request: {query}?"

4. **Candidates.**
   - From windows where `present` ≥ 0.5 (or from every window if none qualify), take the `top_k` units by `best` probability, excluding `none`.
   - If more than `top_k` candidates remain overall, make a final `decide` call. Its state holds only the candidate units, plus one neighbour on each side for context, keyed by their original ids. It asks the same `best` Choice over the candidate ids. Order the candidates by its probabilities.
   - Otherwise, order the candidates by their window probabilities.

5. **Outputs.**
   - `fields[out]` = a list of `{unit_id, text: <unit with context_units neighbours, joined>, score: round(p, 3), char_start}`, via `set_output`. Confidence comes from the final (or single-window) `best` answer.
   - `fields[f"{out}_present"]` = whether the maximum `present` p is at least 0.5.
   - `fields[f"{out}_top"]` = the text of the top unit alone, which is convenient for Verify and Extract.

6. **Edge cases:**
   - An empty document gives `[]` and present=False, with `by="code"`.
   - A one-unit document skips the model.

## Sample data

`data/samples/find.json`: 2 items, each a 40–80 paragraph synthetic terms-of-service document. Give them distinct clauses: termination, refunds, data retention, liability, governing law.

## Tests (offline)

- The splitters.
- Windowing respects both limits.
- A `FakeJev` rule makes `u37` best. Assert the output, and that the final rerank call happens only when needed.
- `present` false everywhere still returns the top candidates, with present=False.
- An empty document.
- Choice option count stays at or below 255.

## Done when

- `pytest -q` passes.
- The dev CLI runs on the sample with `--fake` and `{"query": "When can the company terminate my account?"}`.
- You've committed with `feat(tools): find`.
