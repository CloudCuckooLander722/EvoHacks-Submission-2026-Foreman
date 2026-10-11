# Foreman

**Tagline:** Tedious bulk work, done fast and cheap, with a confidence score on every decision.

## Inspiration

Every team has a pile of tedious bulk work: thousands of app reviews, tickets or spreadsheet rows to sort, dedupe and rank. An LLM agent can do it, but working through 10,000 items one at a time is slow, costs real money, and gives you no signal about which answers to trust. So the work either doesn't get done or someone babysits it. We noticed that most of the steps aren't writing at all. They're small judgments: which category is this, is it a duplicate, is it urgent?

## What it does

You describe a job in plain English ("Turn these app reviews into a ranked bug backlog") and drop in a file (CSV, Excel, JSON, text, PDF or Word). Foreman:

1. **Plans once.** A light LLM turns the request into a plan built from Foreman's tools, e.g. filter → classify → group → dedupe → score → rank → calculate.
2. **Runs everywhere.** Each tool asks Jev, a fast decision model, typed questions about every item, in parallel.
3. **Escalates only when unsure.** Every Jev answer comes with a calibrated confidence. Below the confidence bar (a slider in the UI), the decision gets one second opinion from a light LLM. Above it, Jev's answer stands.
4. **Shows its work.** A live meter shows the plan steps, who made each decision (Jev, LLM or code), cost and time as the job runs. Every decision is logged; click any result for a "why" panel with the question, the answer, who made it and how confident it was.
5. **Hands back something usable.** A written summary, a ranked backlog with merged duplicates ("reported by N"), type and theme counts, a curve of how many decisions escalate at each confidence setting, and an Excel export.

Math, counting and dates are always done in code, never by a model.

## How we built it

- **Jev via Vercel AI Gateway** for per-item decisions (Choice, Score and yes/no questions with confidence), through our own thin async client.
- **DeepSeek V4 Flash on Fireworks** as the planner and the single fallback, through the OpenAI-compatible SDK.
- **A Python async engine** (asyncio, httpx, pydantic v2): a planner that validates plans against the tool registry, an executor that runs plan nodes, and one `Decider.decide()` that every model decision goes through. That one function holds the confidence gate, the fallback and the decision ledger, so they behave the same in every tool.
- **8 tools:** filter, classify, group, dedupe, score, rank, verify, calculate. Each works offline against fakes; 358 offline tests.
- **FastAPI** with server-sent events for live progress, plus file extraction (csv/xlsx/json/txt/pdf/docx) and exports.
- **A React + TypeScript frontend** (Vite) with the live demo, results, "why" panel and a replay mode that plays a recorded run with no backend.

## How AI automates the work

The LLM does what LLMs are good at, once: understanding the request and writing a plan. Jev then makes tens of thousands of small, typed decisions quickly and cheaply, and its confidence tells the system which ones to double-check. The LLM only sees the hard cases. Code does all arithmetic and counting. The result is end-to-end automation of a job a person would otherwise grind through by hand, with an audit trail for every decision.

## Challenges we ran into

- **The LLM fallback over-inferred.** On Score questions, the fallback gave the top severity to a praise review and top frequency to a feature request. The parsing was right; the model was reading beyond the text. We rewrote the fallback prompts to judge only what the text states, number levels explicitly and define what true and false mean.
- **Hidden reasoning caused timeouts.** DeepSeek V4 reasons by default: one theme-proposal call spent 4,259 of 4,445 completion tokens reasoning and took about 40 s, past our 30 s timeout. Turning reasoning off brought it to about 5 s. We also split timeouts and token limits between planning calls and decision calls.
- **Dedupe chaining.** Transitive merges (A≈B, B≈C, so A=C) collapsed whole themes into one "duplicate" group of up to 439 reviews. We anchored clusters on a canonical item, compare only each bug's 5 closest neighbours, and stopped transitive merges. The largest group at 10k is now 14 crash-on-open reports, and the 10k run became twice as fast and half the price.

## Accomplishments that we're proud of

All numbers are from recorded runs (docs/RESULTS.md):

- **10,000 real app reviews** turned into a ranked bug backlog in **13–22 minutes** (varies with LLM provider load) for **about $1.20**, with **76% of decisions made by Jev**.
- **1,000 reviews** in **about 1.5–2 minutes** for **about 10 cents**, **79% by Jev**.
- Fixing dedupe and the plan took the 10k run from 27.6 min / $2.25 to 12.9 min / $1.16, and cut the share of unthemed bugs and feature requests at 10k from 41% to 21%.
- Every decision is logged with who made it, its confidence and its cost.

## What we learned

A calibrated confidence is what makes cheap models usable for real work: it tells you exactly where to spend the expensive model. And questions for a literal decision model have to say exactly what they mean.

## What's next

- New tools: **extract** (pull values out of text, with Jev picking among candidates), **find** (search a collection for matches) and **guard** (policy checks before acting).
- More file types and connectors in and out.
- Smarter planning from the escalation curve: pick the confidence bar per step.

## Built with

Python, asyncio, httpx, pydantic, FastAPI, server-sent events, Jev, Vercel AI Gateway, DeepSeek V4 Flash, Fireworks AI, OpenAI SDK, rapidfuzz, openpyxl, React, TypeScript, Vite, Recharts.

## Links

- Repo: https://github.com/CloudCuckooLander722/EvoHacks-Submission-2026-Foreman

## Try it

```bash
git clone https://github.com/CloudCuckooLander722/EvoHacks-Submission-2026-Foreman.git foreman && cd foreman
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
(cd web && npm install)
cp .env.example .env        # add AI_GATEWAY_API_KEY and FIREWORKS_API_KEY for live runs
scripts/demo.sh             # open http://localhost:5173
```

Drop in `data/hero/demo_300.csv` and press **Run**. No keys? Press **Replay a recorded run** to play a real recorded 1,000-review run through the same UI.
