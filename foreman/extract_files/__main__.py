"""CLI: `python -m foreman.extract_files <file> -o items.json [--check]`.

Prints the item count, the chosen text column and any warnings, so a user can see at a
glance whether their file was read the way they expected before running a job on it.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from rich.console import Console

from foreman.contracts import ItemsFile
from foreman.extract_files import ExtractError, extract

console = Console()


async def _check(items: ItemsFile, fake: bool) -> list[str]:
    from foreman.core.context import make_context
    from foreman.extract_files.check import check_readability

    ctx = await make_context(fake=fake)
    try:
        return await check_readability(items, ctx)
    finally:
        await ctx.aclose()


def main(argv: list[str] | None = None) -> int:
    """Extract one file to items.json; exit code 1 with a readable message on failure."""
    ap = argparse.ArgumentParser(prog="python -m foreman.extract_files")
    ap.add_argument("file")
    ap.add_argument("-o", "--out", default="items.json")
    ap.add_argument("--check", action="store_true", help="run the readability check (needs keys)")
    ap.add_argument("--fake", action="store_true", help="use FakeJev/FakeLLM for --check")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    try:
        items = extract(args.file)
    except ExtractError as e:
        console.print(f"[red]Error:[/red] {e}")
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(items.model_dump_json(indent=2), encoding="utf-8")

    src = items.source
    console.print(
        f"[bold]{len(items.items)}[/bold] items from {src.get('filename')} ({src.get('kind')})"
    )
    if "text_column" in src:
        console.print(f"text column: {src['text_column'] or '(none; rows joined as col: value)'}")
    if src.get("sheet"):
        console.print(f"sheet: {src['sheet']}")
    if args.check:
        warnings = asyncio.run(_check(items, args.fake))
        for w in warnings:
            console.print(f"[yellow]warning:[/yellow] {w}")
        if not warnings:
            console.print("readability check: ok")
    console.print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
