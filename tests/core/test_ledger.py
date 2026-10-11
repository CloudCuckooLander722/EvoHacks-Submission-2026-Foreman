"""Ledger: stats, listeners, escalation curve, JSONL dump."""

import json

import pytest

from foreman.core.ledger import Decision, Ledger


def _d(by: str, conf: float | None, **kw) -> Decision:
    return Decision(
        run_id="r",
        item_id=kw.pop("item_id", "i"),
        tool="t",
        question_id="q",
        qtype="noul",
        value=1.0,
        confidence=conf,
        probabilities=None,
        decided_by=by,
        **kw,
    )


def test_stats_and_calls() -> None:
    led = Ledger("r")
    led.extend(
        [_d("jev", 0.9), _d("jev", 0.3, low_confidence=True), _d("llm", 1.0, jev_confidence=0.2)]
    )
    led.add(_d("code", None))
    led.record_call("jev", 0.002, n=2)
    led.record_call("llm", 0.01)
    st = led.stats()
    assert st.decisions_total == 4
    assert st.by == {"jev": 2, "llm": 1, "code": 1}
    assert st.low_confidence == 1
    assert st.jev_calls == 2 and st.llm_calls == 1
    assert st.jev_cost_usd == pytest.approx(0.002)
    assert st.llm_cost_usd == pytest.approx(0.01)
    assert st.wall_time_s >= 0


def test_listeners_and_errors_are_swallowed() -> None:
    led = Ledger("r")
    seen: list[Decision] = []

    def bad(_d: Decision) -> None:
        raise RuntimeError("listener broke")

    led.on_event(bad)
    led.on_event(seen.append)
    led.add(_d("jev", 0.9))
    assert len(seen) == 1


def test_escalation_curve() -> None:
    led = Ledger("r")
    led.extend(
        [_d("jev", 0.9), _d("jev", 0.3), _d("llm", 1.0, jev_confidence=0.5), _d("code", None)]
    )
    led.add(_d("llm", 1.0))  # LLM decision Jev never saw: not counted
    curve = dict(led.escalation_curve())
    assert len(curve) == 21
    assert curve[0.0] == 0
    assert curve[0.5] == 1
    assert curve[0.55] == 2
    assert curve[0.95] == 3
    assert curve[1.0] == 3


def test_dump_jsonl(tmp_path) -> None:
    led = Ledger("r")
    led.extend([_d("jev", 0.9, item_id="a"), _d("jev", 0.1, item_id="b")])
    path = led.dump_jsonl(tmp_path / "sub" / "decisions.jsonl")
    lines = path.read_text().splitlines()
    assert [json.loads(x)["item_id"] for x in lines] == ["a", "b"]
