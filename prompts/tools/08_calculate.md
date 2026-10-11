# Tool 08 — calculate

Paste everything below the line into Claude Code. Prompts 00 and 01 must already be done.

---

Build the **calculate** tool.

**Read first:**
- `CLAUDE.md`
- `docs/ARCHITECTURE.md` §3–6
- `foreman/tools/base.py`
- `foreman/core/state.py`
- `foreman/core/ledger.py`

**Only create or edit:**
- `foreman/tools/calculate.py`
- `tests/tools/test_calculate.py`

## What it does

Calculate does all the math, counting, date arithmetic and aggregation in plain Python. **No model is ever called.** It exists because Jev (and LLMs) are unreliable at numbers, so every number in a Foreman result comes from code.

`name = "calculate"`, `uses = "code"`.

The planner-facing `description` lists the operations and says to use it for any totals, counts, group-bys, percentages or date differences.

## Params

| Param | Type | Default | Meaning |
|---|---|---|---|
| `operations` | `list[Operation]` | required | At least one. |
| `output_field` | | `"calc"` | Unused by most operations, but it is required by the base class. |
| `today` | `str \| None` | `None` | ISO date for `days_since`. Defaults to today's date. |

`Operation` is a pydantic model with:
- `op`: a Literal (the list below).
- `as_`: the output name, alias `"as"`, required.
- `field`, `a`, `b`, `by`: str or None, plus `n`, `edges`, `labels`, `ndigits`, `desc`.
- `where`: `Condition` or None, ANDed with the tool-level `where`.

Each op validates its required arguments with a `model_validator`.

### Row operations (write `item.fields[as]` for each matching item)

| op | Arguments | Result |
|---|---|---|
| `parse_number` | `field` | Float, from strings like "$1,234.50", "(12.00)" (negative), "12%" (12.0), "1 234,5" (handle the comma-decimal only when unambiguous), "-", or empty (None). |
| `parse_date` | `field` | ISO date via `dateutil.parser.parse(..., default=...)`; None on failure. |
| `add` / `sub` / `mul` / `div` | `a`, `b` | Field names or numeric literals; division by zero gives None. |
| `abs`, `round` | `field`, `ndigits` | |
| `date_diff_days` | `a`, `b` | Date fields → `(b - a).days`. |
| `days_since` | `field` | Days between `today` and the date. |
| `bucket` | `field`, `edges`, `labels` | Label for the bin; `len(labels) == len(edges) + 1`. |

### Aggregate operations (write `run.aggregates[as]`)

| op | Arguments | Result |
|---|---|---|
| `count` | | Number of matching items. |
| `count_true` | `field` | |
| `sum` / `mean` / `median` / `min` / `max` | `field` | |
| `group_count` | `by` | Dict sorted by count, descending. |
| `group_sum` / `group_mean` | `by`, `field` | |
| `share` | `by` | Percent of matching items per value, rounded to 1 decimal. |
| `top_n` | `field`, `n`, `desc` | List of ids. |

## Algorithm (override `run()`)

1. Run the operations in order. Later operations can use fields written by earlier ones.
2. Numeric aggregates always use the shared `to_number()` helper (the same one `parse_number` uses), so "$1,200" sums correctly. Values that aren't numeric are skipped and counted.
3. Add one ledger `Decision` per operation:
   - `item_id="__run__"`, `question_id=as`, `decided_by="code"`, `kind="compute"`;
   - `value` = the aggregate result, or `{"rows_written": n, "skipped": k}` for row operations;
   - `confidence` 1.0.
4. **Report.** `code_ops` = rows written plus aggregates produced. Notes list the skipped counts per operation.

## Tests (offline)

- Every op.
- `to_number` edge cases: currency, parentheses, percent, commas, empty, junk.
- Date parsing and diffs, including a missing date giving None.
- Per-op `where` plus tool-level `where`.
- Ops chaining (`parse_number`, then `group_sum` on its output).
- Ledger entries are `kind="compute"`.
- A validator error when `as` is missing or a required argument is absent.

## Done when

- `pytest -q` passes.
- The dev CLI runs on `reviews_small.json` (no `--fake` needed, no keys used) with `{"operations": [{"op": "count", "as": "n"}, {"op": "group_count", "by": "rating", "as": "by_rating"}, {"op": "mean", "field": "rating", "as": "avg_rating"}]}`.
- You've committed with `feat(tools): calculate`.
