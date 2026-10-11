# Minh's plan: status review and work plan

Written Saturday Oct 10, 22:30 PDT. The submission deadline is **Sunday Oct 11, 11:59 AM PDT**. The essentials below are sized to be finished by about **8:00 AM Sunday**, which leaves time to record the demo video and submit.

You own three things:

1. **Extraction:** files → items.json, in `foreman/extract_files/`.
2. **Formatting:** results.json → a written summary, tables, charts and exports, in `foreman/format/`.
3. **The frontend:** the whole demo site, in `web/`. This is new.

Nathan keeps the planner, the tools, the executor and the API.

---

## 1. Where the project stands

### Done, in progress, not started

| Area | Owner | Status | Evidence |
|---|---|---|---|
| Skeleton, contracts docs | Nathan | Done | `8c5a362`, `43a4306`, `docs/CONTRACTS.md` |
| Core engine: questions, Jev client, LLM client, Decider (confidence gate), ledger, node state, fakes, dev CLI | Nathan | Done, offline tests green | `5521470`, `foreman/core/`, `foreman/testing/fakes.py`, `foreman/dev.py` |
| LLM fallback fixes: clear prompts, hidden reasoning off, bounded timeouts | Nathan | Done | `ffe15fe`, `2515816`, `0acbce8` (the 8 s `decide()` timeout landed inside this merge) |
| Tool: filter | Nathan | Done, tested live | `84f87bc`. Live on 25 reviews: 25 Jev decisions, 0 LLM, 2 spam dropped, 1.1 s |
| Tool: classify | Nathan | Done, tested live | `f1acee6`. Live on 25: 24 Jev / 1 LLM, 6.3 s |
| Tool: score | Nathan | Done, tested live | `ceb5cf1`. Live on 25: 24 Jev / 26 LLM before the prompt fix; praise now scores 0 as it should |
| Tool: group | Nathan | Done, tested live | `380f1db`. Live on 26: 24 Jev / 2 LLM, 29 s (most of it was the LLM theme proposal before reasoning was turned off) |
| Tool: rank | Nathan | Done, tested live | `e8f687e`. Live on 25: 28 Jev / 7 LLM / 1 code, 19 s |
| Tool: dedupe | Nathan | Done, tested live | `a1abd16`, `14c653d`. Several live runs on the samples, the largest with 210 decisions |
| Tool: verify | Nathan | Done, offline tests only | `5577a96`, `fb87e28` |
| Tool: calculate | Nathan | Done (pure code, no model) | `2719bca` |
| Tools: extract, find, guard | Nathan | Not started (extras, only if time) | `prompts/tools/09_extract.md` … `11_guard.md` |
| Planner and executor | Nathan | Not started; **next up** | `foreman/graph/` holds only `__init__.py` |
| API with SSE | Nathan | Not started; after the executor | `foreman/api/` holds only `__init__.py` |
| Hero dataset (~10k public app reviews) | Nathan | Not started | — |
| Extraction | Minh | Prototype only; needs a port | `my-adk-agent/services/data_processor.py` |
| Formatting and exports | Minh | Not started | `foreman/format/` holds only `__init__.py` |
| Frontend | Minh | Not started | no `web/` yet |
| Demo video | Minh | Not started | — |

**A caveat on the live numbers:** every live wall time above was measured *before* `2515816`. At that point the fallback LLM was spending 20–40 s on hidden reasoning per call; with reasoning off it answers in about 1 s. The times will drop, so don't put these numbers on the site. Nathan will send fresh numbers from the hero run (see §4).

### What's left overall

- **Nathan:** planner → executor → API → hero dataset run, plus help with integration.
- **Minh:** extraction → formatting → frontend → demo video.
- **Together:** wire the site to the real API, run the hero demo end to end, record, submit.

---

## 2. Review of your existing code

`my-adk-agent/services/data_processor.py` (`file_reader`) was written for the earlier idea, before we settled on Foreman. A lot of it carries over.

### What's good, and should be kept

- **The input edge cases.** No path, missing file, not a regular file, no permission, not decodable, empty or whitespace-only. These are exactly the failures a user hits when uploading files, and each one gets its own message. Keep every one of these checks in the port.
- **Pydantic output validation.** `OutputSchema` with `extra="forbid"`, bounded floats and a `Literal` file type is the right instinct: bad data fails loudly at the edge instead of three steps later. In the port, the schema to validate against is `ItemsFile` from `foreman/contracts.py`.
- **The Jev validity check.** "Is this extracted text readable, or is it garbage?" is a perfect Jev question: one gut-check judgment with a calibrated probability. The three-band policy (accept / warn / reject) is good too. Keep it as an optional check after extraction.

### What has to change to fit the plan (`docs/CONTRACTS.md`)

**1. The README.** The text in your old repo describes the AI daily brief over email and calendar. The project is now Foreman, and the root `README.md` is already the Foreman one. Please don't bring the old text back. If you want to change the README, edit the Foreman version.

**2. Reach Jev through our client, not the TypeSafe SDK.** `file_reader` creates a `TypeSafeClient` with `TYPESAFE_API_KEY`. We reach Jev through Vercel AI Gateway (`AI_GATEWAY_API_KEY`) via `foreman.core.jev.JevClient`, and **every model decision goes through `Decider.decide()`**. That one call is what applies the confidence bar, does the LLM fallback when Jev is unsure, and logs the decision for the meter and the "why" panel. You never build a client yourself; you get a `RunContext` and call its decider:

```python
from foreman.core.context import RunContext, make_context
from foreman.core.questions import Noul

READABLE = Noul(
    "Is the text in the state readable words or table data?",
    true="Mostly readable words, sentences or table values",
    false="Mostly garbled characters, binary noise or broken OCR",
)

async def readability(text: str, ctx: RunContext) -> float:
    answers = await ctx.decider.decide(
        item_id="extract", tool="extract_files", state=text[:8000], questions={"readable": READABLE}
    )
    return float(answers["readable"].value)  # P(readable): gate, fallback and ledger already handled
```

In tests and offline: `ctx = await make_context(fake=True)` (no keys needed), then `await ctx.aclose()` at the end. The API will pass its own `ctx` in.

**3. Read real formats, not only UTF-8 text.** `open(..., encoding="utf-8")` rejects every xlsx, pdf and docx file. The extractor has to handle **csv, xlsx, pdf, docx, txt and json**.

**4. Detect the file type in code, not with Jev.** Asking Jev "what file type is this?" spends a model call on something code knows for certain, and it can be wrong. Use the extension plus the leading bytes:

| Leading bytes | Type |
|---|---|
| `%PDF` | pdf |
| `PK\x03\x04` (a zip) containing `word/document.xml` | docx |
| `PK\x03\x04` (a zip) containing `xl/workbook.xml` | xlsx |
| decodes as text, starts with `[` or `{` | json |
| decodes as text, `csv.Sniffer` finds a delimiter | csv |
| decodes as text | txt |

Jev still does the readability check (point 2).

**5. Return items, not one blob.** `file_reader` returns the whole file as a single `content` string. The contract is **items.json**: one item per row, ticket or document section, each with `id`, `text`, `fields` and `meta`. Long documents are split into sections of **at most about 20,000 characters** (Jev's state limit is about 32k tokens). The model is `ItemsFile` in `foreman/contracts.py`; validate your output with `ItemsFile.model_validate(...)`.

**6. Use exceptions and logging, not `print` and `None`.** Library code in this repo raises errors and logs with `logging` (CLAUDE.md, rule 6). Turn each edge-case message into `raise ExtractError("File not found: ...")`. The API turns that into a clean error event, and the CLI prints it.

**7. Location.** The code belongs in `foreman/extract_files/`, not `my-adk-agent/`. Leave `my-adk-agent/` where it is for now; once the port is done, you decide whether to delete it.

---

## 3. Your task list, in priority order

The **essentials** add up to about 9 hours, so starting around 23:00 they finish around 8:00 AM. Everything marked *stretch* comes after that, or is cut (see §4 for the cut order).

| # | Task | Hours | Running total |
|---|---|---|---|
| 0 | Setup | 0.25 | 0.25 |
| 1 | A1. Extraction: csv, xlsx, json, txt, plus the CLI | 1.5 | 1.75 |
| 2 | C1. Site scaffold, types, mocks, replayed live demo | 2.0 | 3.75 |
| 3 | C2. Results: table, "why" panel, charts, confidence slider | 2.0 | 5.75 |
| 4 | B1. Summary and xlsx export | 1.25 | 7.0 |
| 5 | C3. Hero, "how it works", tool strip, numbers | 1.25 | 8.25 |
| 6 | C4. Switch to the real API and rehearse end to end | 0.75 | 9.0 |
| 7 | *Stretch:* A2. pdf and docx extraction | 1.0 | |
| 8 | *Stretch:* B2. pdf export | 1.0 | |
| 9 | *Stretch:* B3. docx export | 0.5 | |
| 10 | E. Record the demo video (8:00–10:00 AM) | 1.0 | |

### 0. Setup (0.25 h)

- [ ] Follow §5 "Getting started" and get `python -m pytest -q` green.
- [ ] Run one dev CLI command with `--fake` and open `out/<tool>_results.json` to see the real data shapes.

### A. Extraction: `foreman/extract_files/`

#### A1. csv, xlsx, json, txt, plus the CLI (1.5 h)

**Goal:** `extract(path) -> ItemsFile`, pure code with no keys needed, ready for the API to call.

**Files to create:**
- `foreman/extract_files/__init__.py`: exports `extract`, `ExtractError`.
- `foreman/extract_files/detect.py`: `detect_kind(path) -> Literal["csv","xlsx","pdf","docx","txt","json"]` from the extension plus leading bytes (table in §2).
- `foreman/extract_files/tables.py`: csv and xlsx.
- `foreman/extract_files/text.py`: txt and json.
- `foreman/extract_files/check.py`: the optional readability check from §2, `async def check_readability(items: ItemsFile, ctx, sample=20) -> list[str]`, which returns warnings.
- `foreman/extract_files/__main__.py`: the CLI.
- `tests/extract_files/__init__.py`, `tests/extract_files/test_extract.py`.
- Add `pandas` and `openpyxl` to `dependencies` in `pyproject.toml`, in a small commit of its own. Tell Nathan, since the file is shared.

**Rules per format:**
- **csv / xlsx:** one item per row. The id is `row-<n>`, where `n` is the spreadsheet row number, so the first data row under the header is `row-2` (this matches `data/samples/reviews_small.json`). For xlsx, read the first sheet unless told otherwise, and put the sheet name in `source.sheet`.
  - `text` is the main free-text column: the string column with the longest average length, if that average is at least ~20 characters. If there isn't one, join the row as `col: value; col: value`.
  - `fields` holds **every** column, typed: ints and floats as numbers, dates as ISO `YYYY-MM-DD` strings, empty cells left out. It must survive `json.dumps`, so no `NaN` and no `Timestamp`.
  - `meta`: `{"row": n}`, plus `"sheet"` for xlsx.
  - `source`: `{"filename", "kind", "sheet", "rows", "text_column"}`.
- **json:** a list of objects becomes one item each. Use the object's `id` if every id is unique, else `item-<n>`. `text` is the first of `text`/`body`/`review`/`content`/`message` present, else the longest string value. `fields` is the object. If the file is already an items.json (`{"items": [...]}`), validate it and pass it through.
- **txt:** if it looks like a list (at least 5 non-empty lines and most lines under 300 characters), make one item per line (`line-<n>`). Otherwise split on blank lines into paragraphs and pack them into sections of at most 20,000 characters (`<stem>-s<n>`).
- **Encodings:** try `utf-8-sig`, then `cp1252`. Keep all your old edge cases, each raising `ExtractError`. Also raise if no items come out at all.

**CLI:**
```bash
python -m foreman.extract_files data/samples/reviews.xlsx -o items.json
python -m foreman.extract_files notes.txt -o items.json --check   # --check runs the readability check (needs keys)
```
The CLI prints the item count, the chosen text column and any warnings.

**Inputs → outputs:** a file path → an `ItemsFile`, or the items.json written by the CLI.

**How to test:** build tiny fixtures *in code* in the test file (write a 3-row csv to `tmp_path`, an xlsx with `openpyxl`, a json list, a list-like txt and a prose txt) instead of committing binary files. Assert the ids, `text`, typed `fields` and `meta`, and that `ItemsFile.model_validate(...)` passes. Also test that each edge case raises `ExtractError`.

**Done when:**
- [ ] `pytest -q tests/extract_files` passes.
- [ ] `python -m foreman.extract_files <your xlsx> -o /tmp/items.json` works, and `python -m foreman.dev tool classify --fake --items /tmp/items.json --params '{"options": {"bug": "Something is broken", "praise": "Positive feedback"}}'` runs on the result.

#### A2. *Stretch:* pdf and docx (1 h)

- **pdf:** `pymupdf` (`import fitz`). Pull the text page by page and pack consecutive pages into sections of at most 20,000 characters, splitting a single huge page at paragraph breaks. The id is `<stem>-p<first page>-s<n>`; `meta` is `{"page_start", "page_end"}`.
- **docx:** `python-docx`. Start a new section at each `Heading` paragraph, pack to at most 20,000 characters, and put table rows in as `col: value; ...` lines. The id is `<stem>-s<n>`; `meta` is `{"section", "heading"}`.
- **Files:** `foreman/extract_files/documents.py`. Add `pymupdf` and `python-docx` to `pyproject.toml`.
- **How to test:** generate a 3-page pdf with `fitz` and a docx with two headings, both in `tmp_path`.

**Done when:**
- [ ] A 3-page pdf and a two-heading docx each give the expected sections.
- [ ] No item's text is over 20,000 characters.

### B. Formatting: `foreman/format/`

#### B1. Summary and xlsx export (1.25 h)

**Goal:** a plain-English summary and an Excel file built from a `ResultsFile`.

**Files to create:**
- `foreman/format/__init__.py`: exports `summarize`, `export`.
- `foreman/format/summary.py`: `async def summarize(results: ResultsFile, ctx: RunContext) -> str`.
- `foreman/format/xlsx.py`
- `foreman/format/colors.py`: one palette shared by every export and mirrored in the site.
- `foreman/format/charts.py`: finds chartable aggregates.
- `tests/format/__init__.py`, `tests/format/test_format.py`.

**How `summarize` works:**
- **One** call to `ctx.llm.json(system, user)`. The input is **only** `job`, the plan's tool list, `aggregates`, `stats`, `node_reports` and the dropped count. **Never send per-item data.**
- The prompt says: write 3–5 sentences for a manager; use only numbers that appear in the input; do no arithmetic. It asks for `{"summary": "..."}`.
- **The model never computes numbers.** Every number in the summary must come from `aggregates` or `stats`. Check this in code: pull the numbers out of the summary with a regex. If any of them isn't in the input, use a fallback summary built from a template in code instead.
- Record the call as ARCHITECTURE §4 describes: wrap it in `with track_usage() as u:` (from `foreman.core.llm`), then call `ctx.ledger.record_call("llm", u.cost_usd, n=u.calls)` and add a `Decision(kind="proposal", decided_by="llm", tool="format", question_id="summary", ...)`.
- If the LLM call fails, fall back to the template summary. A summary must always come back.

**`export(results, fmt, summary=None) -> bytes`, xlsx part:**
- **Results sheet:** one row per item: `id`, `text`, then every output field. Hide the `__confidence` / `__by` / `__low_confidence` columns and use them to **colour the cells:** Jev blue `#E5F1FF`, LLM amber `#FFF1D6`, code grey `#F2F2F7`, with red text where `__low_confidence` is true. Add a legend row at the top.
- **One sheet per aggregate:** a `dict[str, number]` becomes two columns; a list of dicts becomes a table.
- **A Stats sheet:** `stats`, plus `node_reports` as a table.
- **A Dropped sheet**, if anything was dropped.
- Use `openpyxl`, which is already added in A1.

**Charts (`charts.py`):** detect chartable aggregates by their **shape, not their name**. The real names follow the output field (`type_counts` from calculate, `category_counts` from classify, `theme_groups` from group), not exactly the names in the CONTRACTS example.
- A `dict[str, int]` is a bar chart (type counts, theme counts).
- A list of `{theme, count}` is a bar chart.
- `stats.by` is the Jev / LLM / code split.

xlsx gets native charts, pdf gets `matplotlib` PNGs, and the site uses the same rules in TypeScript.

**How to test:** build a `ResultsFile` from the mock chain in §5 (`5.json`), plus a `FakeLLM(json_responses={"summary": {"summary": "..."}})` context. Check the following:
- the summary that comes back contains no number missing from the input;
- a summary with an invented number falls back to the template;
- `openpyxl.load_workbook(BytesIO(data))` has the expected sheets, and the fill colours on one Jev cell and one LLM cell are right.

**Done when:**
- [ ] The tests pass.
- [ ] Exporting the mock run produces an `.xlsx` that opens in Excel or Numbers with coloured cells, a legend, aggregate sheets and stats.

#### B2. *Stretch:* pdf export (1 h)

`foreman/format/pdf.py` with `fpdf2` and `matplotlib`. Order: the summary, then the Jev/LLM/code split chart, the aggregate charts, then the top 50 rows as a table, with cells coloured the same way. Add both libraries to `pyproject.toml`.

**Done when:**
- [ ] The pdf opens with all four parts.

#### B3. *Stretch:* docx export (0.5 h)

`foreman/format/docx.py` with `python-docx`: the summary, the charts as images, and one table per aggregate. This is cut first if time runs out.

### C. Frontend: `web/`, a full demo site (Vite + React + TypeScript)

One long page, in this order: **Hero → How Foreman works → Live demo**.

**Libraries:**
- `motion` (Framer Motion) for scroll, fade and number counters; `useReducedMotion()` respects the user's reduced-motion setting.
- `recharts` for charts.
- `@tanstack/react-virtual` for the results table, since 10,000 rows must scroll smoothly.

**Design:** Apple-style.
- Lots of whitespace and large, confident headlines.
- Font stack: `-apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", system-ui, sans-serif`.
- A restrained palette with **one accent** colour (e.g. `#0A84FF`), with Jev / LLM / code reusing the export colours.
- Subtle scroll and fade motion; soft shadows only on things you can click or that float.
- Smooth number counters on the meter.
- It has to look right on a laptop projector at **1280–1440 px** wide. Mobile is nice to have.
- Respect `prefers-reduced-motion`: no parallax, counters jump straight to the value.

#### C1. Scaffold, types, mocks, replayed live demo (2 h)

**Files:**
```
web/
  .env.local                 VITE_USE_MOCKS=true, VITE_API_BASE=/api   (not committed)
  vite.config.ts             proxy /api -> http://localhost:8000
  src/config.ts              reads VITE_USE_MOCKS / VITE_API_BASE: the one switch between mocks and the real API
  src/api/types.ts           TS types mirroring foreman/contracts.py, ledger.py and the §D events
  src/api/client.ts          real API: fetch + EventSource
  src/api/mock.ts            same interface, replays src/mocks/ as a fake event stream
  src/mocks/results.json     a real results.json (see §5 for how to make one)
  src/mocks/decisions.json   the matching decisions.jsonl, as a JSON array
  src/mocks/curve.json       the escalation curve (snippet in §5)
  src/sections/LiveDemo/JobComposer.tsx   job textarea, drag-and-drop file, confidence slider
  src/sections/LiveDemo/PlanSteps.tsx     the plan as steps; each lights up while running, then shows its counts
  src/sections/LiveDemo/Meter.tsx         decisions by Jev / LLM / code, cost, elapsed time; animated counters
```

**The mock stream** has the same interface as the real client: `startRun(file, job, threshold) -> run_id` and `subscribe(run_id, onEvent)`. It emits these events in order:
1. one `plan` event, built from `results.plan`;
2. for each node, `node` running → `decision` events (sampled from `decisions.json`, in `ts` order) → `stats` every 250 ms → `node` done (with that node's `node_reports` entry);
3. `done`.

Compress it so the whole run replays in about 20 seconds.

**Done when:**
- [ ] `npm run dev` shows the composer.
- [ ] Submitting replays a whole run: the steps light up in order, and the meter counts up smoothly and ends on exactly the `stats` from results.json.

#### C2. Results view (2 h)

**Files:** `src/sections/Results/Summary.tsx`, `Charts.tsx`, `ResultsTable.tsx`, `WhyPanel.tsx`, `Downloads.tsx`, `ConfidenceCurve.tsx`.

- **Summary:** the written summary, from `aggregates.summary` (see §D).
- **Charts:** the same shape-based detection as `charts.py`. At minimum: type counts, theme counts, and the Jev/LLM/code split.
- **Results table:** virtualized, with cells tinted by `__by`. Clicking a cell opens the **"why" panel** with that decision's question, answer, confidence, who decided, and, if the LLM overrode Jev, Jev's original answer and confidence. Data comes from `GET /runs/{id}/decisions?item_id=` (in mock mode, filter `decisions.json`).
- **Download buttons** for xlsx, pdf and docx (`GET /runs/{id}/export?format=`). In mock mode, link to sample files in `public/`.
- **Confidence slider (the key demo moment).** After a run, dragging the slider reads `aggregates.escalation_curve` and shows, live and without re-running: "At 0.80, **1,240** of 43,500 Jev decisions (2.9%) would go to the LLM." Draw the curve as an area chart with a marker that follows the slider, and animate the number. Also show the extra cost: `llm_cost_usd / by.llm` × the escalated count, computed in code and labelled "≈". Make dragging buttery: no layout shift, and the number morphs.

**Done when:**
- [ ] On the mock run, every chart renders.
- [ ] Clicking any output cell opens its "why" panel.
- [ ] Dragging the slider updates the curve marker and the count at 60 fps on a laptop.

#### C3. Hero, "how it works", tool strip, numbers (1.25 h)

**Hero:**
- The demo video at the top: `<video autoplay muted loop playsinline controls>`. Use a placeholder until E is recorded.
- A one-line headline: "Tedious bulk work, done in seconds, with a confidence score on every decision."
- A short subline, and a **Try it** button that scrolls to the live demo.
- The layout is modelled on the old Oafer landing page. Put `// TODO(nathan): Oafer landing link` at the top of `Hero.tsx` until Nathan sends the link.

**How Foreman works:** four scroll-driven sections, each with a visual:
1. **Plan once:** a light LLM turns your request into a plan of tools.
2. **Decide everywhere:** Jev answers typed questions about every item, in parallel, at about $0.04 per million tokens.
3. **Escalate only when unsure:** below the confidence bar, a light LLM gives a second opinion.
4. **Show the work:** every decision is logged with who made it.

**Tool strip:** the 11 tools as cards with one-line descriptions. The text is in the README's toolkit table, or in `python -m foreman.dev catalog`.

**Numbers:** real numbers from our live runs (items, wall time, cost, % decided by Jev). **Do not invent them.** Show `—` placeholders in `src/content/numbers.ts` until Nathan sends them.

**Done when:**
- [ ] The page reads top to bottom at 1280 and 1440 px.
- [ ] With reduced motion turned on, nothing animates.

#### C4. Switch to the real API (0.75 h, once Nathan's API is up)

- [ ] Set `VITE_USE_MOCKS=false`, run `uvicorn foreman.api.app:app --port 8000` (Nathan will confirm the module path), and do one full run with a real file.
- [ ] Fix any shape mismatches **by reporting them to Nathan**. Don't patch around them in the UI; both sides build to §D.

**Done when:**
- [ ] A real upload runs end to end on the site, including downloads.

### D. The API contract the frontend uses

Nathan builds this. It's written down here so you can build against it now. All paths are under `VITE_API_BASE` (default `/api`, proxied by Vite to `http://localhost:8000`).

| Endpoint | What it does |
|---|---|
| `POST /runs` (multipart: `file`, `job`, `threshold`) | Returns `{"run_id": "r_8f2c1a9b"}`. The server extracts the file with your `extract()`, plans, then executes. `threshold` is optional, 0–1, default 0.7. |
| `GET /runs/{id}/events` (SSE) | `plan`, `node`, `decision`, `stats`, `done`, `error` (shapes below). |
| `GET /runs/{id}/results` | results.json (`ResultsFile` in `foreman/contracts.py`). |
| `GET /runs/{id}/decisions?item_id=row-2` | That item's ledger rows: a JSON array of `Decision` (`foreman/core/ledger.py`). |
| `GET /runs/{id}/export?format=xlsx\|pdf\|docx` | The file bytes, made by your `export()`, with `Content-Disposition: attachment`. |

**Two additions to results.json that Nathan will make**, both inside `aggregates` (a free-form dict), so the contract models don't change:
- `aggregates.summary`: the string from your `summarize()`.
- `aggregates.escalation_curve`: `[[threshold, count], ...]`, 21 steps from 0.0 to 1.0, straight from `Ledger.escalation_curve()`. `count` is how many Jev decisions had confidence below that threshold.

**SSE wire format:** each event is `event: <name>\ndata: <one line of JSON>\n\n`. The shapes below are derived from `contracts.py` and `ledger.py`; field names match exactly.

`plan`, sent once, after planning:
```json
{
  "run_id": "r_8f2c1a9b",
  "job": "Turn these reviews into a backlog...",
  "items_total": 10000,
  "nodes": [
    {"id": "n1", "tool": "filter", "params": {"condition": "Is this a genuine product review (not spam)?", "output_field": "is_review"}, "why": "Drop spam before judging anything else."},
    {"id": "n2", "tool": "classify", "params": {"options": {"bug": "...", "feature_request": "...", "praise": "..."}, "output_field": "type"}, "why": "Split bugs from feature requests."}
  ]
}
```

`node`, sent when a node starts, while it runs (progress), and when it finishes. `report` is a `NodeReport` and appears only on `done`:
```json
{"node": "n2", "tool": "classify", "index": 1, "total": 7, "status": "running", "done": 4200, "items": 9420}
```
```json
{"node": "n2", "tool": "classify", "index": 1, "total": 7, "status": "done",
 "report": {"node": "n2", "tool": "classify", "items_in": 9420, "items_out": 9420, "jev_decisions": 9100, "llm_decisions": 320, "code_ops": 0, "low_confidence": 3, "duration_ms": 8100, "notes": []}}
```

`decision`, a compact ledger row. These are **sampled** (at most ~20 per second) for the live ticker; the totals always come from `stats`.
```json
{"item_id": "row-2", "node": "n2", "tool": "classify", "question_id": "type", "qtype": "choice", "value": "bug", "confidence": 0.93, "decided_by": "jev", "jev_value": null, "jev_confidence": null, "low_confidence": false, "cost_usd": 0.0000021, "ts": 1791692184.96}
```

`stats`, the meter, about every 250 ms. This is exactly `RunStats`:
```json
{"decisions_total": 21840, "by": {"jev": 20210, "llm": 1580, "code": 50}, "low_confidence": 4, "jev_cost_usd": 0.18, "llm_cost_usd": 0.31, "wall_time_s": 14.2, "jev_calls": 9800, "llm_calls": 1580}
```

`done`:
```json
{"run_id": "r_8f2c1a9b", "results_url": "/runs/r_8f2c1a9b/results"}
```

`error` (`fatal: false` means the run continues, e.g. one node had skipped items):
```json
{"message": "Could not read the file: not a valid xlsx", "node": null, "fatal": true}
```

### E. Demo video (1 h, 8:00–10:00 AM)

Record once the site works end to end. **Keep the cost and elapsed time on screen the whole way through.**

| Time | Shot |
|---|---|
| 0:00–0:15 | The hero. One sentence: "10,000 app reviews, one plain-English request." |
| 0:15–0:30 | Drag in the 10,000-review file and type the job: *"Turn these into a backlog: drop spam, split bugs from feature requests, group into themes, merge duplicates, rank by urgency."* Leave the slider at 0.7 and submit. |
| 0:30–1:05 | The plan appears as steps, with each step's "why". The steps light up in turn while the meter counts Jev / LLM / code decisions, cost and time. |
| 1:05–1:30 | Results: the summary, the theme and type charts, the ranked table. Click one cell to show the "why" panel, ideally one where the LLM overrode Jev. |
| 1:30–1:45 | Drag the confidence slider: the escalation count changes live with no re-run. Say: "you choose the trade-off between cost and certainty." |
| 1:45–2:00 | Download the xlsx and open it (coloured by who decided). End on the final cost and time. |

---

## 4. Hand-offs and risks

### What you need from Nathan, and when

| What | By (proposed; agree tonight) |
|---|---|
| Hero dataset: a public app-review set, ~10k rows, as csv/xlsx | Sun 00:30 |
| Real live-run numbers for the site, plus one live `results.json` + `decisions.jsonl` for the mocks | Sun 01:00 |
| The Oafer landing page link | Sun 01:00 |
| API running locally with the §D contract | Sun 04:00 |
| A full hero run through the API, for rehearsal | Sun 06:00 |

### What Nathan needs from you

| What | By |
|---|---|
| `extract()` importable from `foreman.extract_files` (csv/xlsx at least) | Sun 01:00 |
| `summarize()` and `export(results, "xlsx")` importable from `foreman.format` | Sun 06:00 |

### Risks and fallbacks

| Risk | Fallback |
|---|---|
| The API is late | The site replays a recorded run (the C1 mock stream with the real hero `results.json`). The demo still works; only the upload is simulated. |
| Odd files don't extract | Demo with the CSV. pdf/docx extraction is already marked stretch. |
| Running out of time | Cut in this order: docx export → the "how it works" animations (keep static sections) → mobile. |

---

## 5. Getting started

```bash
git pull
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # Nathan shares the keys privately. Never commit .env or paste keys anywhere in git.
python -m pytest -q           # offline, no keys needed
python -m foreman.dev catalog # the 11 tools the planner can use
```

**See the real data shapes offline.** Chain the dev CLI: each run's `out/<tool>_results.json` is a valid input for the next. This builds a realistic multi-tool results file without keys:

```bash
mkdir -p /tmp/mock && cp data/samples/reviews_small.json /tmp/mock/ && cd /tmp/mock
T() { python -m foreman.dev tool "$1" --fake --items "$2" --params "$3" && cp "out/$1_results.json" "$4"; }
T classify reviews_small.json '{"options":{"bug":"Something is broken, crashes or fails","feature_request":"Asks for a new feature or option","praise":"Positive feedback, great, love"},"output_field":"type"}' 1.json
T group 1.json '{"themes":[{"name":"Camera","description":"Camera crashes or problems"},{"name":"Login","description":"Login, password or account problems"},{"name":"Requests","description":"Requests for new features"}],"output_field":"theme"}' 2.json
T score 2.json '{"dimensions":[{"name":"severity","instructions":"How severe is the problem described in the text?","levels":["No problem","Minor annoyance","Feature broken","Crash or data loss"],"weight":2},{"name":"reach","instructions":"How many users does the problem likely affect?","levels":["One user","Some users","Most users"]}],"output_field":"urgency"}' 3.json
T rank 3.json '{"criterion":"urgency","score_field":"urgency","top_k":10}' 4.json
T calculate 4.json '{"operations":[{"op":"group_count","by":"type","as":"type_counts"},{"op":"group_count","by":"theme","as":"theme_counts"}]}' 5.json
```

What to know about these files:
- `5.json` has all 25 items, each with `type`, `theme`, `urgency`, `rank` and their `__confidence` / `__by` fields. Each results file only carries the aggregates of its *own* tool, so merge `aggregates` across `1.json`…`5.json` for the mock.
- Each run's decision log is at the `decisions_file` path inside its results file.
- `--fake` answers come from a keyword heuristic, so the labels are rough; the shapes are exact. Nathan will send a live hero run to replace them.
- `group --fake` needs fixed `themes` as above, because the fake LLM can't propose themes.

**Escalation curve for the mocks**, computed with the real ledger code:
```bash
python - <<'EOF'
import json
from foreman.core.ledger import Decision, Ledger
led = Ledger("mock")
led.extend(Decision.model_validate_json(line) for line in open("runs/<run_id>/decisions.jsonl"))
print(json.dumps(led.escalation_curve()))
EOF
```

**Frontend:**
```bash
npm create vite@latest web -- --template react-ts
cd web && npm install && npm install motion recharts @tanstack/react-virtual
echo 'VITE_USE_MOCKS=true' > .env.local
npm run dev
```
`node_modules/` and `web/dist/` are already in `.gitignore`.

### Working in parallel without git clashes

- **Separate folders.** You work in `foreman/extract_files/`, `foreman/format/`, `web/` and their tests. Nathan works in `foreman/core/`, `foreman/tools/`, `foreman/graph/` and `foreman/api/`. Shared files (`pyproject.toml`, `docs/CONTRACTS.md`, `README.md`) get small, separate commits, with a heads-up to the other person.
- **Pull before you commit:** `git pull --rebase`, then commit, then push straight away.
- **Small commits,** each one passing `pytest -q`. Use prefixes like `feat(extract): csv and xlsx`, `feat(format): xlsx export`, `feat(web): live demo replay`.
- **Stage files by name:** `git add foreman/extract_files/tables.py tests/extract_files/test_extract.py`. **Never** `git add -A` or `git add .`; several Claude Code sessions run in this repo at once, and `-A` sweeps up their half-finished work. If git reports `index.lock`, wait a few seconds and retry.
- **Contracts:** if items.json or results.json needs to change, agree it with Nathan first, then change `docs/CONTRACTS.md` and `foreman/contracts.py` together.
