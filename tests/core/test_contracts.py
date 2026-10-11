"""items.json / results.json round trip."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from foreman.contracts import ResultsFile, build_results, load_items, save_results
from foreman.core.ledger import Decision, Ledger
from foreman.core.state import ItemState

SAMPLE = Path(__file__).resolve().parents[2] / "data" / "samples" / "reviews_small.json"


def test_load_sample_items() -> None:
    run = load_items(SAMPLE, job="triage")
    assert 20 <= len(run.items) <= 30
    assert run.job == "triage" and run.run_id.startswith("r_")
    assert all(isinstance(i.fields.get("rating"), int) for i in run.items)


def test_load_items_rejects_duplicate_ids(tmp_path) -> None:
    p = tmp_path / "items.json"
    p.write_text(
        json.dumps({"source": {}, "items": [{"id": "a", "text": "x"}, {"id": "a", "text": "y"}]})
    )
    with pytest.raises(ValueError, match="unique"):
        load_items(p)


def test_build_and_save_results(tmp_path) -> None:
    run = load_items(SAMPLE, run_id="r_test", job="triage")
    dropped = ItemState(
        id="spam-1",
        text="buy",
        fields={"is_review": False},
        meta={"drop_reason": "filter n1: is_review=false"},
    )
    run.dropped.append(dropped)
    run.aggregates["theme_counts"] = {"Camera": 3}
    ledger = Ledger("r_test")
    ledger.add(
        Decision(
            run_id="r_test",
            item_id=run.items[0].id,
            tool="filter",
            question_id="q",
            qtype="noul",
            value=0.9,
            confidence=0.8,
            probabilities={"true": 0.9, "false": 0.1},
            decided_by="jev",
        )
    )
    results = build_results(
        run,
        ledger,
        plan={"nodes": [{"id": "n1", "tool": "filter", "params": {}}]},
        threshold=0.7,
        started_at=datetime(2026, 10, 11, 8, 1, 12, tzinfo=UTC),
        finished_at="2026-10-11T08:01:49Z",
        node_reports=[{"node": "n1", "tool": "filter", "items_in": 25, "items_out": 23}],
        runs_dir=tmp_path / "runs",
    )
    assert results.started_at == "2026-10-11T08:01:12Z"
    assert results.dropped[0].model_dump() == {
        "id": "spam-1",
        "text": "buy",
        "fields": {"is_review": False},
        "reason": "filter n1: is_review=false",
    }
    assert results.items[0].meta == {"row": 2}
    assert results.stats.decisions_total == 1
    assert Path(results.decisions_file).read_text().count("\n") == 1
    assert results.decisions_file.endswith("r_test/decisions.jsonl")

    out = save_results(results, tmp_path / "out" / "results.json")
    again = ResultsFile.model_validate_json(out.read_text())
    assert again.aggregates == {"theme_counts": {"Camera": 3}}
    assert again.node_reports[0].node == "n1"
