# CLAUDE.md — Foreman

Read this before doing anything in this repo. Then read `docs/ARCHITECTURE.md`. If your task touches the data handed between team members, also read `docs/CONTRACTS.md`.

## What we are building

Foreman is an AI task runner for tedious bulk work: categorize, filter, dedupe, rank, extract, check. A light LLM plans the job once. Our tools then run it on every item. About 90% of the per-item decisions are made by **Jev**, a fast, cheap, calibrated decision model. When Jev's confidence falls below the threshold, the decision goes to **one** fallback: a light LLM call (DeepSeek V4 Flash on Fireworks). Every decision is logged with who made it and how confident it was.

This is a hackathon build: EvoHacks, theme "AI for Automation". Submission deadline is **Sunday Oct 11, 11:59 AM PDT**. Prefer simple and working over clever.

## Team ownership

| Area | Owner | Where |
|---|---|---|
| File extraction (csv/xlsx/pdf/docx/txt/json → items) | Minh | `foreman/extract_files/` |
| Planner, tools, node state, executor, API, integration | Nathan | `foreman/core/`, `foreman/tools/`, `foreman/graph/`, `foreman/api/` |
| Formatting results into reports, charts and exports | Minh | `foreman/format/` |
| Frontend (the demo site) | Minh | `web/` |

Minh's current plan and the API contract the frontend builds against are in `docs/MINH_PLAN.md`.

Do not edit another owner's folder unless the task says so. The two hand-off formats (`items.json` in, `results.json` out) are defined in `docs/CONTRACTS.md` and implemented in `foreman/contracts.py`. Changing them needs both people to agree.

## Stack

- Python 3.12, async throughout (`asyncio`, `httpx`).
- `pydantic` v2 for every schema; `pydantic-settings` reads `.env`.
- Jev is called over HTTP through Vercel AI Gateway. Use our own thin client in `foreman/core/jev.py`, not the TypeSafe SDK.
- The LLM is called through the `openai` SDK pointed at Fireworks' OpenAI-compatible endpoint, in `foreman/core/llm.py`.
- FastAPI for the API (later). `pytest` + `pytest-asyncio` for tests. `rich` for the dev CLI.

## Commands

```bash
source .venv/bin/activate
pytest -q                                   # all offline tests (fakes, no keys needed)
pytest -q -m live                           # live tests, need keys in .env
python -m foreman.dev ping                  # checks Jev + LLM keys and endpoints
python -m foreman.dev tool <name> --items data/samples/<file>.json --params '<json>' [--fake] [--threshold 0.7]
```

## Rules for all code

1. **Every model decision goes through `Decider.decide()`** in `foreman/core/decide.py`. Tools never call Jev or the LLM for decisions directly. That is how the confidence gate, the fallback and the ledger stay consistent.
2. **Math, counting, dates and comparisons are done in code, never by a model.** Jev is bad at all four.
3. **Jev never generates text.** If a value must be pulled out of text, code or the LLM proposes candidates and Jev picks one with a Choice.
4. **Every tool must work offline** with `FakeJev` / `FakeLLM` from `foreman/testing/fakes.py`. The keys are not in `.env` yet, so offline tests are the main way to verify work.
5. **Tools only touch their own file** (`foreman/tools/<name>.py`), their tests (`tests/tools/test_<name>.py`) and their sample data (`data/samples/<name>.json`). The registry discovers tools automatically, so no shared file needs editing. If a tool genuinely needs a change in `foreman/core/`, make the smallest change, keep existing tests green, and say so in your summary.
6. Keep functions small and typed. No global state except settings. No `print` in library code; use `logging`.
7. Never commit `.env` or put keys in code, tests or logs.

## Writing Jev questions (important — Jev reads literally)

Jev is a "System One" model. It answers fast gut-check questions about a `state` and returns probabilities plus a confidence. It does badly when questions are vague. Every question a tool builds must follow these rules:

- **One judgment per question.** Split compound questions and combine the answers in code.
- **Say exactly what you mean.** Jev answers the words, not the intent. Put boundary cases into the option descriptions (`criteria`).
- **Keep instructions and criteria consistent.** For a Noul, `true` must mean yes.
- **Refer to parts of the state by name** ("the text in `record_a`"), and avoid double negatives and multi-hop reasoning.
- **Send only what the question needs.** Unrelated text in the state lowers accuracy. The hard limit is about 32k tokens for the state plus the longest question.
- **Choice:** at most 255 options, and Jev leans slightly toward the first option. Always include an explicit escape option such as `none_of_these` or `not_present` where one makes sense.
- **Score:** 2 to 10 levels, ordered low → high, each with a clear description. Use the expected level for thresholds and sorting, not as an exact number.
- **Noul:** returns P(yes) between 0 and 1. Our confidence for a Noul is `abs(2p - 1)`.

## Confidence and fallback (the "confidence bar")

- One run-wide threshold, `ctx.threshold`, between 0 and 1 (default from `CONFIDENCE_THRESHOLD`, 0.7). The UI slider sets it.
- If a Jev answer's confidence is at or above the threshold, it is accepted (`decided_by="jev"`).
- Otherwise it gets **one** light-LLM call with the same question (`decided_by="llm"`). If that call fails or returns junk, keep Jev's answer and mark it `low_confidence=True`.
- No other escalation levels. No quotes before a run (cut for time).

## Done means

- `pytest -q` passes offline.
- The tool runs from the dev CLI with `--fake` on its sample file.
- Public classes and functions have docstrings that explain *why*, not just *what*.
