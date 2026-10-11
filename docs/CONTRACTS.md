# Hand-off contracts

These two files are the only interface between Minh's code and Nathan's. They are implemented as pydantic models in `foreman/contracts.py`. Both of you build against these; change them only together.

## items.json — extraction output (Minh → Nathan)

```json
{
  "source": {"filename": "reviews.xlsx", "kind": "xlsx", "sheet": "Sheet1"},
  "items": [
    {
      "id": "row-2",
      "text": "App crashes every time I open the camera. Pixel 8, latest update.",
      "fields": {"rating": 1, "date": "2026-09-30", "author": "kim_88"},
      "meta": {"row": 2}
    }
  ]
}
```

Rules:
- **One item = one unit of work:** a row, a ticket, a review, or a document section. Never put a whole file into one item unless the job is about the whole document (Find).
- `id` is unique and stable. Use `row-<n>` for sheets and `<file>-p<page>-s<section>` for documents.
- `text` is the main text the tools judge. For spreadsheets, Minh picks the main free-text column; if there is none, join the columns as `col: value; col: value`.
- `fields` holds every original column, typed where obvious (numbers, ISO dates).
- Keep `text` under about 20,000 characters per item. Split longer documents into sections, because Jev's state limit is about 32k tokens.

## results.json — executor output (Nathan → Minh)

```json
{
  "run_id": "r_8f2c",
  "job": "Turn these reviews into a backlog...",
  "threshold": 0.7,
  "started_at": "2026-10-11T08:01:12Z",
  "finished_at": "2026-10-11T08:01:49Z",
  "plan": {"nodes": [{"id": "n1", "tool": "filter", "params": {}}]},
  "items": [
    {
      "id": "row-2",
      "text": "App crashes every time I open the camera...",
      "fields": {
        "rating": 1,
        "type": "bug", "type__confidence": 0.93, "type__by": "jev",
        "theme": "Camera", "theme__confidence": 0.71, "theme__by": "llm",
        "urgency": 82.5, "urgency__confidence": 0.88, "urgency__by": "jev",
        "rank": 3
      }
    }
  ],
  "dropped": [{"id": "row-9", "reason": "filter n1: is_review=false"}],
  "aggregates": {
    "groups": [{"theme": "Camera", "description": "...", "count": 412, "example_ids": ["row-2"]}],
    "theme_counts": {"Camera": 412, "Login": 280},
    "ranking": ["row-77", "row-12", "row-2"]
  },
  "node_reports": [
    {"node": "n1", "tool": "filter", "items_in": 10000, "items_out": 9420, "jev_decisions": 10000, "llm_decisions": 310, "code_ops": 0, "low_confidence": 4, "duration_ms": 18200}
  ],
  "stats": {
    "decisions_total": 48210,
    "by": {"jev": 43500, "llm": 4573, "code": 137},
    "low_confidence": 22,
    "jev_cost_usd": 0.41, "llm_cost_usd": 0.95,
    "wall_time_s": 37.2
  },
  "decisions_file": "runs/r_8f2c/decisions.jsonl"
}
```

Notes for formatting:
- The full decision log is in a separate JSONL file (one `Decision` per line, see ARCHITECTURE §4), because it can be large. Use it for the "why" view and audit exports.
- Every tool output field `X` comes with `X__confidence` and `X__by`. Formatting can colour cells by these.
- `aggregates` holds ready-made tables and lists for charts. Formatting should never recompute them with an LLM.
