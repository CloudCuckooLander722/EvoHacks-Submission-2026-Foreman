# Video script (2:30)

## Before recording

```bash
cd foreman
source .venv/bin/activate
python -m foreman.dev ping        # both keys OK
scripts/demo.sh                   # API :8000 + site :5173
# open http://localhost:5173 in a clean browser window, zoom 110–125%
# have data/hero/demo_300.csv ready in Finder to drag in
# in the editor, open these tabs in order:
#   foreman/graph/planner.py, foreman/tools/ (folder), foreman/core/decide.py (line ~106)
```

A live 300-row run takes about a minute. Do one warm-up run first (the first run loads the SDKs), then record. If the live run stalls (LLM provider load), press **Replay a recorded run** instead: same UI, a real recorded 1,000-review run, no API needed.

## Shot list

| Time | Shot | Narration |
|---|---|---|
| 0:00–0:20 | Hero section, slow scroll. Then a spreadsheet of raw reviews. | "Every team has a pile of tedious bulk work: thousands of reviews, tickets, rows to sort, dedupe and rank. Asking an LLM to do it one item at a time is slow and expensive, and you get no signal about which answers to trust." |
| 0:20–0:40 | "How it works" section. | "Foreman plans once and decides fast. A light LLM turns your request into a plan made of our tools. Then Jev, a fast, cheap decision model, answers every small judgment with a calibrated confidence. Only when Jev is unsure does the decision escalate to an LLM, once." |
| 0:40–1:00 | "Try it": drag in `demo_300.csv`, show the job text "Turn these app reviews into a ranked bug backlog", plan `reviews_backlog`, confidence bar at 0.70, press **Run**. | "Here are 300 real app reviews. I ask for a ranked bug backlog, set the confidence bar, and run." |
| 1:00–1:20 | Plan steps light up one by one (filter → classify → group → dedupe → score → rank → calculate); the meter counts Jev / LLM / code, cost and time. | "Each step runs then finishes. The meter shows who made every decision: most are Jev, a few go to the LLM, and math is done in code. Cost and time tick up live, in cents and seconds." |
| 1:20–1:40 | Results: summary, ranked bug backlog with "reported by N", type and theme counts ("Other" = no theme fit). Click a row: the "why" panel. Drag the confidence slider on the curve. Hover "Download Excel". | "Out comes a ranked bug backlog, with duplicates merged and counted. Click any row to see why: every question, who answered it, and how confident. Drag the confidence bar to see how many decisions would escalate at each setting. And it exports to Excel." |
| 1:40–2:10 | Editor: `foreman/graph/planner.py` → `foreman/tools/` (8 files) → `foreman/core/decide.py`, highlight the `confidence < self.threshold` line. | "The planner writes a plan from our toolkit. Eight tools: filter, classify, group, dedupe, score, rank, verify, calculate. Every model decision goes through one function, `Decider.decide`. This line is the confidence gate: below the threshold, one LLM call; above it, Jev's answer stands. Every decision is logged." |
| 2:10–2:30 | Hero stat tiles (10k run), then the logo / repo URL. | "On 10,000 real reviews: 13 to 22 minutes depending on LLM provider load, about $1.20, with 76% of decisions made by Jev. Tedious bulk work, done fast and cheap, with a confidence score on every decision. That's Foreman." |

All numbers above are from `docs/RESULTS.md` (runs D2/D3 and C3). Don't add others.
