# Tool 04 — dedupe

Paste everything below the line into Claude Code. Prompts 00 and 01 must already be done.

---

Build the **dedupe** tool, which does entity resolution.

**Read first:**
- `CLAUDE.md`
- `docs/ARCHITECTURE.md` §3–6
- `foreman/tools/base.py`
- `foreman/core/*`
- `foreman/testing/fakes.py`

**Only create or edit:**
- `foreman/tools/dedupe.py`
- `tests/tools/test_dedupe.py`
- `data/samples/dedupe.json`

## What it does

Dedupe finds items that describe the same real-world thing: the same customer, the same vendor ("ADOBE CC" vs "ADOBE SYSTEMS"), or the same bug reported twice. Code cheaply proposes candidate pairs, Jev judges each pair, and union-find builds the clusters. Jev never sees all n² pairs.

`name = "dedupe"`, `uses = "jev"`.

The planner-facing `description` should say:
- what Dedupe does;
- that `key_fields` should hold the identifying fields.

## Params

| Param | Type | Default | Meaning |
|---|---|---|---|
| `key_fields` | `list[str]` | `["text"]` | Fields used to find candidates (`"text"` = `item.text`). |
| `compare_fields` | `list[str] \| None` | `None` | Fields shown to Jev for the judgment. Defaults to `key_fields`; at most 8. |
| `min_similarity` | `int` | `55` | rapidfuzz `token_set_ratio` cutoff, 0–100. |
| `max_pairs` | `int` | `20000` | |
| `match_threshold` | `float` | `0.75` | On the normalized same-entity score. |
| `check_conflicts` | `bool` | `True` | |
| `output_field` | | `"dup_group"` | |

## Algorithm (override `run()`)

1. **Select items** matching `where`.

2. **Key string.** For each item, join the values of `key_fields` with " | " and normalize:
   - lowercase;
   - turn punctuation into spaces;
   - drop pure-digit tokens of 3 or more characters, such as store numbers and dates;
   - collapse whitespace.

3. **Candidate pairs**, using `rapidfuzz`:
   - **n ≤ 3000:** `process.cdist(keys, keys, scorer=fuzz.token_set_ratio, score_cutoff=min_similarity, workers=-1)`, keeping pairs where i < j.
   - **n > 3000:** block first. Build an index from token (the first 5 characters of each token with 3 or more characters) to item indices, skip tokens appearing in more than 200 items, and compare within blocks only.
   - Sort pairs by similarity, highest first. Keep at most `max_pairs`; if the list was cut, add a report note.

4. **Judge each pair** with `ctx.decider.decide(item_id=f"{a.id}|{b.id}", tool="dedupe", state=..., questions=...)` through `gather_bounded`.
   - **State:** `{"record_a": {f: value for f in compare_fields}, "record_b": {...}}`, where `"text"` maps to the item text, truncated to 1,500 characters each.
   - **Question `same`:** a Score with these instructions:

     > Do `record_a` and `record_b` describe the same real-world entity (the same person, company, product, account, or the same reported issue)? Formatting differences, abbreviations and typos do not make them different.

     Levels, low → high:
     1. "Clearly different entities"
     2. "Probably different entities"
     3. "Cannot tell from the information given"
     4. "Probably the same entity"
     5. "Clearly the same entity"
   - **If `check_conflicts`:** add one Noul `conflict_<field>` per compare field (at most 5 fields, `fallback=False`). Instructions: "Do `record_a` and `record_b` give genuinely conflicting values for `<field>` (not just different formatting or one being empty)?"

5. **Match.** `norm = same.value / 4`. A pair matches if `norm >= match_threshold`. Record the conflicting fields (conflict p ≥ 0.5) per matched pair.

6. **Cluster** the matched pairs with union-find. For each cluster of size 2 or more:
   - Group id: `dup-1`, `dup-2`, and so on, ordered by the earliest member.
   - Canonical member: the one with the most non-empty fields; ties go to the earliest.
   - For every member:
     - `set_output(item, out, group_id, confidence=<min confidence of its matched edges>, by=<"llm" if any edge was LLM-decided else "jev">)`
     - `fields[f"{out}_canonical"] = item is canonical`
   - Items in no cluster get `fields[out] = None`, with no confidence fields.

7. **Aggregates.** `run.aggregates[f"{out}_clusters"]` = a list of `{group, canonical_id, member_ids, conflicts: sorted set of field names}`.

8. **Report.** Notes give the number of pairs checked and the number of clusters. `items_out` = the number of unique entities (non-duplicates plus one per cluster).

Note in a docstring that union-find can chain A~B~C into one cluster; that is acceptable for this build.

## Sample data

`data/samples/dedupe.json`: about 20 vendor records with `fields.vendor` and `fields.address`. Include messy duplicates such as:
- "ADOBE *CREATIVE CLD 800-833-6687" vs "Adobe Systems Inc"
- "SQ *BLUE BOTTLE 0423" vs "Blue Bottle Coffee"
- two distinct "Starbucks" stores at different addresses (a conflict).

## Tests (offline)

- Key normalization.
- Candidate generation finds the expected pairs and skips obviously different ones.
- A `FakeJev` rule returns a high same-entity score for chosen pairs. Assert the clusters, the group ids, the canonical choice and the aggregates.
- Below-threshold pairs don't cluster.
- Conflicts are recorded.
- The `max_pairs` cap adds a note.
- The blocking path for n > 3000. Use a small `min_similarity` with synthetic data, monkeypatch the 3000 limit down for speed, and assert it runs without n² comparisons.

## Done when

- `pytest -q` passes.
- The dev CLI runs on `data/samples/dedupe.json` with `--fake` and `{"key_fields": ["vendor"], "compare_fields": ["vendor", "address"]}`.
- You've committed with `feat(tools): dedupe`.
