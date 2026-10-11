"""Dev CLI: ping, tool runner, catalog.

    python -m foreman.dev ping
    python -m foreman.dev tool <name> --items <path> --params '<json>' [--threshold 0.7] [--fake] [--limit N]
    python -m foreman.dev catalog

The fastest way to see a tool working end to end, offline with `--fake` or live.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress
from rich.table import Table

from foreman.config import Settings, get_settings

console = Console()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )
    for noisy in ("httpx", "httpcore", "openai", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --------------------------------------------------------------------------- ping


async def _ping_jev(s: Settings) -> None:
    from foreman.core.jev import JevClient, JevError
    from foreman.core.questions import Noul

    console.rule("Jev")
    if s.ai_gateway_api_key is None:
        console.print("[red]AI_GATEWAY_API_KEY is missing in .env[/red] — Jev cannot be reached.")
        return
    async with JevClient(s) as jev:
        try:
            res = await jev.ask(
                "The app crashes every time I open the camera.",
                {"bug": Noul("Does the text describe a software bug?")},
            )
        except JevError as e:
            console.print(
                f"[red]Jev failed[/red] at {jev.url}: status={e.status} type={e.error_type}"
            )
            console.print(f"  message: {e.message}")
        else:
            a = res.answers["bug"]
            console.print(
                f"[green]ok[/green] model={s.jev_model} latency={res.latency_ms}ms "
                f"cost=${res.cost_usd:.8f} input_tokens={res.input_tokens} "
                f"P(yes)={float(a.value):.3f} confidence={a.confidence:.3f}"
            )

    url = f"{s.jev_base_url.rstrip('/')}/v1/models"
    try:
        async with httpx.AsyncClient(timeout=s.jev_timeout_s) as http:
            r = await http.get(
                url, headers={"Authorization": f"Bearer {s.ai_gateway_api_key.get_secret_value()}"}
            )
        r.raise_for_status()
        body = r.json()
        # The gateway returns {"models": [{"name": ...}]}; accept OpenAI's {"data": [{"id": ...}]} too.
        entries = body.get("models") or body.get("data") or []
        ids = [m.get("name") or m.get("id") for m in entries if isinstance(m, dict)]
        console.print(f"models at {url}: {', '.join(map(str, ids)) or '(none listed)'}")
    except (httpx.HTTPError, ValueError) as e:
        console.print(f"[yellow]could not list models[/yellow] at {url}: {e}")


async def _ping_llm(s: Settings) -> None:
    from foreman.core.llm import LLMClient, LLMError

    console.rule("LLM")
    if s.fireworks_api_key is None:
        console.print(
            "[red]FIREWORKS_API_KEY is missing in .env[/red] — the LLM cannot be reached."
        )
        return
    async with LLMClient(s) as llm:
        t0 = time.perf_counter()
        try:
            out = await llm.json("Reply with only a JSON object.", 'Return exactly {"ok": true}.')
        except LLMError as e:
            console.print(f"[red]LLM failed[/red] at {s.llm_base_url}: {e}")
            return
        ms = int((time.perf_counter() - t0) * 1000)
        console.print(
            f"[green]ok[/green] model={s.llm_model} latency={ms}ms cost=${llm.cost_usd:.8f} "
            f"tokens={llm.prompt_tokens}+{llm.completion_tokens} "
            f"json_mode={llm._json_mode} reply={json.dumps(out)}"
        )


async def cmd_ping(_args: argparse.Namespace) -> int:
    s = get_settings()
    await _ping_jev(s)
    await _ping_llm(s)
    return 0


# --------------------------------------------------------------------------- tool


def _print_validation_error(e: ValidationError) -> None:
    t = Table(title="Invalid params", show_lines=False)
    t.add_column("field")
    t.add_column("problem")
    t.add_column("input")
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"]) or "(root)"
        t.add_row(loc, err["msg"], json.dumps(err.get("input"), default=str)[:60])
    console.print(t)


def _fmt_cell(fields: dict[str, Any], key: str) -> str:
    v = fields.get(key, "")
    conf = fields.get(f"{key}__confidence")
    by = fields.get(f"{key}__by")
    text = json.dumps(v, default=str) if not isinstance(v, str) else v
    if len(text) > 40:
        text = text[:37] + "..."
    extra = []
    if isinstance(conf, (int, float)):
        extra.append(f"{conf:.2f}")
    if by:
        extra.append(str(by))
    if fields.get(f"{key}__low_confidence"):
        extra.append("LOW")
    return f"{text} ({', '.join(extra)})" if extra else text


def _results_table(run: Any, added: list[str]) -> Table:
    t = Table(title=f"{len(run.items)} items (showing up to 20)")
    t.add_column("id", no_wrap=True)
    t.add_column("text")
    for key in added:
        t.add_column(key)
    for item in run.items[:20]:
        t.add_row(item.id, item.text[:60], *(_fmt_cell(item.fields, k) for k in added))
    return t


async def cmd_tool(args: argparse.Namespace) -> int:
    from foreman.contracts import NodeReport, build_results, load_items, save_results
    from foreman.core.context import make_context
    from foreman.tools import get_tool

    s = get_settings()
    try:
        tool = get_tool(args.name)
    except KeyError as e:
        console.print(f"[red]{e.args[0]}[/red]")
        return 2
    try:
        params = tool.Params.model_validate_json(args.params)
    except ValidationError as e:
        _print_validation_error(e)
        return 2

    run = load_items(args.items, job=args.job)
    if args.limit:
        run.items = run.items[: args.limit]
    before = {i.id: set(i.fields) for i in run.items}

    ctx = await make_context(run_id=run.run_id, threshold=args.threshold, fake=args.fake)
    started = datetime.now(UTC)
    mode = "fake" if args.fake else "live"
    console.print(
        f"run {run.run_id}: {tool.name} on {len(run.items)} items ({mode}, threshold {ctx.threshold})"
    )
    try:
        with Progress(console=console, transient=True) as bar:
            task = bar.add_task(tool.name, total=None)
            ctx.progress = lambda _t, done, total: bar.update(task, completed=done, total=total)
            report = await tool.run(run, params, ctx)
    finally:
        await ctx.aclose()
    finished = datetime.now(UTC)

    added: list[str] = []
    for item in run.items:
        for k in item.fields:
            if k not in before.get(item.id, set()) and "__" not in k and k not in added:
                added.append(k)
    error_key = f"{params.output_field}__error"
    if any(error_key in i.fields for i in run.items):
        added.append(error_key)
    console.print(_results_table(run, added))
    if run.dropped:
        console.print(f"dropped: {len(run.dropped)} items")

    console.rule("ToolReport")
    console.print_json(report.model_dump_json())
    console.rule("Ledger stats")
    console.print_json(ctx.ledger.stats().model_dump_json())
    console.rule("Aggregates")
    console.print_json(json.dumps(run.aggregates, default=str))

    results = build_results(
        run,
        ctx.ledger,
        plan={
            "job": run.job,
            "nodes": [{"id": "n1", "tool": tool.name, "params": params.model_dump(mode="json")}],
        },
        threshold=ctx.threshold,
        started_at=started,
        finished_at=finished,
        node_reports=[NodeReport(node="n1", **report.model_dump())],
        runs_dir=s.runs_dir,
    )
    out = save_results(results, Path("out") / f"{tool.name}_results.json")
    console.print(f"wrote {out} and {results.decisions_file}")
    return 0


# --------------------------------------------------------------------------- catalog


async def cmd_catalog(_args: argparse.Namespace) -> int:
    from foreman.tools import catalog

    entries = catalog()
    if not entries:
        console.print("[yellow]no tools registered yet[/yellow]")
        return 0
    t = Table(title="Tool catalog", show_lines=True)
    t.add_column("name", no_wrap=True)
    t.add_column("uses", no_wrap=True)
    t.add_column("description")
    t.add_column("params")
    for e in entries:
        props = e["params_schema"].get("properties", {})
        t.add_row(e["name"], e["uses"], e["description"], ", ".join(props))
    console.print(t)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m foreman.dev", description="Foreman dev CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping", help="check Jev and LLM keys and endpoints").set_defaults(fn=cmd_ping)

    t = sub.add_parser("tool", help="run one tool on an items.json file")
    t.add_argument("name")
    t.add_argument("--items", required=True, help="path to items.json")
    t.add_argument("--params", default="{}", help="tool params as JSON")
    t.add_argument(
        "--threshold", type=float, default=None, help="confidence threshold (default from .env)"
    )
    t.add_argument("--fake", action="store_true", help="use FakeJev/FakeLLM (offline)")
    t.add_argument("--limit", type=int, default=None, help="only the first N items")
    t.add_argument("--job", default="", help="job text recorded in results.json")
    t.set_defaults(fn=cmd_tool)

    sub.add_parser("catalog", help="print the tool catalog").set_defaults(fn=cmd_catalog)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _setup_logging(get_settings().log_level)
    return asyncio.run(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
