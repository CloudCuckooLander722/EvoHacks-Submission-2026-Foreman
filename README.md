# Foreman

**Tedious bulk work, done at a fraction of the cost and time of an LLM agent, with a confidence score on every decision.**

Built for EvoHacks 2026 (theme: AI for Automation).

## The problem

Asking an LLM to work through 10,000 rows, tickets or reviews one at a time is slow and expensive, and you can't tell which answers to trust. So the work either doesn't get done or someone babysits it.

## The idea

Most steps in a tedious job aren't writing. They're small judgments: *Which category is this? Is it a duplicate? Is it urgent? Does it match?*

Foreman gives those judgments to **Jev**, a new kind of model built only for fast, typed decisions:

- **Cheap and fast:** $0.042 per million input tokens with free output, 70–500 ms per call, and one call answers many questions at once.
- **Confidence on every answer:** a calibrated confidence comes back with each decision.

Foreman then wraps Jev in a system that knows when to distrust it:

1. **Plan once.** A light LLM reads your request and turns it into a plan made of Foreman's tools.
2. **Run everywhere.** Each tool asks Jev typed questions about every item, in parallel.
3. **Escalate only when unsure.** Below the confidence bar, a decision gets a second opinion from a light LLM. Above it, Jev's answer stands.
4. **Show your work.** Every decision is logged with who made it (Jev, LLM or code), its confidence and its cost. The live meter shows the split as the job runs.
5. **Hand back something usable.** Results come back as a written summary, charts and tables, exportable to Excel, PDF or Word.

Math, counting and dates are always done in code, never by a model.

## The toolkit

| Tool | Does |
|---|---|
| filter | Keep only items that match a condition |
| classify | Put items into categories, including deep category trees |
| group | Discover themes and assign every item to one |
| dedupe | Find records that are the same thing |
| score | Rate items on several weighted dimensions |
| rank | Sort items by a fuzzy quality like urgency |
| verify | Check claims against a source |
| calculate | Totals, counts, group-bys, date math (pure code) |

Next: extract, find, guard.

## Example

> "Turn these 10,000 app reviews into a backlog: drop spam, split bugs from feature requests, group them into themes, merge duplicates, and rank by urgency."

Foreman plans `filter → classify → group → dedupe → score → rank → calculate` and runs it. The output is a ranked backlog with theme counts and the decision split by Jev, LLM and code. The fraction needing a second opinion depends on where the confidence bar is set.

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and [`docs/CONTRACTS.md`](docs/CONTRACTS.md) for the data handed between components.

## Run it

You need Python 3.12 and Node 20+.

**1. Setup**

```bash
git clone https://github.com/CloudCuckooLander722/EvoHacks-Submission-2026-Foreman.git foreman && cd foreman
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
(cd web && npm install)
pytest -q                   # offline tests, no keys needed
```

**2. Keys in `.env`** (only for live runs)

```bash
cp .env.example .env
# AI_GATEWAY_API_KEY=...   Jev, through Vercel AI Gateway
# FIREWORKS_API_KEY=...    the fallback LLM (DeepSeek V4 Flash on Fireworks)
python -m foreman.dev ping  # checks both keys
```

**3. Start the demo**

```bash
scripts/demo.sh             # API on :8000 + site on :5173; open http://localhost:5173
```

**4. Try it.** In "Try it", drop in [`data/hero/demo_300.csv`](data/hero/demo_300.csv) (300 real Medium app reviews; the page also links it), keep the job *"Turn these app reviews into a ranked bug backlog"* and the saved plan `reviews_backlog`, and press **Run**. You see the plan, each step running then done, the live Jev / LLM / code meter with cost and time, then the ranked bug backlog ("reported by N"), type and theme counts, the confidence-bar slider and an Excel export. Click a backlog row for the "why" panel. A 300-row run takes about a minute.

**No keys? Replay mode.** Press **Replay a recorded run**: it plays a real recorded 1,000-review run (`web/public/replays/reviews-1k/`) through the same UI, with no API and no keys. If the API isn't running, the page says so and replay still works. To build a static, replay-only site: `cd web && VITE_USE_MOCKS=true npm run build` (output in `web/dist/`).

### Hero data

10,000 Medium app reviews (CC0) are already in [`data/hero/`](data/hero/README.md), with a 1,000-review subset. To rebuild them:

```bash
pip install huggingface_hub
python scripts/get_hero_data.py
```

Run the hero job from the CLI (live, needs keys):

```bash
python -m foreman.dev run --items data/hero/reviews_1k.json --plan data/plans/reviews_backlog.json \
  --job "Turn these app reviews into a ranked bug backlog"
python -m foreman.dev run --items data/hero/reviews_1k.json --job "<any plain-English job>"  # planner
```

Real numbers from these runs are in [`docs/RESULTS.md`](docs/RESULTS.md).

### foreman-api

```bash
uvicorn foreman.api.app:app --reload --port 8000
```

The endpoints and SSE events are listed in [`docs/MINH_PLAN.md` §D](docs/MINH_PLAN.md). A quick run from the terminal:

```bash
curl -F file=@data/hero/reviews_1k.json -F plan=reviews_backlog -F threshold=0.7 localhost:8000/runs
curl -N localhost:8000/runs/<run_id>/events     # plan, node, decision, stats, done
curl localhost:8000/runs/<run_id>/results
```

`plan` is optional (a saved plan in `data/plans/`); without it, send `job` and the planner writes the plan. Settings in `.env`: `ALLOWED_ORIGINS` (comma-separated, added to `http://localhost:5173`), `MAX_ACTIVE_RUNS` (default 2), `MAX_ITEMS` per run (default 2000), `MAX_UPLOAD_MB` (default 50).

Each API run also records `runs/<run_id>/events.jsonl`. Turn a finished run into a replay bundle for the static site (format in [`docs/CONTRACTS.md`](docs/CONTRACTS.md)):

```bash
python -m foreman.dev replay-export <run_id> --out web/public/replays/<name>/
```

## Team

- **Minh:** extraction, formatting, frontend.
- **Nathan:** engine, tools, planner, API.
