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
| extract | Pull fields like vendor, amount or date out of messy text |
| find | Locate the passage that answers a question in a long document |
| guard | Check a proposed action is safe before it happens |

## Example

> "Turn these 10,000 app reviews into a backlog: drop spam, split bugs from feature requests, group them into themes, merge duplicates, and rank by urgency."

Foreman plans `filter → classify → group → dedupe → score → rank → calculate` and runs it. The output is a ranked backlog with theme counts and the decision split by Jev, LLM and code. The fraction needing a second opinion depends on where the confidence bar is set.

## Architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full design and [`docs/CONTRACTS.md`](docs/CONTRACTS.md) for the data handed between components.

## Run it

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # then add your keys
pytest -q                   # offline tests
python -m foreman.dev ping  # checks the API keys
```

## Team

- **Nathan:** planner, tools, node state, executor, frontend.
- **Minh:** file extraction, formatting and exports.
