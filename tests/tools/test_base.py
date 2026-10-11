"""PerItemTool.run with a dummy tool defined here (not in the package), plus the registry."""

from typing import ClassVar

import pytest

from foreman.core.context import make_context
from foreman.core.questions import Answer, Noul, Question
from foreman.core.state import Condition, ItemState, RunState, set_output
from foreman.testing.fakes import FakeAnswer, FakeJev, FakeLLM
from foreman.tools import catalog, get_tool
from foreman.tools.base import PerItemTool, ToolParams


class DummyParams(ToolParams):
    output_field: str = "is_bug"


class DummyTool(PerItemTool):
    name: ClassVar[str] = "dummy"
    description: ClassVar[str] = "Test tool."
    Params: ClassVar[type[ToolParams]] = DummyParams
    uses: ClassVar = "jev"

    def questions(self, item: ItemState, params) -> dict[str, Question]:
        return {"bug": Noul("Does the text describe a bug?", true="crash broken")}

    def apply(self, item: ItemState, answers: dict[str, Answer], params) -> None:
        a = answers["bug"]
        set_output(
            item,
            params.output_field,
            float(a.value) >= 0.5,
            confidence=a.confidence,
            by=a.decided_by,
            low_confidence=a.low_confidence,
        )


def _run() -> RunState:
    texts = {
        "a": "App crashes on launch",
        "b": "Love it",
        "c": "Camera is broken",
        "d": "x" * 400,
    }
    items = [
        ItemState(id=k, text=v, fields={"rating": i}) for i, (k, v) in enumerate(texts.items())
    ]
    return RunState(run_id="r", job="j", items=items)


async def test_run_respects_where_and_counts() -> None:
    def rule(state, _qid, _q):
        return FakeAnswer(0.6) if "Camera" in state else None  # confidence 0.2 -> fallback

    progress: list[tuple[str, int, int]] = []
    ctx = await make_context(threshold=0.7, jev=FakeJev(rule), llm=FakeLLM())
    ctx.progress = lambda tool, done, total: progress.append((tool, done, total))
    run = _run()
    params = DummyParams(where=Condition(field="rating", op="lte", value=2))
    report = await DummyTool().run(run, params, ctx)

    by_id = {i.id: i for i in run.items}
    assert by_id["a"].fields["is_bug"] is True and by_id["a"].fields["is_bug__by"] == "jev"
    assert by_id["b"].fields["is_bug"] is False
    assert by_id["c"].fields["is_bug__by"] == "llm"
    assert "is_bug" not in by_id["d"].fields  # rating 3 does not match `where`
    assert report.items_in == 4 and report.items_out == 4
    assert report.jev_decisions == 2 and report.llm_decisions == 1 and report.low_confidence == 0
    assert report.duration_ms >= 0
    assert progress[-1] == ("dummy", 3, 3)


async def test_run_low_confidence_counted() -> None:
    ctx = await make_context(
        threshold=0.9, jev=FakeJev(lambda *_: FakeAnswer(0.7)), llm=FakeLLM(fail=True)
    )
    report = await DummyTool().run(_run(), DummyParams(), ctx)
    assert report.low_confidence == 4 and report.jev_decisions == 4


async def test_run_skips_failed_items() -> None:
    ctx = await make_context(jev=FakeJev(max_state_tokens=50), llm=FakeLLM())
    run = _run()
    report = await DummyTool().run(run, DummyParams(), ctx)
    d = next(i for i in run.items if i.id == "d")
    assert "is_bug" not in d.fields
    assert "state is" in d.fields["is_bug__error"]
    assert any(n.startswith("d: skipped (StateTooLarge") for n in report.notes)
    assert report.jev_decisions == 3

    ctx = await make_context(jev=FakeJev(fail=True), llm=FakeLLM(fail=True))
    run = _run()
    report = await DummyTool().run(run, DummyParams(), ctx)
    assert all("is_bug__error" in i.fields for i in run.items)
    assert sum("DecisionError" in n for n in report.notes) == 4


def test_registry_ignores_tools_outside_package() -> None:
    assert all(entry["name"] != "dummy" for entry in catalog())
    with pytest.raises(KeyError, match="valid tools"):
        get_tool("dummy")
