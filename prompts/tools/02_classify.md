# Tool 02 — classify

Paste everything below the line into Claude Code. Prompts 00 and 01 must already be done.

---

Build the **classify** tool.

**Read first:**
- `CLAUDE.md`
- `docs/ARCHITECTURE.md` §3–6
- `foreman/tools/base.py`
- `foreman/core/questions.py`
- `foreman/core/decide.py`
- `foreman/testing/fakes.py`

**Only create or edit:**
- `foreman/tools/classify.py`
- `tests/tools/test_classify.py`
- `data/samples/classify.json` (optional)

## What it does

Classify puts each item into exactly one category. It supports three cases:
1. A flat list of up to about 250 options.
2. A very large flat list, run as a tournament.
3. A category tree, walked level by level with beam search over Jev's probabilities.

`name = "classify"`, `uses = "jev"`.

The planner-facing `description` should cover:
- what Classify does;
- that option descriptions should state boundaries ("billing: charges, refunds, invoices — not login problems");
- to use `taxonomy` for nested categories.

## Params

| Param | Type | Default | Meaning |
|---|---|---|---|
| `options` | `dict[str, str \| None] \| None` | `None` | Category key → description. Keys are short slugs. |
| `taxonomy` | `dict[str, Node] \| None` | `None` | Tree of `Node(description: str \| None = None, children: dict[str, Node] = {})`. Leaves have no children. |
| `instructions` | `str \| None` | `None` | Override for the question. Default: "Which category best describes the text?" |
| `add_other` | `bool` | `True` | Adds `"other": "None of the listed categories fits the text"`, last. |
| `beam_width` | `int` | `2` | Range 1–4. |
| `top_n_probs` | `int` | `3` | |
| `output_field` | | `"category"` | |

A validator requires exactly one of `options` and `taxonomy`. Option keys must be non-empty and unique.

## Algorithm

Override `run()`. Use `ctx.decider.decide` per item with bounded concurrency through `gather_bounded`. You may reuse helpers from `base.py` for `where`, error handling and reports.

### Flat, at most 255 options in total (including `other`)

One Choice per item. The value is the chosen key.

### Flat, more than 255 options: tournament

1. Split the options into chunks of at most 200.
2. Ask **all** chunk questions in a **single** Jev call. Use question ids `chunk_0`, `chunk_1` and so on, with the same instructions and that chunk's options. One call works because every question in a call is evaluated in parallel against the same state.
3. Make a second call: a Choice among the chunk winners, plus `other` if enabled.
4. Final value = the winner. Confidence = the minimum of the final answer's confidence and the winning chunk's confidence.

### Taxonomy: beam search

1. **Level 1:** a Choice over the root's children.
2. Keep the top `beam_width` children by probability. Prune any below 0.1 probability, but always keep at least one.
3. **Next level:** ask the children-choice for every kept non-leaf node in **one** Jev call (one question per node, id `n_<path>`).
4. **Path probability** = the product of the probabilities along the path. When an answer came from the LLM fallback, its probabilities are `{choice: 1.0}`; treat that as 1.0 for the chosen key and 0 for the others.
5. Continue until every beam path ends at a leaf, with a maximum depth of 6.
6. Pick the highest-probability path.
7. Write the value (the leaf key) and `fields[f"{out}_path"]` (e.g. `"hardware > camera > crash"`).
8. Confidence = the minimum confidence along the chosen path.

Add `other` at each level only if `add_other` is set. Picking `other` at any level ends that path with the value `"other"`.

### Outputs

Use `set_output(item, out, value, confidence=..., by=...)`. `by` is `"llm"` if any decision on the chosen path came from the LLM, otherwise `"jev"`.

Also write `fields[f"{out}__probs"]`: the top `top_n_probs` of the final question's probabilities, rounded to 3 decimals.

Store counts per class in `run.aggregates[f"{out}_counts"]`, sorted descending.

## Tests (offline)

- Flat classification on review texts, using the `FakeJev` keyword default or a rule. Check the values, `__probs` and the aggregates.
- Picking `other`.
- Tournament with 600 synthetic options where the rule makes `opt_437` win. Assert exactly 2 Jev calls for the item, and the final value `opt_437`.
- A 3-level taxonomy. The rule makes the level-1 top choice lead to a weaker leaf than the second choice. Assert that the beam finds the better path, and that the number of Jev calls is the depth plus at most one per level.
- A low-confidence answer falls back to the LLM, so `by == "llm"`.
- The validator rejects having both `options` and `taxonomy`, and empty keys.

## Done when

- `pytest -q` passes.
- The dev CLI runs on `reviews_small.json` with `--fake`, using `{"options": {"bug": "Something broken or crashing", "feature_request": "Asks for new functionality", "praise": "Positive feedback without a request"}}`.
- You've committed with `feat(tools): classify`.
