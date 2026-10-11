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
  "plan": {
    "job": "Turn these reviews into a backlog...",
    "nodes": [
      {"id": "n1", "tool": "filter", "params": {"condition": "Is this a genuine product review (not spam)?", "output_field": "is_review"}},
      {"id": "n2", "tool": "classify", "params": {"options": {"bug": "...", "feature_request": "...", "praise": "...", "other": null}, "output_field": "type"}}
    ]
  },
  "items": [
    {
      "id": "row-2",
      "text": "App crashes every time I open the camera...",
      "fields": {
        "rating": 1,
        "type": "bug", "type__confidence": 0.93, "type__by": "jev",
        "theme": "Camera", "theme__confidence": 0.71, "theme__by": "llm",
        "urgency": 82.5, "urgency__confidence": 0.88, "urgency__by": "jev",
        "rank": 3, "rank__confidence": 1.0, "rank__by": "code"
      },
      "meta": {"row": 2}
    }
  ],
  "dropped": [
    {
      "id": "row-9",
      "text": "Buy cheap followers at ...",
      "fields": {"rating": 5, "is_review": false, "is_review__confidence": 0.97, "is_review__by": "jev"},
      "reason": "filter n1: is_review=false"
    }
  ],
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
- `plan` is the full plan.json object (`job` + `nodes`, see ARCHITECTURE §7) the run executed.
- Every item keeps its `meta` from items.json unchanged (`row`, `page`, `sheet`...), so exports can point back to the source.
- Every tool output field `X` comes with `X__confidence` (0–1) and `X__by` (`"jev"`, `"llm"` or `"code"`). Formatting can colour cells by these.
- `X__low_confidence` is written only when it is `true` (Jev was unsure and the LLM fallback failed). If the key is absent, treat it as `false`.
- Outputs computed by code (e.g. `rank` sorted from a score field, `calculate` results) have `X__by: "code"` and `X__confidence: 1.0`.
- `dropped` lists items a tool removed (e.g. filter). Each entry is `{id, text, fields, reason}`; `reason` comes from `meta["drop_reason"]`, set by the tool that dropped the item. Dropped items have no `meta` in results.json.
- `aggregates` holds ready-made tables and lists for charts. Formatting should never recompute them with an LLM.

## Replay bundles — recorded runs for the static site (Nathan → Minh)

The deployed site plays back recorded runs, so it needs no API and no keys. A bundle is made from a finished API run:

```bash
python -m foreman.dev replay-export <run_id> --out web/public/replays/<name>/
```

```
web/public/replays/
  index.json                 every bundle, newest last (upserted by name)
  <name>/
    events.jsonl             the run's SSE stream, exactly as sent, one event per line
    results.json             results.json (ResultsFile), as GET /runs/{id}/results returns it
    decisions.jsonl          the full ledger, one Decision per line (the "why" panel)
    input/<file>             the uploaded file
    export.xlsx|pdf|docx     only when foreman.format.export exists
```

**events.jsonl:** one JSON object per line, in send order:

```json
{"t_ms": 253, "event": "plan", "data": {"run_id": "r_8e62399f", "job": "...", "items_total": 1000, "nodes": [...]}}
{"t_ms": 351, "event": "stats", "data": {"decisions_total": 12, "by": {"jev": 12, "llm": 0, "code": 0}, ...}}
```

- `event` is one of the §D names (`plan`, `node`, `decision`, `stats`, `done`, `error`) and `data` is that event's §D payload, byte-for-byte what the browser received.
- `t_ms` is milliseconds since the run was created (POST /runs), so the player can replay in real time or compress it (`t_ms / speed`).
- `decision` events are the same 10-per-second sample as live; the full ledger is `decisions.jsonl`. The last `stats` event equals `results.json`'s `stats`.
- A run that failed ends with `error` (`fatal: true`) and has no `done` and no results.json; replay-export only bundles finished runs.

**decisions.jsonl:** in a bundle, use this file for the "why" panel (filter by `item_id`), not the `decisions_file` path inside results.json, which points at the server's `runs/` folder.

**index.json:**

```json
{
  "version": 1,
  "replays": [
    {
      "name": "reviews-1k", "run_id": "r_8e62399f",
      "job": "Turn these app reviews into a ranked bug backlog",
      "items": 1000, "duration_s": 117.1,
      "cost_usd": 0.106869, "jev_cost_usd": 0.051219, "llm_cost_usd": 0.055651,
      "decisions": {"total": 3349, "jev": 2559, "llm": 785, "code": 5},
      "jev_share": 0.7641, "jev_share_of_model": 0.7653,
      "threshold": 0.7, "finished_at": "2026-10-11T06:38:43Z",
      "files": {"events": "events.jsonl", "results": "results.json", "decisions": "decisions.jsonl",
                "input": "input/reviews_1k.json", "exports": {"xlsx": "export.xlsx"}}
    }
  ]
}
```

- `items` is the number of items the run processed (the plan event's `items_total`).
- `jev_share` is Jev's share of all decisions; `jev_share_of_model` excludes code operations (Jev / (Jev + LLM)).
- Paths in `files` are relative to the bundle folder. `exports` is `{}` until the formatter exists.
