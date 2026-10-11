# Tool 07 — verify

Paste everything below the line into Claude Code. Prompts 00 and 01 must already be done.

---

Build the **verify** tool.

**Read first:**
- `CLAUDE.md`
- `docs/ARCHITECTURE.md` §3–6
- `foreman/tools/base.py`
- `foreman/core/*`
- `foreman/testing/fakes.py`

**Only create or edit:**
- `foreman/tools/verify.py`
- `tests/tools/test_verify.py`

## What it does

Verify checks whether each claim in some text is backed by a source. Typical uses:
- check an LLM-drafted reply against the customer's message or a policy;
- check an extracted value against the original document;
- catch made-up details.

Code splits the claim text into sentences. Jev judges every sentence in **one** call per item.

`name = "verify"`, `uses = "jev"`.

The planner-facing `description` should say: use Verify after any step that generated or extracted text, and keep the source short (filter it with Find first if it is long).

## Params

| Param | Type | Default | Meaning |
|---|---|---|---|
| `claim_field` | `str` | required | The field holding the text to check. `"text"` means `item.text`. |
| `source_field` | `str \| None` | `"text"` | The field holding the per-item evidence. |
| `source_text` | `str \| None` | `None` | Fixed reference applied to every item, e.g. a refund policy. |
| `split` | `bool` | `True` | |
| `min_words` | `int` | `4` | |
| `max_claims` | `int` | `30` | |
| `output_field` | | `"verified"` | |

Validation:
- At least one of `source_field` and `source_text` must be set.
- `claim_field` must differ from `source_field`.

If both sources are set, the source sent to Jev is `{"item_source": ..., "reference": ...}`.

## Algorithm

Subclass `PerItemTool`, overriding `state()`, `questions()` and `apply()`.

1. **Claims.**
   - If `split` is set, split the claim text into sentences in code. Split on `.`, `!` or `?` followed by whitespace or a newline, and on bullet lines; protect common abbreviations (e.g., i.e., Mr., Dr., U.S.).
   - Drop sentences shorter than `min_words` words and keep the first `max_claims`.
   - Ids: `c1`, `c2`, and so on.
   - If there are no claims, write the verdict `"no_claims"` with `by="code"` and make no call.

2. **State:** `{"source": <source>, "claims": {"c1": "...", ...}}`.

3. **Questions:** one Choice per claim, id = the claim id.
   - Instructions: f"Using only `source`, is claim `{cid}` in `claims` supported?"
   - Options:
     - `supported`: "The source states this or directly implies it."
     - `contradicted`: "The source says something that conflicts with this claim."
     - `not_mentioned`: "The source does not address this claim."

4. **Apply.** Per-claim results: `[{id, text, verdict, confidence, by}]` → `fields[f"{out}_claims"]`. The overall verdict is:
   - `contradicted` if any claim is contradicted;
   - otherwise `supported` if all are supported;
   - otherwise `partially_supported`.

   Write it with `set_output`. Confidence = the minimum claim confidence; `by` = `"llm"` if any claim came from the LLM.

   Also write `fields[f"{out}_problem_claims"]`: the texts of every claim that is not supported.

5. **`StateTooLarge` on an item:** the verdict is `"skipped_source_too_large"` with `by="code"`, plus a report note suggesting Find upstream. Never crash the run.

6. **Aggregates:** `run.aggregates[f"{out}_counts"]` = counts per overall verdict.

## Tests (offline)

- The sentence splitter, including abbreviations and bullets.
- One Jev call per item, with one question per claim.
- The overall verdict logic across all three cases.
- `source_text` combined with `source_field` produces the right state shape.
- `no_claims`.
- The too-large path: monkeypatch `JEV_MAX_STATE_TOKENS` low, or use a `FakeJev` that raises `StateTooLarge`.

## Done when

- `pytest -q` passes.
- The dev CLI runs with `--fake` on a small sample you create inline in the test. Commit an extra `data/samples/verify.json` where each item has `fields.reply` (a drafted reply) and `text` (the customer message). Some replies should invent details.
- You've committed with `feat(tools): verify`.
