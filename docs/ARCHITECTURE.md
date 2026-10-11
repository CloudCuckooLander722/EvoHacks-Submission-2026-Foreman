# Foreman architecture

**A light LLM plans the job once, a set of tools runs it on every item, and Jev makes most of the decisions inside those tools.** Anything Jev is unsure about gets one light-LLM retry, and every decision is logged.

## 1. The pipeline

```
 user query + file
        │
        ▼
 ┌────────────────────┐   Minh
 │ 1. File extraction │   csv / xlsx / pdf / docx / txt  →  items.json
 └────────────────────┘
        │ items.json
        ▼
 ┌────────────────────┐   Nathan
 │ 2. Planner (LLM)   │   query + tool catalog + 20 sample items  →  plan.json
 └────────────────────┘
        │ plan.json
        ▼
 ┌────────────────────┐   Nathan
 │ 3. Executor        │   runs plan nodes in order over the run state
 │    └─ Tools (11)   │   each tool asks Jev typed questions via the Decider
 │       └─ Decider   │   Jev ≥ threshold → accept;  < threshold → 1 LLM call
 │          └─ Ledger │   every decision recorded (who, answer, confidence, cost)
 └────────────────────┘
        │ results.json
        ▼
 ┌────────────────────┐   Minh
 │ 4. Formatting      │   LLM-written summary + tables/charts → xlsx / pdf / docx bytes
 └────────────────────┘
        │
        ▼
     frontend: live meter (Jev / LLM / code), confidence bar, results, download
```

**Why it is cheap and fast:**
- The planner LLM runs once per job, not once per item.
- One Jev call carries many questions, answered in parallel, in 70–500 ms, for $0.042 per million input tokens. Output is free.
- The LLM only sees the minority of decisions Jev is unsure about.

## 2. Repository layout

```
foreman/
  config.py              settings from .env (pydantic-settings)
  contracts.py           items.json / results.json models (shared with Minh)
  core/
    questions.py         Noul / Choice / Score question types + Answer
    jev.py               async Jev client (Vercel AI Gateway), rate limit, retries, cost
    llm.py               async LLM client (Fireworks, OpenAI-compatible), JSON helpers
    decide.py            Decider: confidence gate + single LLM fallback + ledger writes
    ledger.py            Decision records, run stats, event hook for the live meter
    state.py             ItemState, RunState, Condition (the "node state")
    context.py           RunContext passed to every tool
    concurrency.py       bounded gather with progress callback
  tools/
    __init__.py          auto-discovering registry: get_tool(), catalog()
    base.py              Tool, PerItemTool, ToolParams, ToolReport
    filter.py classify.py group.py dedupe.py score.py rank.py
    verify.py calculate.py extract.py find.py guard.py
  graph/                 planner.py, plan.py (schema), executor.py   (after tools)
  api/                   FastAPI app + SSE                           (after graph)
  extract_files/         Minh: file → items.json
  format/                Minh: results.json → report + exports
  testing/fakes.py       FakeJev, FakeLLM (deterministic, offline; also used by `dev --fake`)
  dev.py                 dev CLI: ping, tool runner
tests/
  core/ tools/
data/samples/            small sample item files per tool
prompts/                 the Claude Code prompts used to build this repo
```

## 3. Node state

Every item flows through the plan as an `ItemState`. Tools read its fields and write new ones. This is the "node state" architecture: the plan is a list of nodes, and each node is a tool that reads and writes typed fields on the state.

```python
class ItemState(BaseModel):
    id: str
    text: str  # main text of the item (from extraction)
    fields: dict[str, Any] = {}  # original columns + everything tools add
    meta: dict[str, Any] = {}  # source info (row, page, sheet...)


class RunState(BaseModel):
    run_id: str
    job: str  # the user's plain-English request
    items: list[ItemState]
    dropped: list[
        ItemState
    ] = []  # full items removed by a tool (e.g. Filter); reason in meta["drop_reason"]
    aggregates: dict[str, Any] = {}  # cross-item outputs: groups, rankings, totals...
```

Field naming convention for tool outputs:

- `fields[out]` holds the value.
- `fields[f"{out}__confidence"]` holds 0–1 confidence; for composite outputs, the minimum.
- `fields[f"{out}__by"]` holds `"jev"`, `"llm"` or `"code"`.
- `fields[f"{out}__low_confidence"]` is written only when True (the fallback failed); otherwise the key is absent.
- Outputs computed by code (e.g. `rank` from a `score_field`, `calculate` results) use `by="code"` and confidence 1.0.

**Dropping items.** A tool that removes an item (e.g. Filter) moves the whole `ItemState` from `RunState.items` to `RunState.dropped` and sets `item.meta["drop_reason"]` (e.g. `"filter n1: is_review=false"`). In results.json each dropped item becomes `{id, text, fields, reason}`, with `reason` taken from `meta["drop_reason"]`.

**Routing.** Every tool's params accept `where: Condition | None`, and only items matching the condition are processed. This is how the plan branches without a full graph engine.

```python
class Condition(BaseModel):
    field: str
    op: Literal["eq", "ne", "in", "not_in", "truthy", "falsy", "gte", "lte"]
    value: Any = None
```

## 4. Core APIs (exact contracts every tool relies on)

### Questions and answers — `core/questions.py`

```python
@dataclass(frozen=True)
class Noul:
    instructions: str
    true: str | None = None     # what "yes" means
    false: str | None = None    # what "no" means
    fallback: bool = True       # allow LLM fallback when unsure

@dataclass(frozen=True)
class Choice:
    instructions: str
    options: dict[str, str | None]   # option key -> description (max 255)
    fallback: bool = True

@dataclass(frozen=True)
class Score:
    instructions: str
    levels: list[str]                # 2..10, low -> high
    fallback: bool = True

Question = Noul | Choice | Score

@dataclass
class Answer:
    qtype: Literal["noul", "choice", "score"]
    value: str | float               # choice: option key; score: expected level index (float); noul: P(yes)
    probabilities: dict[str, float]  # choice: option -> p; score: "0".."n" -> p; noul: {"true": p, "false": 1-p}
    confidence: float                # choice/score: Jev's confidence; noul: abs(2p - 1); LLM answers: 1.0
    decided_by: Literal["jev", "llm"]
    low_confidence: bool = False     # True when below threshold and the fallback failed
    jev_value: str | float | None = None   # Jev's original answer when the LLM overrode it

def to_wire(q: Question) -> dict   # -> TypeSafe request shape
```

Wire format for one Jev request: `POST {JEV_BASE_URL}/v1/systemone`. The default base URL is `https://ai-gateway.vercel.sh/typesafe`.

```json
{
  "model": "typesafe-ai/jev",
  "state": "string | object | array",
  "questions": {
    "q1": {"type": "noul",   "instructions": "...", "criteria": {"true": "...", "false": "..."}},
    "q2": {"type": "choice", "instructions": "...", "criteria": {"billing": "Charges and refunds", "other": null}},
    "q3": {"type": "score",  "instructions": "...", "criteria": ["Calm", "Frustrated", "Very angry"]}
  }
}
```

Response: `answers[qid]` holds the following:
- noul: `{noul}`.
- choice: `{choice, probabilities, confidence}`.
- score: `{score, legend, probabilities, confidence}`.

Other response fields:
- `usage.input_tokens`.
- `provider_metadata.gateway.cost`, a string in USD, when routed through Vercel.

Errors: 401, 422, 429 (rate limit), 529 (overloaded).

### Jev client — `core/jev.py`

```python
class JevClient:
    async def ask(self, state: str | dict | list, questions: dict[str, Question]) -> JevResult
    # JevResult: answers: dict[str, Answer] (decided_by="jev"), input_tokens, cost_usd, latency_ms
```

- Validates limits before sending: options ≤ 255, levels 2–10, and estimated state tokens (chars/4) ≤ `JEV_MAX_STATE_TOKENS` (default 28,000). Over the limit it raises `StateTooLarge`.
- Concurrency semaphore (`JEV_MAX_CONCURRENCY`, default 32) plus a token-bucket rate limiter (`JEV_MAX_RPS`, default 60; the published limit is 80).
- Retries 429/529/5xx and timeouts with exponential backoff and jitter, up to 5 attempts.
- Cost comes from `provider_metadata.gateway.cost` if present, else `input_tokens * 0.042 / 1e6`.
- Errors: `ValueError` for malformed questions, `StateTooLarge` (a `ValueError`) before sending an oversized state, `JevError` for refusals and exhausted retries.

### LLM client — `core/llm.py`

```python
class LLMClient:
    async def decide(self, state, qid: str, question: Question) -> Answer | None   # fallback; None if unusable
    async def json(self, system: str, user: str) -> dict                          # proposals, planner
```

- Uses `AsyncOpenAI(base_url=LLM_BASE_URL, api_key=FIREWORKS_API_KEY)`, model `LLM_MODEL`, temperature 0.
- `decide` asks for strict JSON: choice `{"choice": "<key>"}`, score `{"level": <int>}`, noul `{"answer": true|false}`. It validates the answer, retries once on bad output, and otherwise returns `None`.
- Tracks tokens and cost (prices from env) and has its own semaphore (`LLM_MAX_CONCURRENCY`, default 8).

### Decider (the confidence gate) — `core/decide.py`

```python
class Decider:
    async def decide(self, *, item_id: str, tool: str, state, questions: dict[str, Question]) -> dict[str, Answer]
```

1. Sends **one** Jev call with all the questions.
2. For each answer below `ctx.threshold` (with `question.fallback=True`), runs `llm.decide` concurrently.
3. If the LLM returns a valid answer, it replaces Jev's (`decided_by="llm"`, `jev_value` kept). Otherwise Jev's answer stays with `low_confidence=True`.
4. Writes one ledger `Decision` per question and returns the final answers.

If Jev itself fails (`JevError`), the LLM is tried for every question; if any still fails, `decide` raises `DecisionError`. `StateTooLarge` propagates unchanged. `PerItemTool.run` catches both, skips the item and sets `fields[f"{output_field}__error"]`.

### Ledger — `core/ledger.py`

```python
class Decision(BaseModel):
    run_id: str; item_id: str; tool: str; question_id: str; qtype: str
    value: Any; confidence: float | None; probabilities: dict[str, float] | None
    decided_by: Literal["jev", "llm", "code"]; kind: Literal["decision", "proposal", "compute"] = "decision"
    jev_value: Any = None; jev_confidence: float | None = None; low_confidence: bool = False
    latency_ms: int | None = None; cost_usd: float = 0.0; ts: float

class Ledger:
    def add(self, d: Decision) -> None              # also fires on_event callbacks (live meter / SSE)
    def stats(self) -> RunStats                     # counts by decided_by, jev/llm cost, low_confidence count, wall time
    def escalation_curve(self, steps=21) -> list[tuple[float, int]]   # threshold -> how many Jev decisions would fall back
    def record_call(self, provider: Literal["jev", "llm"], cost_usd=0.0, n=1) -> None  # call counts + cost for RunStats
```

`RunStats` call counts and costs come from `record_call`, not from decisions, because one Jev call covers several decisions and a failed fallback costs money without a decision of its own. The Decider records its own calls. A tool that calls `ctx.llm.json()` directly (proposals) should wrap it in `with track_usage() as u:` (from `core/llm.py`), then call `ctx.ledger.record_call("llm", u.cost_usd, n=u.calls)` and add a `Decision(kind="proposal", decided_by="llm", ...)`.

`escalation_curve()` powers the confidence-bar demo. Every Jev confidence is stored, so the UI can show how many items would escalate at any threshold without re-running.

### RunContext — `core/context.py`

```python
@dataclass
class RunContext:
    run_id: str
    threshold: float
    jev: JevClient
    llm: LLMClient
    decider: Decider
    ledger: Ledger
    progress: Callable[[str, int, int], None] | None = None  # (tool, done, total)
```

## 5. Tool interface — `tools/base.py`

```python
class ToolParams(BaseModel):
    input_field: str = "text"  # "text" = item.text, else item.fields[input_field]
    output_field: str  # each tool sets its own default
    where: Condition | None = None


class ToolReport(BaseModel):
    tool: str
    items_in: int
    items_out: int
    jev_decisions: int
    llm_decisions: int
    code_ops: int
    low_confidence: int
    duration_ms: int
    notes: list[str] = []


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]  # 2–4 sentences: what it does + when the planner should use it
    Params: ClassVar[type[ToolParams]]
    uses: ClassVar[Literal["jev", "jev+llm", "code"]]

    async def run(self, run: RunState, params: ToolParams, ctx: RunContext) -> ToolReport: ...


class PerItemTool(Tool):
    """For tools that ask questions about each item independently."""

    def questions(self, item: ItemState, params) -> dict[str, Question]: ...
    def state(self, item: ItemState, params) -> str | dict: ...  # default: the input text
    def apply(self, item: ItemState, answers: dict[str, Answer], params) -> None: ...

    def after_all(
        self, run: RunState, params, report: ToolReport
    ) -> None: ...  # optional hook, e.g. Filter drops items here

    # run(): selects items via `where`, calls ctx.decider.decide per item with bounded concurrency, applies,
    # then after_all(); items_out = len(run.items) afterwards
```

`RunContext.jev` / `.llm` are typed as the `JevLike` / `LLMLike` protocols in `core/decide.py`, so `FakeJev` / `FakeLLM` (and test doubles) fit without subclassing.

The registry (`tools/__init__.py`) imports every module in `foreman/tools/` and registers each `Tool` subclass by `name`. `catalog()` returns `[{name, description, uses, params_schema}]` for the planner.

## 6. The 11 tools

The Write tool from the original design is now part of Minh's formatting step.

| # | Tool | What it does | How | Jev primitive |
|---|---|---|---|---|
| 1 | filter | Keeps items matching a yes/no condition | One Noul per item, cutoff in code | Noul |
| 2 | classify | Puts each item in one category (flat or hierarchical) | Choice; trees walked level by level with beam 2; >255 flat options run as a tournament | Choice |
| 3 | group | Clusters items into themes | LLM proposes themes from a sample once; Jev assigns every item; outliers get one refinement round | Choice (+ LLM proposal) |
| 4 | dedupe | Finds records that are the same entity | Code builds candidate pairs (rapidfuzz); Jev scores each pair; union-find clusters | Score + Noul |
| 5 | score | Rates items on weighted dimensions | One Score per dimension in one call; weights combined in code | Score |
| 6 | rank | Sorts items by a fuzzy criterion | Score each, sort in code; near-ties re-checked pairwise in both orders | Score + Choice |
| 7 | verify | Checks claims against a source | Claims split in code; one Choice per claim: supported / contradicted / not_mentioned | Choice |
| 8 | calculate | Totals, counts, group-bys, date math | Pure code, no model | — |
| 9 | extract | Pulls fields out of messy text | Regex (and LLM for free text) proposes candidates; Jev picks; dates as month/day/year Choices; normalized in code | Choice (+ LLM proposal) |
| 10 | find | Locates the passage answering a query in a long document | Split into numbered units; Choice over unit ids per window + "answer present?" Noul; final pick across windows | Choice + Noul |
| 11 | guard | Gates an action before it happens | Risk Nouls (destructive, irreversible, off-goal, injection); allow / review / block policy in code | Noul |

Build order follows the hero demo: tools 1–8 are needed for it, 9–11 are extras.

## 7. Plan format, planner and executor — `foreman/graph/`

### Plan (`graph/plan.py`)

```python
class PlanNode(BaseModel):
    id: str            # n1, n2, ...
    tool: str          # a registered tool name
    params: dict       # validated against the tool's Params
    why: str = ""      # one line from the planner, shown in the UI

class Plan(BaseModel):
    job: str
    nodes: list[PlanNode]   # 1-12 nodes, run in order
```

The hand-written hero plan is `data/plans/reviews_backlog.json` (filter → classify `type` → group `theme` on bugs and feature requests → dedupe issues blocked by theme → score `urgency` (severity ×2, frequency ×1) → rank by `urgency`, top 20 → calculate type/theme counts, `n_items`, `avg_urgency`). It is the planner's few-shot example and the demo fallback (`planner.hero_plan()`).

`validate_plan(plan, sample_fields) -> list[str]` returns readable problems (empty = OK):
- every tool exists, and params validate against its `Params` (pydantic errors as `n2 (classify): params.options: ...`);
- node ids are unique; at most 12 nodes;
- every field a node reads exists at that point: `"text"`, a field in the items, or one an earlier node produced. Reads are found generically: `where.field`, any `*_field` param (input/score/claim/source...), any `*_fields` list (key/compare), `block_by`, and Calculate's per-op `field`/`by`/`a`/`b`/`where` (a/b that parse as numbers are literals). A node produces its `output_field` and every `f"{output_field}_..."` field (`urgency_severity`, `rank_score`, `type__confidence`); Calculate row ops produce their `as` names, visible to later ops in the same node;
- a `where` with `eq/ne/in/not_in` on a Classify (or Filter) output uses values that output can hold (catches `"Bug"` vs `"bug"`).

`FieldScope` does the field tracking; the executor reuses it.

### Planner (`graph/planner.py`)

`async make_plan(job, run, ctx, *, max_repairs=1) -> Plan`
- **System prompt:** the planning rules (fewest nodes, filter first, one condition per option/level, filled `true_means`/`false_means`, boundaries in classify options, ordered concrete levels, no model math, dedupe issues+`block_by` for free text, rank `score_field` + `top_k ≤ 25`, `where` routing, end with calculate) plus the hero plan as one complete example.
- **User prompt:** the job, the item fields with up to 2 example values each, 15 sample items spread across the file (200 chars each), and `compact_catalog()`: each tool's description and one line per param (`name: type = default - description`, nested models inlined as `{a, b}`, no `$defs`).
- One `ctx.llm.json()` call inside `track_usage()`, charged with `ledger.record_call("llm", ...)` and logged as `Decision(item_id="__run__", tool="planner", kind="proposal", decided_by="llm", value=<nodes>)`.
- Validated with `validate_plan`. On problems, **one** repair call with the previous JSON and the problem list (question id `plan_repair_1`). Still invalid, or the LLM call fails → `PlanError` with `.problems`.

### Executor (`graph/executor.py`)

`async execute(plan, run, ctx, on_node=None) -> ResultsFile`
- Runs nodes in order on one `RunState`. For each: look up the tool, validate params, check its field reads, run it, tag its `ToolReport` as a `NodeReport`.
- `on_node` events (dicts, JSON-ready): `{"node", "tool", "index", "total", "status"}` plus
  - `"running"` with `"why"` when a node starts, then repeated with `"done"`/`"items"` as items finish (from `ctx.progress`);
  - `"done"` with `"report"` (a `NodeReport` dict);
  - `"failed"` with `"error"` (unknown tool, invalid params, or the tool raised);
  - `"skipped"` with `"error"` naming the missing fields.
- **Partial results:** a failed node is recorded and the run continues. Later nodes that read a field the failed node would have written are skipped (same `FieldScope` logic); independent nodes still run. Failures and skips are listed in `aggregates["__failed_nodes"]` (`[{node, tool, index, total, status, error}]`; key absent when none).
- Adds `aggregates["escalation_curve"]` (`[[threshold, count], ...]`, 21 steps, from `ledger.escalation_curve()`).
- Builds results with `contracts.build_results` (full plan, `ctx.threshold`, `ledger.stats()`) and writes `runs/<run_id>/results.json`, `runs/<run_id>/plan.json` and `runs/<run_id>/decisions.jsonl`.

### Dev command

```bash
python -m foreman.dev run --items <items.json> --job "<plain English>" [--plan <plan.json>] [--save-plan <path>] [--threshold 0.7] [--fake]
```
Starts the `openai` import in a thread and fires `LLMClient.warm()` (one tiny call, not charged to the run) in the background, so the first fallback doesn't pay the cold start. Plans with `make_plan` (or loads and validates `--plan`, with `--job` overriding its job), prints the plan, prints one live line per node, then a summary: items in/out, decisions by Jev/LLM/code with Jev's share, costs, wall time, top 5 ranked, theme counts and the results path. With `--fake`, `FakeLLM` has no canned theme proposal, so a plan with `group` shows the partial-result path (group fails, its dependents are skipped).

**Stretch:** merge consecutive per-item nodes into one Jev call per item (speculative fan-out).

## 8. API and frontend (after executor)

- `POST /runs` takes items.json, the job text and a threshold, and returns `run_id`.
- `GET /runs/{id}/events` is an SSE stream of decisions, node reports and the meter.
- `GET /runs/{id}/results` returns results.json.
- `GET /runs/{id}/curve` returns the escalation curve for the confidence bar.
- **Frontend:** upload, job box, confidence slider, the live meter (Jev / LLM / code, cost, time), the results table with a per-cell "why" (the ledger), and download (Minh's exports).

## 9. Environment

See `.env.example`.
- Jev runs through Vercel AI Gateway (`AI_GATEWAY_API_KEY`).
- The LLM is DeepSeek V4 Flash on Fireworks (`FIREWORKS_API_KEY`), used for the fallback, proposals and planning.
