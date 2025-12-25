"""FastAPI entrypoint for the NAS orchestrator prototype."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from .converge.runner import ApplyRunner
from .converge.services import ServiceConfigurator
from .models import (
    ApplyResponse,
    RenderResult,
    StackConfig,
    StatusResponse,
    ServiceStatus,
    ValidationResult,
)
from .rendering import ComposeRenderer
from .storage import ConfigRepository
from .validators import run_validation

ROOT_DIR = Path(__file__).resolve().parents[1]

app = FastAPI(title="NAS Orchestrator", version="0.1.0")
repo = ConfigRepository(ROOT_DIR)
renderer = ComposeRenderer(ROOT_DIR / "templates")
services = ServiceConfigurator(repo=repo)
runner = ApplyRunner(repo=repo, renderer=renderer, services=services)
app.mount(
    "/ui",
    StaticFiles(directory=ROOT_DIR / "ui", html=True),
    name="ui",
)


@app.get("/", include_in_schema=False)
def serve_ui_root() -> FileResponse:
    """Return the static wizard UI."""
    return FileResponse(ROOT_DIR / "ui" / "index.html")


@app.get("/api/config", response_model=StackConfig)
def get_config() -> StackConfig:
    """Return the saved stack configuration."""
    try:
        return repo.load_stack()
    except FileNotFoundError as exc:  # pragma: no cover - initial bootstrap
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put("/api/config", response_model=StackConfig)
def update_config(config: StackConfig) -> StackConfig:
    """Persist an updated configuration to stack.yaml."""
    repo.save_stack(config)
    return config


@app.post("/api/validate", response_model=ValidationResult)
def validate_config(config: StackConfig) -> ValidationResult:
    """Run lightweight sanity checks on config paths."""
    return run_validation(config)




@app.get("/api/status", response_model=StatusResponse)
def get_status() -> StatusResponse:
    """Return a placeholder status summary for the managed services."""
    cfg = repo.load_stack()
    services = []
    for name, settings in cfg.services.model_dump(mode="python").items():
        enabled = settings.get("enabled", True)
        services.append(
            ServiceStatus(
                name=name,
                status="unknown" if enabled else "down",
                message=None if enabled else "service disabled in configuration",
            )
        )
    return StatusResponse(services=services)

@app.post("/api/render", response_model=RenderResult)
def render_compose(config: StackConfig) -> RenderResult:
    """Render docker-compose and env files to the generated directory."""
    try:
        result = renderer.render(config, repo.generated_dir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return result


@app.post("/api/apply", response_model=ApplyResponse)
def apply_stack(config: StackConfig) -> ApplyResponse:
    """Run the converge engine steps for the supplied configuration."""
    run_id = str(uuid4())
    ok, events = runner.run(run_id, config)
    return ApplyResponse(ok=ok, run_id=run_id, events=events)


@app.get("/api/runs/{run_id}/events")
async def stream_run_events(run_id: str) -> EventSourceResponse:
    """Stream converge events for a given run identifier."""

    async def event_generator():
        sent = 0
        while True:
            record = repo.get_run(run_id)
            if record is None:
                yield {
                    "event": "error",
                    "data": json.dumps({"message": "run_not_found"}),
                }
                return

            while sent < len(record.events):
                event = record.events[sent]
                sent += 1
                yield {
                    "event": "stage",
                    "data": event.model_dump_json(),
                }

            if record.ok is not None:
                yield {
                    "event": "status",
                    "data": json.dumps(
                        {"ok": record.ok, "summary": record.summary or ""}
                    ),
                }
                return

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())

