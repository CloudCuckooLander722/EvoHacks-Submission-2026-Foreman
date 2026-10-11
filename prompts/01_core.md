# Prompt 01 — Build the core engine

Run this after 00. Every tool depends on it, so run it alone before any tool prompt. Paste everything below the line into Claude Code.

---

Build Foreman's core engine: config, contracts, Jev client, LLM client, the confidence gate (Decider), the ledger, node state, the tool base classes and registry, offline fakes, and the dev CLI.

**Read first:** `CLAUDE.md`, then `docs/ARCHITECTURE.md` (§3–5 are the exact contracts to implement), then `docs/CONTRACTS.md`. Match the signatures in ARCHITECTURE exactly. Eleven tool prompts will be written against them. If you must deviate, update ARCHITECTURE.md in the same change and tell me why.

Work in this order, running `pytest -q` after each step.

## 1. `foreman/config.py`

- A `Settings(BaseSettings)` class reading `.env`, with every variable in `.env.example`.
- Keys are optional `SecretStr` so offline tests run without them.
- Provide `get_settings()`, cached with `lru_cache`.

## 2. `foreman/core/state.py`

- `ItemState`, `RunState` and `Condition` exactly as in ARCHITECTURE §3.
- `Condition.matches(item) -> bool`. A field missing from the item never matches, except for `falsy`, which a missing field does match.
- Helper `get_input(item, field) -> str`. `"text"` returns `item.text`; any other field returns `str(item.fields.get(field, ""))`.
- Helper `set_output(item, field, value, *, confidence, by, low_confidence=False)`. It writes `field`, `field__confidence` and `field__by`, plus `field__low_confidence` only when True.

## 3. `foreman/core/questions.py`

- `Noul`, `Choice`, `Score`, `Question`, `Answer` exactly as in ARCHITECTURE §4.
- `to_wire(q)` builds the TypeSafe request shape. A Choice's options become `criteria` (a map from option to description or null). A Score's levels become `criteria` (a list). A Noul becomes `criteria: {"true": ..., "false": ...}`, included only when given.
- `validate_question(q)` raises `ValueError` when:
  - a Choice has 0 options, more than 255 options, or empty option keys;
  - a Score has fewer than 2 or more than 10 levels;
  - the instructions are empty.
- `answer_from_wire(qtype, raw) -> Answer`.
  - **noul:** `value = p`, `probabilities = {"true": p, "false": 1 - p}`, `confidence = abs(2p - 1)`.
  - **choice:** `value` = the chosen key, plus Jev's probabilities and confidence.
  - **score:** `value = float(score)`; the probabilities keys stay as strings `"0"`, `"1"`, …; plus Jev's confidence.
  - In all cases `decided_by = "jev"`.

## 4. `foreman/core/concurrency.py`

- `async def gather_bounded(factories, limit, on_done=None)`. It runs coroutine factories with a semaphore, preserves result order, calls `on_done(done, total)` after each one, and raises the first error only after the others finish (`return_exceptions` internally, then re-raise).
- `class RateLimiter(rps)`, an async token bucket with `async with limiter:` semantics.

## 5. `foreman/core/jev.py`

Implement `JevClient.ask(state, questions) -> JevResult` per ARCHITECTURE §4.

- **HTTP:** use `httpx.AsyncClient` against `f"{JEV_BASE_URL}/v1/systemone"` with `Authorization: Bearer {AI_GATEWAY_API_KEY}` and `model = JEV_MODEL`.
- **Before sending:** validate every question. Estimate state tokens as `len(json.dumps(state)) / 4`; if that is above `JEV_MAX_STATE_TOKENS`, raise `StateTooLarge`.
- **Concurrency and pacing:** a semaphore of `JEV_MAX_CONCURRENCY` plus a `RateLimiter(JEV_MAX_RPS)`.
- **Retries:**
  - Retry 429, 529, 5xx and timeouts up to 5 attempts, with exponential backoff (0.5s base), full jitter, and honouring `retry-after`.
  - On 4xx other than 429, raise `JevError` with the body's `message` and `error_type`.
- **Result:**
  - `JevResult(answers, input_tokens, cost_usd, latency_ms)`.
  - Cost comes from `provider_metadata.gateway.cost` (a string; convert with float) when present. Otherwise use `input_tokens * JEV_PRICE_PER_M_INPUT / 1e6`.
- **Lifecycle:** `aclose()`, and usable as an async context manager.
- **Tests (`tests/core/test_jev.py`, using `respx`):**
  - Correct request body for all three question types.
  - Response parsing.
  - A retry on 429 that then succeeds.
  - `StateTooLarge`.
  - Validation errors.
  - Add one `@pytest.mark.live` test that asks a real Noul.

## 6. `foreman/core/llm.py`

Implement `LLMClient` per ARCHITECTURE §4.

- Use `openai.AsyncOpenAI(base_url=LLM_BASE_URL, api_key=FIREWORKS_API_KEY)`, model `LLM_MODEL`, `temperature=0`.
- **`json(system, user) -> dict`:**
  - Request `response_format={"type": "json_object"}`. If the provider rejects that parameter, retry once without it and remember the outcome for this client instance.
  - Parse robustly: strip code fences, then take the first balanced `{...}` block. If the model returns reasoning text, ignore it.
  - Retry once on parse failure, then raise `LLMError`.
- **`decide(state, qid, question) -> Answer | None`:** a compact prompt containing the state (JSON-dumped if not a string), the instructions, and the options or levels with their descriptions. It must demand only the JSON below:
  - **choice:** `{"choice": "<one of the keys>"}`, validated against the keys. The Answer has `probabilities = {key: 1.0}` and `confidence = 1.0`.
  - **score:** `{"level": <int 0..n-1>}`. The Answer has `value = float(level)`.
  - **noul:** `{"answer": true|false}`. The Answer has `value` 1.0 or 0.0.
  - In every case `decided_by = "llm"`. Return `None` on any failure; never raise from `decide`.
- **Tracking:** prompt and completion tokens and cost (using the price env vars). Concurrency is limited by `LLM_MAX_CONCURRENCY`.
- **Tests:**
  - Mock the OpenAI client.
  - Parsing with fences or extra text.
  - Validation of choice keys.
  - `None` on garbage.
  - One live test.

## 7. `foreman/core/ledger.py`

Implement `Decision`, `RunStats`, `Ledger` per ARCHITECTURE §4.

- **`Ledger(run_id)`** methods:
  - `add`
  - `extend`
  - `on_event(callback)`, which supports several callbacks; a callback exception is logged, never raised.
  - `stats()`, which uses the wall time since creation.
  - `escalation_curve(steps=21)`: for thresholds 0.0, 0.05 … 1.0, count Jev decisions (`decided_by` jev, or llm with `jev_confidence` set) whose Jev confidence is below the threshold.
  - `dump_jsonl(path)`
- **`RunStats` fields:**
  - `decisions_total`
  - `by` (a dict over jev, llm and code)
  - `low_confidence`
  - `jev_cost_usd`
  - `llm_cost_usd`
  - `wall_time_s`
  - `jev_calls`
  - `llm_calls`

## 8. `foreman/core/decide.py` — the confidence gate

Implement `Decider(jev, llm, ledger, run_id, threshold)` and `decide(*, item_id, tool, state, questions)` exactly as ARCHITECTURE §4.

1. Make one Jev call with all the questions.
2. Collect the answers where `confidence < threshold` and `question.fallback` is true. Run `llm.decide` on them concurrently.
3. Merge the results:
   - **LLM answer valid:** `decided_by="llm"`, `jev_value` set to Jev's original value. Record `jev_confidence` in the ledger.
   - **LLM returned None:** keep the Jev answer with `low_confidence=True`.
4. Write one `Decision` per question. Split the Jev call's cost evenly across that call's decisions; put the LLM cost on the decision it belongs to.
5. If Jev itself fails after its retries:
   - Try the LLM for every question.
   - Questions the LLM also fails become a `DecisionError`. The tool decides what to do with it; tools usually skip the item and note it.

Tests (with `respx` or fakes) cover:
- Above-threshold answers are accepted.
- Below-threshold answers fall back.
- A fallback failure produces the low-confidence flag.
- `fallback=False` questions are never escalated.
- Ledger counts.
- The Jev-down path.

## 9. `foreman/core/context.py`

- `RunContext` per ARCHITECTURE §4.
- **`async def make_context(run_id=None, threshold=None, fake=False, jev=None, llm=None) -> RunContext`.** It builds real clients from settings, or `FakeJev` and `FakeLLM` when `fake=True`. The `jev` and `llm` arguments let tests inject their own.
- **`RunContext.aclose()`.**

## 10. `foreman/testing/fakes.py` — offline doubles every tool test will use

- **`FakeJev(rule=None, latency_ms=1)`**
  - It has the same `ask()` signature and return type as `JevClient`, and records `calls` as a list of `(state, questions)`.
  - `rule(state, qid, question) -> FakeAnswer | None` lets a test force the value, probabilities and confidence for any question. Returning `None` uses the default.
  - The deterministic default works on the lowercased state text (JSON-dumped if it is not a string):
    - **choice:** score each option by how many words (longer than 3 characters) of `key + description` appear in the state. Pick the highest, breaking ties by the first option. Give it probability 0.8, split 0.2 evenly over the rest, and confidence 0.9 if its score is above 0, else 0.3.
    - **score:** pick the level with the most word overlap, defaulting to the middle level, with confidence 0.8. Put 0.7 probability on it and spread the rest evenly.
    - **noul:** p = 0.9 if any word (longer than 3 characters) of `question.true` (or of the instructions, if `true` is None) appears in the state, else 0.1.
  - It reports `input_tokens = len(json)/4` and the matching cost.
- **`FakeLLM(rule=None, json_responses=None, fail=False)`**
  - It has the same `decide()` and `json()` signatures as `LLMClient`.
  - `decide` uses `rule`, falling back to the same keyword heuristics, and returns `None` when `fail=True`.
  - `json(system, user)` returns the first value in `json_responses` (a dict mapping a substring to a dict) whose key appears in `system` or `user`, and raises `LLMError` if none matches.
  - It records `calls`.
- Add tests for the fakes themselves, because every tool test depends on them behaving as described.

## 11. `foreman/tools/base.py` and `foreman/tools/__init__.py`

- **Base classes:** `ToolParams`, `ToolReport`, `Tool` and `PerItemTool` exactly as ARCHITECTURE §5.
- **`PerItemTool.run`:**
  1. Select the items matching `params.where`.
  2. For each one, call `ctx.decider.decide(item_id=item.id, tool=self.name, state=self.state(item, params), questions=self.questions(item, params))` through `gather_bounded` (limit `JEV_MAX_CONCURRENCY`, reporting progress through `ctx.progress`).
  3. Call `self.apply(...)` on each result.
  4. Count `jev_decisions`, `llm_decisions` and `low_confidence` from the returned answers, time the run, and return a `ToolReport`.
  5. If an item raises `DecisionError` or `StateTooLarge`, skip it, set `fields[f"{params.output_field}__error"]`, and add a note.
- **Registry:**
  - It uses `pkgutil` to import every module in `foreman.tools` except `base`, and registers the concrete `Tool` subclasses that have a `name`.
  - It exposes `get_tool(name) -> Tool` (an instance; unknown names raise `KeyError` listing the valid ones), `all_tools()` and `catalog()`, which returns `[{name, description, uses, params_schema: Params.model_json_schema()}]`.
  - Registration must not depend on editing `__init__.py`, so tool prompts can run in parallel without merge conflicts.
- **Test:** add a dummy `Tool` subclass inside the test file (not in the package). It checks that `PerItemTool.run` respects `where`, counts correctly and handles errors.

## 12. `foreman/contracts.py`

- `ItemsFile` (`source` and `items: list[ItemState]`) and `ResultsFile`, exactly as in `docs/CONTRACTS.md`.
- **`load_items(path) -> RunState`.**
- **`build_results(run, ledger, *, plan, threshold, started_at, finished_at, node_reports, runs_dir) -> ResultsFile`.** It writes `runs/<run_id>/decisions.jsonl` and sets `decisions_file`. Dropped items keep their `reason` from `meta.get("drop_reason")`.
- **`save_results(results, path)`.**

## 13. `foreman/dev.py` — the dev CLI (use `argparse` + `rich`)

- **`python -m foreman.dev ping`:**
  - Sends one real Jev Noul and one real `LLMClient.json` call.
  - Prints the latency, model and cost for each, or a clear message saying which key is missing or which endpoint failed, including the status code and message.
  - Also lists models from `GET {JEV_BASE_URL}/v1/models` if that works.
- **`python -m foreman.dev tool <name> --items <path> --params '<json>' [--threshold 0.7] [--fake] [--limit N]`:**
  1. Loads the items and builds the context.
  2. Validates the params with the tool's `Params`, printing pydantic errors nicely.
  3. Runs the tool.
  4. Prints a table of up to 20 items showing the id, the first 60 characters of text, and every field the tool added (value, confidence, by).
  5. Prints the `ToolReport` and the ledger stats, plus the aggregates as JSON.
  6. Writes `out/<tool>_results.json` with `build_results`.
- **`python -m foreman.dev catalog`:** prints the tool catalog.
- **Sample:** create `data/samples/reviews_small.json`, a valid `items.json` with about 25 realistic app reviews (bugs, feature requests, praise, two spam, three near-duplicate bug reports, mixed ratings in `fields.rating`, dates in `fields.date`). Tool prompts can reuse it.

## 14. Finish

1. Run `ruff check .` and `pytest -q`; both must be green.
2. If `.env` has keys, also run `python -m foreman.dev ping` and `pytest -q -m live`. If not, say so.
3. Update `docs/ARCHITECTURE.md` only if you changed a signature.
4. Commit with `feat(core): engine, decider, ledger, fakes, dev CLI`.
5. Give me a short summary that includes anything the tool prompts need to know.
