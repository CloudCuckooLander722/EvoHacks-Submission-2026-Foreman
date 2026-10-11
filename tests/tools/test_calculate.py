"""Calculate: every op, to_number/to_date edge cases, where routing, chaining, ledger."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from foreman.core.context import make_context
from foreman.core.state import ItemState, RunState
from foreman.tools import get_tool
from foreman.tools.calculate import CalculateParams, Operation, to_date, to_number

TODAY = "2026-10-10"


def _run() -> RunState:
    rows: list[dict[str, Any]] = [
        {"price": "$1,200.00", "qty": 2, "team": "a", "rating": 5, "ok": True, "d": "2026-10-01"},
        {"price": "(50)", "qty": 1, "team": "b", "rating": 3, "ok": "yes", "d": "Oct 5, 2026"},
        {"price": "300", "qty": 0, "team": "a", "rating": 4, "ok": False, "d": "not a date"},
        {"price": "n/a", "qty": 3, "team": "c", "rating": 1, "ok": "no"},
    ]
    items = [ItemState(id=f"i{n}", text=f"item {n}", fields=f) for n, f in enumerate(rows)]
    return RunState(run_id="r", job="j", items=items)


async def _calc(ops: list[dict[str, Any]], run: RunState | None = None, **kw: Any):
    run = run or _run()
    ctx = await make_context(run_id="r", fake=True)
    params = CalculateParams.model_validate({"operations": ops, "today": TODAY, **kw})
    report = await get_tool("calculate").run(run, params, ctx)
    return run, report, ctx


def _col(run: RunState, key: str) -> list[Any]:
    return [i.fields.get(key) for i in run.items]


# --------------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$1,234.50", 1234.5),
        ("(12.00)", -12.0),
        ("12%", 12.0),
        ("1 234,5", 1234.5),
        ("1.234,50", 1234.5),
        ("12,5", 12.5),
        ("1,234", 1234.0),
        ("1,234,567", 1234567.0),
        ("-$5", -5.0),
        ("€ 3", 3.0),
        ("100 USD", 100.0),
        ("  7 ", 7.0),
        ("−4", -4.0),
        (3, 3.0),
        (2.5, 2.5),
        ("-", None),
        ("", None),
        ("n/a", None),
        (None, None),
        ("abc", None),
        ("12abc", None),
        ("nan", None),
        (True, None),
        ([1], None),
        (float("inf"), None),
    ],
)
def test_to_number(raw: Any, expected: float | None) -> None:
    assert to_number(raw) == expected


def test_to_date() -> None:
    today = date(2026, 10, 10)
    assert to_date("2026-09-30", today) == date(2026, 9, 30)
    assert to_date("Sep 30, 2026", today) == date(2026, 9, 30)
    assert to_date("March 2025", today) == date(2025, 3, 1)
    assert to_date("not a date", today) is None
    assert to_date("", today) is None
    assert to_date(None, today) is None


# --------------------------------------------------------------------------- row ops


async def test_parse_number_and_parse_date() -> None:
    run, report, _ = await _calc(
        [
            {"op": "parse_number", "field": "price", "as": "price_n"},
            {"op": "parse_date", "field": "d", "as": "d_iso"},
        ]
    )
    assert _col(run, "price_n") == [1200.0, -50.0, 300.0, None]
    assert _col(run, "d_iso") == ["2026-10-01", "2026-10-05", None, None]
    assert run.items[0].fields["price_n__by"] == "code"
    assert run.items[0].fields["price_n__confidence"] == 1.0
    assert report.code_ops == 3 + 2
    assert "parse_number -> price_n: skipped 1 of 4" in report.notes
    assert "parse_date -> d_iso: skipped 2 of 4" in report.notes


async def test_arithmetic_with_fields_and_literals() -> None:
    run, _, _ = await _calc(
        [
            {"op": "add", "a": "rating", "b": "qty", "as": "add"},
            {"op": "sub", "a": "rating", "b": 1, "as": "sub"},
            {"op": "mul", "a": "price", "b": "qty", "as": "mul"},
            {"op": "div", "a": "rating", "b": "qty", "as": "div"},
            {"op": "mul", "a": "rating", "b": "10", "as": "x10"},
        ]
    )
    assert _col(run, "add") == [7.0, 4.0, 4.0, 4.0]
    assert _col(run, "sub") == [4.0, 2.0, 3.0, 0.0]
    assert _col(run, "mul") == [2400.0, -50.0, 0.0, None]
    assert _col(run, "div") == [2.5, 3.0, None, pytest.approx(1 / 3)]  # qty 0 -> None
    assert _col(run, "x10") == [50.0, 30.0, 40.0, 10.0]


async def test_abs_round_bucket() -> None:
    run, _, _ = await _calc(
        [
            {"op": "abs", "field": "price", "as": "abs"},
            {"op": "div", "a": "rating", "b": 3, "as": "third"},
            {"op": "round", "field": "third", "ndigits": 2, "as": "r2"},
            {"op": "round", "field": "third", "as": "r0"},
            {
                "op": "bucket",
                "field": "rating",
                "edges": [2, 4],
                "labels": ["low", "mid", "high"],
                "as": "band",
            },
        ]
    )
    assert _col(run, "abs") == [1200.0, 50.0, 300.0, None]
    assert _col(run, "r2") == [1.67, 1.0, 1.33, 0.33]
    assert _col(run, "r0") == [2, 1, 1, 0]
    assert _col(run, "band") == ["high", "mid", "high", "low"]  # edge value goes up


async def test_date_diff_and_days_since() -> None:
    run = _run()
    run.items[0].fields["closed"] = "2026-10-11"
    run.items[1].fields["closed"] = "2026-10-05"
    run, report, _ = await _calc(
        [
            {"op": "date_diff_days", "a": "d", "b": "closed", "as": "open_days"},
            {"op": "days_since", "field": "d", "as": "age"},
        ],
        run,
    )
    assert _col(run, "open_days") == [10, 0, None, None]  # bad / missing date -> None
    assert _col(run, "age") == [9, 5, None, None]
    assert report.code_ops == 2 + 2


async def test_days_since_defaults_to_today() -> None:
    run = _run()
    run.items[0].fields["d"] = date.today().isoformat()
    ctx = await make_context(fake=True)
    params = CalculateParams.model_validate(
        {"operations": [{"op": "days_since", "field": "d", "as": "age"}]}
    )
    await get_tool("calculate").run(run, params, ctx)
    assert run.items[0].fields["age"] == 0


# --------------------------------------------------------------------------- aggregates


async def test_scalar_aggregates() -> None:
    run, report, _ = await _calc(
        [
            {"op": "count", "as": "n"},
            {"op": "count_true", "field": "ok", "as": "n_ok"},
            {"op": "sum", "field": "price", "as": "total"},
            {"op": "mean", "field": "rating", "as": "avg"},
            {"op": "mean", "field": "price", "ndigits": 1, "as": "avg_price"},
            {"op": "median", "field": "rating", "as": "med"},
            {"op": "min", "field": "price", "as": "lo"},
            {"op": "max", "field": "price", "as": "hi"},
            {"op": "mean", "field": "missing", "as": "none"},
        ]
    )
    agg = run.aggregates
    assert agg["n"] == 4
    assert agg["n_ok"] == 2
    assert agg["total"] == 1450.0  # "$1,200.00" + "(50)" + "300"; "n/a" skipped
    assert agg["avg"] == 3.25
    assert agg["avg_price"] == 483.3
    assert agg["med"] == 3.5
    assert agg["lo"] == -50.0
    assert agg["hi"] == 1200.0
    assert agg["none"] is None
    assert report.code_ops == 9
    assert "sum -> total: skipped 1 of 4" in report.notes


async def test_group_aggregates_share_top_n() -> None:
    run, _, _ = await _calc(
        [
            {"op": "group_count", "by": "team", "as": "by_team"},
            {"op": "group_sum", "by": "team", "field": "price", "as": "spend"},
            {"op": "group_mean", "by": "team", "field": "rating", "as": "team_rating"},
            {"op": "share", "by": "team", "as": "team_share"},
            {"op": "top_n", "field": "rating", "n": 2, "as": "best"},
            {"op": "top_n", "field": "rating", "n": 2, "desc": False, "as": "worst"},
            {"op": "top_n", "field": "price", "as": "priciest"},
            {"op": "group_count", "by": "rating", "as": "by_rating"},
        ]
    )
    agg = run.aggregates
    assert agg["by_team"] == {"a": 2, "b": 1, "c": 1}
    assert list(agg["by_team"]) == ["a", "b", "c"]
    assert agg["spend"] == {"a": 1500.0, "c": 0.0, "b": -50.0}
    assert list(agg["spend"]) == ["a", "c", "b"]  # sorted by value, descending
    assert agg["team_rating"] == {"a": 4.5, "b": 3.0, "c": 1.0}
    assert agg["team_share"] == {"a": 50.0, "b": 25.0, "c": 25.0}
    assert agg["best"] == ["i0", "i2"]
    assert agg["worst"] == ["i3", "i1"]
    assert agg["priciest"] == ["i0", "i2", "i1"]  # "n/a" skipped, default n
    assert agg["by_rating"] == {"1": 1, "3": 1, "4": 1, "5": 1}  # JSON-safe keys


async def test_group_count_skips_missing_and_share_rounds() -> None:
    run = RunState(
        run_id="r",
        job="j",
        items=[ItemState(id=str(i), text="t", fields={"k": "x" if i else "y"}) for i in range(3)]
        + [ItemState(id="m", text="t")],
    )
    run, report, _ = await _calc(
        [
            {"op": "group_count", "by": "k", "as": "g"},
            {"op": "share", "by": "k", "as": "s"},
        ],
        run,
    )
    assert run.aggregates["g"] == {"x": 2, "y": 1}
    assert run.aggregates["s"] == {"x": 66.7, "y": 33.3}
    assert "group_count -> g: skipped 1 of 4" in report.notes


# --------------------------------------------------------------------------- routing / chaining


async def test_op_where_and_tool_where_are_anded() -> None:
    run, report, _ = await _calc(
        [
            {"op": "count", "as": "a_team"},
            {
                "op": "count",
                "as": "a_team_good",
                "where": {"field": "rating", "op": "gte", "value": 5},
            },
            {"op": "parse_number", "field": "price", "as": "p"},
        ],
        where={"field": "team", "op": "eq", "value": "a"},
    )
    assert run.aggregates["a_team"] == 2
    assert run.aggregates["a_team_good"] == 1
    assert _col(run, "p") == [1200.0, None, 300.0, None]
    assert "p" not in run.items[1].fields  # not selected, not written
    assert "2 of 4 items matched `where`" in report.notes


async def test_chaining_parse_then_group_sum() -> None:
    run = _run()
    run.items[0].fields["price"] = "abc"
    run, report, _ = await _calc(
        [
            {"op": "parse_number", "field": "price", "as": "amount"},
            {"op": "mul", "a": "amount", "b": "qty", "as": "line"},
            {"op": "group_sum", "by": "team", "field": "line", "as": "by_team"},
            {"op": "count", "as": "n_valid", "where": {"field": "amount", "op": "truthy"}},
        ],
        run,
    )
    assert _col(run, "line") == [None, -50.0, 0.0, None]
    assert run.aggregates["by_team"] == {"a": 0.0, "c": 0.0, "b": -50.0}
    assert run.aggregates["n_valid"] == 2
    assert "group_sum -> by_team: skipped 2 of 4" in report.notes


# --------------------------------------------------------------------------- ledger / report


async def test_ledger_has_one_compute_decision_per_op() -> None:
    _, report, ctx = await _calc(
        [
            {"op": "parse_number", "field": "price", "as": "p"},
            {"op": "sum", "field": "p", "as": "total"},
        ]
    )
    ds = ctx.ledger.decisions
    assert [d.question_id for d in ds] == ["p", "total"]
    assert all(d.kind == "compute" and d.decided_by == "code" for d in ds)
    assert all(d.item_id == "__run__" and d.confidence == 1.0 for d in ds)
    assert ds[0].value == {"rows_written": 3, "skipped": 1}
    assert ds[1].value == 1450.0
    assert ctx.ledger.stats().by["code"] == 2
    assert ctx.ledger.stats().jev_calls == 0 and ctx.ledger.stats().llm_calls == 0
    assert report.jev_decisions == 0 and report.llm_decisions == 0
    assert report.items_in == report.items_out == 4


# --------------------------------------------------------------------------- validation


def test_params_accept_as_alias_and_round_trip() -> None:
    p = CalculateParams.model_validate({"operations": [{"op": "count", "as": "n"}]})
    assert p.operations[0].as_ == "n"
    assert p.output_field == "calc"
    assert p.model_dump(mode="json")["operations"][0]["as"] == "n"
    assert Operation(op="count", as_="n").as_ == "n"


@pytest.mark.parametrize(
    "op",
    [
        {"op": "count"},  # missing "as"
        {"op": "sum", "as": "x"},  # missing field
        {"op": "div", "a": "x", "as": "x"},  # missing b
        {"op": "group_sum", "by": "t", "as": "x"},  # missing field
        {"op": "share", "as": "x"},  # missing by
        {"op": "bucket", "field": "f", "edges": [1, 2], "labels": ["a", "b"], "as": "x"},
        {"op": "bucket", "field": "f", "edges": [2, 1], "labels": ["a", "b", "c"], "as": "x"},
        {"op": "bucket", "field": "f", "as": "x"},
        {"op": "explode", "as": "x"},
        {"op": "top_n", "field": "f", "n": 0, "as": "x"},
    ],
)
def test_invalid_operations_rejected(op: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CalculateParams.model_validate({"operations": [op]})


def test_empty_operations_and_bad_today_rejected() -> None:
    with pytest.raises(ValidationError):
        CalculateParams.model_validate({"operations": []})
    with pytest.raises(ValidationError):
        CalculateParams.model_validate(
            {"operations": [{"op": "count", "as": "n"}], "today": "10/10/2026"}
        )


def test_registered_as_code_tool() -> None:
    tool = get_tool("calculate")
    assert tool.uses == "code"
    assert "group" in tool.description and "percent" in tool.description
