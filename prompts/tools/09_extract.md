# Tool 09 — extract

Paste everything below the line into Claude Code. Prompts 00 and 01 must already be done.

---

Build the **extract** tool.

**Read first:**
- `CLAUDE.md`
- `docs/ARCHITECTURE.md` §3–6
- `foreman/tools/base.py`
- `foreman/core/*`
- `foreman/testing/fakes.py`

**Only create or edit:**
- `foreman/tools/extract.py`
- `tests/tools/test_extract.py`
- `data/samples/extract.json`

## What it does

Extract pulls structured fields out of messy text, for example the vendor, amount, invoice date and email from an email body.

Jev does not generate text, so extraction becomes **choosing**:
1. Code (regex) proposes candidate spans for patterned kinds.
2. For free-text fields, one LLM call per item proposes candidate spans.
3. Jev picks the right candidate, or "not present".
4. Code checks each candidate appears verbatim in the text, then normalizes the value.

Dates are extracted as month, day and year separately (three Choices) and assembled in code, because Jev is unreliable at date arithmetic.

`name = "extract"`, `uses = "jev+llm"`.

## Params

| Param | Type | Default | Meaning |
|---|---|---|---|
| `fields` | `list[FieldSpec]` | required | 1–10 entries. |
| `use_llm_candidates` | `bool` | `True` | |
| `max_candidates` | `int` | `8` | |
| `output_field` | | `"extracted"` | Writes a dict summary. Each field is also written individually. |

Each `FieldSpec` has:
- `name`: a slug.
- `description`: e.g. "the total amount charged".
- `kind`: one of `text`, `email`, `phone`, `money`, `number`, `date`, `url`, `enum`.
- `options`: `dict[str, str | None] | None`, required for `enum`.
- `required`: bool, default False.

## Algorithm

Override `run()`. Process items concurrently with `gather_bounded`.

1. **Regex candidates** for `email`, `phone`, `money` (with an optional currency symbol or code), `number`, `url`, and `date` patterns:
   - ISO `2026-09-30`
   - `09/30/2026` and `30/09/2026`
   - `Sep 30, 2026`
   - `30 September 2026`

   Each candidate = `{span, start, context}`, where the context is up to 40 characters on each side. Dedupe by span and keep the first `max_candidates`.

2. **LLM candidates.** Fields of kind `text` always use them, and regex kinds use them only when regex found nothing.
   - If `use_llm_candidates` is set, make ONE `ctx.llm.json` call per item covering all those fields.
   - Ask for `{"<field>": ["exact substring", ...]}`, copied verbatim from the text, at most 5 per field.
   - **Keep only the spans where `span in text` is exactly true.**
   - Add a ledger `Decision` with `kind="proposal"` and `decided_by="llm"`.
   - If the LLM call fails, carry on with only the regex candidates.

3. **Questions.** Build ONE `ctx.decider.decide` call per item containing every field question:
   - **enum:** a Choice over the options plus `not_stated: "The text does not state this"`.
   - **date:** three Choices, `<name>__month`, `<name>__day` and `<name>__year`.
     - Options: `jan`…`dec`, then `1`…`31`, then the years found in the text by regex (4-digit, 1900–2100). Each set also gets `not_stated`.
     - If no years were found, skip the year question and treat the year as None.
     - Instructions example: f"In the text, what is the month of {description}?"
   - **Other kinds with candidates:** a Choice with options `c0..cN`, each described as f'"{span}" — …{context}…'. Add `not_present: f"The text does not contain {description}"`. Instructions: f"Which option is {description}?"
   - **No candidates at all:** no question; the value is None with `by="code"`.

4. **Normalize** in code:
   - email → lowercase;
   - phone → digits with a leading `+` if one was present;
   - money → float via the same parsing rules as Calculate's `to_number`, plus `fields[f"{name}_currency"]` (USD, EUR or GBP from the symbol or code, else None);
   - number → float;
   - date → assemble with `datetime.date`. All parts present gives an ISO date; month and year only gives "YYYY-MM"; an invalid combination such as Feb 30 gives None with `low_confidence=True`.

5. **Write** each field with `set_output(item, name, value, confidence, by)`. A date's confidence is the minimum of its parts.
   - Write `fields[f"{name}__missing"] = True` when the field is required and the value is None.
   - `fields[out]` = a dict of all the extracted values.

6. **Aggregates:** `run.aggregates[f"{out}_fill_rate"]` = the percent of items with a non-null value, per field.

## Sample data

`data/samples/extract.json`: about 12 messy receipt or email texts containing vendors, amounts, dates in mixed formats and emails. One should have no date, and one should mention two amounts (subtotal and total).

## Tests (offline)

- Regex candidates for each kind.
- LLM candidates are filtered to verbatim spans: a `FakeLLM` returns one real span and one invented one.
- `not_present`.
- Date assembly, including a missing year, Feb 30 and month-plus-year only.
- Enum.
- The money currency.
- Exactly one Jev call per item.
- An LLM failure falls back to regex-only.

## Done when

- `pytest -q` passes.
- The dev CLI runs on the sample with `--fake` and fields `vendor` (text), `total` (money), `date` (date) and `email` (email). Pass `--fake` with `FakeLLM`'s default; you may give the CLI a `--fake` default JSON response for "candidates".
- You've committed with `feat(tools): extract`.
