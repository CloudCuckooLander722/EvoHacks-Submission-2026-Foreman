"""Foreman's HTTP API (MINH_PLAN §D): runs, their SSE event stream, results and exports.

    uvicorn foreman.api.app:app --reload --port 8000

A run is an asyncio task on this process (see `foreman.api.runs`); the server never
crashes because of one, and at most `MAX_ACTIVE_RUNS` run at once. Tests swap the live
clients for fakes by overriding the `get_context_factory` dependency.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from sse_starlette import EventSourceResponse

from foreman.api.extract import load_optional
from foreman.api.runs import (
    ContextFactory,
    RunRecord,
    RunRegistry,
    load_saved_plan,
    recorded_events,
    run_job,
)
from foreman.api.settings import ApiSettings, get_api_settings
from foreman.config import Settings, get_settings
from foreman.contracts import ResultsFile
from foreman.core.context import make_context, new_run_id
from foreman.graph.executor import run_dir

log = logging.getLogger(__name__)

NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")
EXPORT_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def get_context_factory() -> ContextFactory:
    """How a run gets its clients. Live by default; tests override this with fakes."""
    return make_context


def warm_llm_sdk(settings: Settings) -> None:
    """Import `openai` and load the SDK's lazy modules once, so no request pays for it.

    Building an `LLMClient` imports the package (15-40 s cold) and touches
    `chat.completions`; later clients reuse the loaded modules.
    """
    from foreman.core.llm import LLMClient

    LLMClient(settings)


def _check_id(run_id: str) -> str:
    if not NAME.fullmatch(run_id):
        raise HTTPException(404, detail={"status": "unknown", "message": "unknown run"})
    return run_id


def _results_path(run_id: str) -> Path:
    return run_dir(run_id) / "results.json"


def _ready_results(runs: RunRegistry, run_id: str) -> Path:
    """results.json once the run is done; 404 with the status while it isn't."""
    rec = runs.get(_check_id(run_id))
    if rec is not None and rec.status != "done":
        message = rec.error if rec.status == "failed" else f"run is still {rec.status}"
        raise HTTPException(404, detail={"status": rec.status, "message": message})
    path = _results_path(run_id)
    if not path.exists():
        raise HTTPException(404, detail={"status": "unknown", "message": "unknown run"})
    return path


async def _save_upload(file: UploadFile, dest: Path, max_bytes: int) -> Path:
    name = Path(file.filename or "upload").name or "upload"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / name
    size = 0
    with path.open("wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > max_bytes:
                out.close()
                shutil.rmtree(dest.parent, ignore_errors=True)
                raise HTTPException(413, detail=f"file is larger than {max_bytes >> 20} MB")
            out.write(chunk)
    return path


def create_app(settings: ApiSettings | None = None, *, warm: bool = True) -> FastAPI:
    """Build the app. `warm=False` skips loading the openai SDK at startup (tests)."""
    api = settings or get_api_settings()
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=get_settings().log_level.upper(), format="%(levelname)s %(name)s: %(message)s"
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        core = get_settings()
        if warm:
            log.info("loading the openai SDK once at startup")
            await asyncio.to_thread(warm_llm_sdk, core)
        log.info(
            "Foreman API ready: max %d active runs, %d items per run, origins %s",
            api.max_active_runs,
            api.max_items,
            api.cors_origins,
        )
        yield
        await app.state.runs.shutdown()

    app = FastAPI(title="Foreman", version="0.1.0", lifespan=lifespan)
    app.state.runs = RunRegistry()
    app.state.api_settings = api
    app.add_middleware(
        CORSMiddleware,
        allow_origins=api.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )

    def runs_of(request: Request) -> RunRegistry:
        return request.app.state.runs

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        """Whether keys are configured (never their values) and how busy the server is."""
        core = get_settings()
        return {
            "ok": True,
            "keys": {
                "jev": core.ai_gateway_api_key is not None,
                "llm": core.fireworks_api_key is not None,
            },
            "active_runs": runs_of(request).active(),
            "max_active_runs": api.max_active_runs,
            "max_items": api.max_items,
        }

    @app.post("/runs")
    async def create_run(
        request: Request,
        file: Annotated[UploadFile, File()],
        make_ctx: Annotated[ContextFactory, Depends(get_context_factory)],
        job: Annotated[str, Form()] = "",
        threshold: Annotated[float | None, Form()] = None,
        plan: Annotated[str | None, Form()] = None,
    ) -> dict[str, str]:
        """Save the upload, start the run in the background and return its id at once."""
        runs = runs_of(request)
        if runs.active() >= api.max_active_runs:
            raise HTTPException(
                429,
                detail=f"{runs.active()} runs are already in progress (the limit is "
                f"{api.max_active_runs}). Try again when one finishes.",
            )
        if threshold is not None and not 0.0 <= threshold <= 1.0:
            raise HTTPException(400, detail="threshold must be between 0 and 1")
        plan = (plan or "").strip() or None
        if plan is not None:
            available = sorted(p.stem for p in api.plans_dir.glob("*.json"))
            if not NAME.fullmatch(plan) or plan not in available:
                raise HTTPException(
                    400, detail=f"unknown plan {plan!r}; saved plans: {', '.join(available)}"
                )
            load_saved_plan(api.plans_dir, plan)  # a broken plan file fails here, not later
        if plan is None and not job.strip():
            raise HTTPException(400, detail="job is required unless `plan` names a saved plan")

        run_id = new_run_id()
        path = await _save_upload(
            file, run_dir(run_id) / "input", int(api.max_upload_mb * (1 << 20))
        )
        rec = RunRecord(
            run_id=run_id,
            job=job.strip(),
            threshold=threshold,
            plan_name=plan,
            input_path=path,
            max_items=api.max_items,
        )
        runs.add(rec)
        rec.task = asyncio.create_task(run_job(rec, make_ctx, api.plans_dir))
        log.info("run %s started: %s (%s)", run_id, path.name, plan or "planner")
        return {"run_id": run_id}

    @app.get("/runs/{run_id}/events")
    async def events(request: Request, run_id: str) -> EventSourceResponse:
        """SSE: plan, node, decision, stats, done, error. Late clients get a replay first."""
        rec = runs_of(request).get(_check_id(run_id))
        if rec is not None:
            source = rec.stream()
        elif (recorded := recorded_events(run_id)) is not None:

            async def replay() -> AsyncIterator[tuple[str, str]]:
                for ev in recorded:
                    yield ev

            source = replay()
        else:
            raise HTTPException(404, detail={"status": "unknown", "message": "unknown run"})

        async def frames() -> AsyncIterator[dict[str, str]]:
            async for name, data in source:
                yield {"event": name, "data": data}

        return EventSourceResponse(frames(), ping=15)

    @app.get("/runs/{run_id}/results")
    def results(request: Request, run_id: str) -> FileResponse:
        """results.json; 404 with `{"status": ...}` in `detail` while the run isn't done."""
        path = _ready_results(runs_of(request), run_id)
        return FileResponse(path, media_type="application/json")

    @app.get("/runs/{run_id}/decisions")
    def decisions(request: Request, run_id: str, item_id: str | None = None) -> JSONResponse:
        """Ledger rows (all, or one item's) for the "why" panel; live runs read memory."""
        rec = runs_of(request).get(_check_id(run_id))
        if rec is not None and rec.ctx is not None and not rec.finished:
            rows = [
                d.model_dump(mode="json")
                for d in list(rec.ctx.ledger.decisions)
                if item_id is None or d.item_id == item_id
            ]
            return JSONResponse(rows)
        path = run_dir(run_id) / "decisions.jsonl"
        if not path.exists():
            status = rec.status if rec is not None else "unknown"
            raise HTTPException(404, detail={"status": status, "message": "no decisions yet"})
        needle = None if item_id is None else f'"item_id":{json.dumps(item_id)}'
        with path.open(encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if needle is None or needle in line]
        return JSONResponse(rows)

    @app.get("/runs/{run_id}/export")
    async def export(
        request: Request,
        run_id: str,
        fmt: Annotated[Literal["xlsx", "pdf", "docx"], Query(alias="format")],
    ) -> Response:
        """The run as xlsx/pdf/docx via Minh's `foreman.format.export`; 501 until it exists."""
        export_fn = load_optional("foreman.format", "export")
        if export_fn is None:
            raise HTTPException(501, detail="export not built yet")
        path = _ready_results(runs_of(request), run_id)
        results = ResultsFile.model_validate_json(path.read_text(encoding="utf-8"))
        summary = results.aggregates.get("summary")
        try:
            if inspect.iscoroutinefunction(export_fn):
                data = await export_fn(results, fmt, summary=summary)
            else:
                data = await asyncio.to_thread(export_fn, results, fmt, summary=summary)
        except NotImplementedError:
            raise HTTPException(501, detail=f"{fmt} export not built yet") from None
        except Exception as e:
            log.exception("export %s for %s failed", fmt, run_id)
            raise HTTPException(500, detail=f"export failed: {type(e).__name__}: {e}") from e
        return Response(
            content=bytes(data),
            media_type=EXPORT_TYPES[fmt],
            headers={"Content-Disposition": f'attachment; filename="foreman-{run_id}.{fmt}"'},
        )

    return app


app = create_app()
