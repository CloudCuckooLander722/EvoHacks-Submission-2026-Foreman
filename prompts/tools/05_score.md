# Tool 05 — score

Paste everything below the line into Claude Code. Prompts 00 and 01 must already be done.

---

Build the **score** tool, a composite scorer.

**Read first:**
- `CLAUDE.md`
- `docs/ARCHITECTURE.md` §3–6
- `foreman/tools/base.py`
- `foreman/core/*`
- `foreman/testing/fakes.py`

**Only create or edit:**
- `foreman/tools/score.py`
- `tests/tools/test_score.py`

## What it does

Score rates each item on several separate dimensions, such as severity, number of users affected, and how clearly it is described. Each dimension is one Jev Score question, and the weights live in code. All the dimensions are asked in **one** Jev call per item, because Jev evaluates every question in a call in parallel.

**Why:** Jev is good at single gut-check judgments, not at weighing many factors at once. So we split the judgment into dimensions and combine them ourselves.

`name = "score"`, `uses = "jev"`.

The planner-facing `description` should:
- explain this;
- tell the planner to write levels as concrete, ordered descriptions.

## Params

| Param | Type | Default | Meaning |
|---|---|---|---|
| `dimensions` | `list[Dimension]` | required | 1–8 items. |
| `scale` | `float` | `100.0` | |
| `output_field` | | `"score"` | |

Each `Dimension` has:
- `name`: a slug, unique.
- `instructions`: str, e.g. "How severe is the problem described in the text for the user?"
- `levels`: `list[str]`, 2–10 entries, ordered low → high.
- `weight`: float, greater than 0, default 1.0.

Validate everything: unique names, the level count, positive weights.

## Algorithm

Subclass `PerItemTool`.

- **`questions()`**: `{f"dim_{d.name}": Score(instructions=d.instructions, levels=d.levels)}` for each dimension.
- **`apply()`**:
  - For each dimension, `norm = answer.value / (len(levels) - 1)`, clamped to the range 0–1.
  - Write `fields[f"{out}_{name}"] = round(norm * scale, 1)` and `fields[f"{out}_{name}__confidence"]`.
  - Composite = `sum(w * norm) / sum(w) * scale`, rounded to 1 decimal.
  - Write it with `set_output(item, out, composite, confidence=min(dim confidences), by="llm" if any dimension came from the LLM else "jev", low_confidence=any(...))`.
- **Aggregates**: after the run, `run.aggregates[f"{out}_summary"] = {mean, median, min, max, per_dimension_mean}`. Compute it in code over the processed items, using `statistics`.

The docstring must note that Jev's expected level is used for ordering and thresholds, not as an exact measurement.

## Tests (offline)

- Two dimensions with different weights. A `FakeJev` rule sets the expected levels, so the composite math is exact.
- Exactly one Jev call per item, carrying all the dimension questions.
- `by` is `"llm"` if one dimension falls back.
- The validator rejects duplicate names, 1 level, 11 levels and zero weight.
- Summary aggregates are correct.

## Done when

- `pytest -q` passes.
- The dev CLI runs on `reviews_small.json` with `--fake` and two dimensions: severity (4 levels) and reach (3 levels).
- You've committed with `feat(tools): score`.
