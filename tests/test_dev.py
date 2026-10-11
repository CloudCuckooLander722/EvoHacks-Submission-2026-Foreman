"""Dev CLI end to end with a dummy tool registered for the test only."""

import json
from pathlib import Path
from typing import ClassVar

import pytest

import foreman.tools as registry
from foreman import dev
from foreman.core.questions import Choice
from foreman.core.state import set_output
from foreman.tools.base import PerItemTool, ToolParams

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "samples" / "reviews_small.json"


class KindParams(ToolParams):
    output_field: str = "kind"


class KindTool(PerItemTool):
    name: ClassVar[str] = "kind"
    description: ClassVar[str] = "Test tool."
    Params: ClassVar[type[ToolParams]] = KindParams
    uses: ClassVar = "jev"

    def questions(self, item, params):
        return {
            "k": Choice(
                "What kind of review?",
                {"bug": "crash broken", "praise": "love great", "other": None},
            )
        }

    def apply(self, item, answers, params):
        a = answers["k"]
        set_output(item, params.output_field, a.value, confidence=a.confidence, by=a.decided_by)


@pytest.fixture
def with_kind_tool(monkeypatch):
    monkeypatch.setattr(registry, "_registry", {"kind": KindTool})


def test_dev_tool_fake(with_kind_tool, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    code = dev.main(
        ["tool", "kind", "--items", str(SAMPLE), "--fake", "--limit", "5", "--params", "{}"]
    )
    assert code == 0
    out = json.loads((tmp_path / "out" / "kind_results.json").read_text())
    assert len(out["items"]) == 5
    assert out["items"][0]["fields"]["kind"] == "bug"
    assert out["node_reports"][0]["tool"] == "kind"
    assert (tmp_path / out["decisions_file"]).exists()


def test_dev_tool_bad_params(with_kind_tool, capsys) -> None:
    assert (
        dev.main(["tool", "kind", "--items", str(SAMPLE), "--fake", "--params", '{"where": 3}'])
        == 2
    )


def test_dev_unknown_tool_and_catalog(with_kind_tool) -> None:
    assert dev.main(["tool", "nope", "--items", str(SAMPLE), "--fake"]) == 2
    assert dev.main(["catalog"]) == 0
