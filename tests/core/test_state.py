"""Node state: routing conditions and the output-field convention."""

import pytest

from foreman.config import Settings
from foreman.core.state import Condition, ItemState, get_input, set_output


def _item(**fields) -> ItemState:
    return ItemState(id="a", text="hello", fields=fields)


def test_settings_defaults_without_keys(monkeypatch) -> None:
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "")
    s = Settings(_env_file=None)
    assert s.ai_gateway_api_key is None
    assert s.jev_max_state_tokens == 28_000
    assert s.confidence_threshold == 0.7


@pytest.mark.parametrize(
    ("op", "value", "fields", "expected"),
    [
        ("eq", "bug", {"t": "bug"}, True),
        ("eq", "bug", {"t": "praise"}, False),
        ("ne", "bug", {"t": "praise"}, True),
        ("in", ["bug", "feature"], {"t": "bug"}, True),
        ("not_in", ["bug"], {"t": "bug"}, False),
        ("truthy", None, {"t": 1}, True),
        ("falsy", None, {"t": 0}, True),
        ("gte", 3, {"t": 4}, True),
        ("lte", 3, {"t": 4}, False),
        ("gte", 3, {"t": "x"}, False),
    ],
)
def test_condition_ops(op, value, fields, expected) -> None:
    assert Condition(field="t", op=op, value=value).matches(_item(**fields)) is expected


@pytest.mark.parametrize("op", ["eq", "ne", "in", "not_in", "truthy", "gte", "lte"])
def test_missing_field_never_matches(op) -> None:
    assert Condition(field="t", op=op, value=[1]).matches(_item()) is False


def test_missing_field_matches_falsy() -> None:
    assert Condition(field="t", op="falsy").matches(_item()) is True


BUG = {"field": "t", "op": "eq", "value": "bug"}
CANON = {"field": "c", "op": "truthy"}


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({"t": "bug", "c": True}, True),
        ({"t": "bug", "c": False}, False),
        ({"t": "bug"}, False),  # missing field fails its truthy test
        ({"t": "praise", "c": True}, False),
    ],
)
def test_condition_all(fields, expected) -> None:
    cond = Condition.model_validate({"all": [BUG, CANON]})
    assert cond.matches(_item(**fields)) is expected


def test_condition_any_and_nesting() -> None:
    cond = Condition.model_validate(
        {"any": [BUG, {"all": [{"field": "t", "op": "eq", "value": "praise"}, CANON]}]}
    )
    assert cond.matches(_item(t="bug")) is True
    assert cond.matches(_item(t="praise", c=1)) is True
    assert cond.matches(_item(t="praise")) is False
    assert cond.fields() == ["t", "t", "c"]


def test_condition_leaf_and_all_must_both_hold() -> None:
    cond = Condition.model_validate({**BUG, "all": [CANON]})
    assert cond.matches(_item(t="bug", c=True)) is True
    assert cond.matches(_item(t="bug")) is False


@pytest.mark.parametrize(
    "data", [{}, {"field": "t"}, {"op": "eq"}, {"all": []}, {"any": [], "all": None}]
)
def test_condition_needs_a_test(data) -> None:
    with pytest.raises(ValueError):
        Condition.model_validate(data)


def test_get_input() -> None:
    item = _item(rating=5)
    assert get_input(item, "text") == "hello"
    assert get_input(item, "rating") == "5"
    assert get_input(item, "nope") == ""


def test_set_output() -> None:
    item = _item()
    set_output(item, "type", "bug", confidence=0.9, by="jev")
    assert item.fields == {"type": "bug", "type__confidence": 0.9, "type__by": "jev"}
    set_output(item, "x", 1, confidence=0.2, by="jev", low_confidence=True)
    assert item.fields["x__low_confidence"] is True
