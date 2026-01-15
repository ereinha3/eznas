"""FastAPI entrypoint for the NAS orchestrator prototype."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, Field, ConfigDict

from .converge.runner import ApplyRunner
from .converge.services import ServiceConfigurator
from .clients.jellyfin import JellyfinClient
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
CONFIG_ROOT = Path(os.getenv("ORCH_ROOT", ROOT_DIR))
FRONTEND_DIST_DIR = ROOT_DIR / "frontend" / "dist"
UI_FALLBACK_DIR = ROOT_DIR / "ui"
UI_DIR = FRONTEND_DIST_DIR if FRONTEND_DIST_DIR.exists() else UI_FALLBACK_DIR

app = FastAPI(title="NAS Orchestrator", version="0.1.0")
repo = ConfigRepository(CONFIG_ROOT)
renderer = ComposeRenderer(ROOT_DIR / "templates")
services = ServiceConfigurator(repo=repo)
runner = ApplyRunner(repo=repo, renderer=renderer, services=services)
app.mount(
    "/ui",
    StaticFiles(directory=UI_DIR, html=True),
    name="ui",
)
if (UI_DIR / "assets").exists():
    app.mount(
        "/assets",
        StaticFiles(directory=UI_DIR / "assets"),
        name="assets",
    )


class CredentialUser(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    username: str
    password: Optional[str] = Field(default=None, serialization_alias="password")


class ServiceCredential(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    service: str
    label: str
    username: Optional[str] = None
    password: Optional[str] = None
    editable: bool = False
    can_view_password: bool = Field(default=False, serialization_alias="canViewPassword")
    multi_user: bool = Field(default=False, serialization_alias="multiUser")
    supports_user_creation: bool = Field(default=False, serialization_alias="supportsUserCreation")
    users: List[CredentialUser] = Field(default_factory=list)


class ServiceCredentialsResponse(BaseModel):
    services: List[ServiceCredential]


class CredentialUpdate(BaseModel):
    username: str
    password: str


class JellyfinUserRequest(BaseModel):
    username: str
    password: str


def _build_service_credentials(config: StackConfig, state: dict) -> ServiceCredentialsResponse:
    services_credentials: List[ServiceCredential] = []
    secrets = state.get("secrets", {})
    services_state = state.get("services", {})

    qb_secret = secrets.get("qbittorrent", {})
    qb_cfg = config.services.qbittorrent
    qb_username = qb_secret.get("username", qb_cfg.username)
    qb_password = qb_secret.get("password", qb_cfg.password)
    services_credentials.append(
        ServiceCredential(
            service="qbittorrent",
            label="qBittorrent",
            username=qb_username,
            password=qb_password,
            editable=True,
            can_view_password=True,
        )
    )

    radarr_secret = secrets.get("radarr", {})
    if radarr_secret:
        services_credentials.append(
            ServiceCredential(
                service="radarr-ui",
                label="Radarr UI",
                username=radarr_secret.get("ui_username"),
                password=radarr_secret.get("ui_password"),
                can_view_password=radarr_secret.get("ui_password") is not None,
            )
        )

    radarr_state = services_state.get("radarr", {})
    if radarr_state:
        services_credentials.append(
            ServiceCredential(
                service="radarr",
                label="Radarr → qBittorrent",
                username=radarr_state.get("download_client_username"),
                password=radarr_state.get("download_client_password"),
                can_view_password=radarr_state.get("download_client_password") is not None,
            )
        )

    sonarr_secret = secrets.get("sonarr", {})
    if sonarr_secret:
        services_credentials.append(
            ServiceCredential(
                service="sonarr-ui",
                label="Sonarr UI",
                username=sonarr_secret.get("ui_username"),
                password=sonarr_secret.get("ui_password"),
                can_view_password=sonarr_secret.get("ui_password") is not None,
            )
        )

    sonarr_state = services_state.get("sonarr", {})
    if sonarr_state:
        services_credentials.append(
            ServiceCredential(
                service="sonarr",
                label="Sonarr → qBittorrent",
                username=sonarr_state.get("download_client_username"),
                password=sonarr_state.get("download_client_password"),
                can_view_password=sonarr_state.get("download_client_password") is not None,
            )
        )

    jellyfin_secret = secrets.get("jellyfin", {})
    jellyfin_users = jellyfin_secret.get("users", [])
    services_credentials.append(
        ServiceCredential(
            service="jellyfin",
            label="Jellyfin admin",
            username=jellyfin_secret.get("admin_username"),
            password=jellyfin_secret.get("admin_password"),
            can_view_password=True,
            multi_user=True,
            supports_user_creation=True,
            users=[
                CredentialUser(username=user.get("username"), password=user.get("password"))
                for user in jellyfin_users
            ],
        )
    )

    return ServiceCredentialsResponse(services=services_credentials)


@app.get("/", include_in_schema=False)
def serve_ui_root() -> FileResponse:
    """Return the static wizard UI."""
    return FileResponse(UI_DIR / "index.html")


@app.get("/vite.svg", include_in_schema=False)
def serve_vite_icon() -> FileResponse:
    """Return the Vite favicon if present in the bundled UI."""
    asset = UI_DIR / "vite.svg"
    if asset.exists():
        return FileResponse(asset)
    raise HTTPException(status_code=404, detail="vite.svg not found")


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

@app.get("/api/secrets", response_model=ServiceCredentialsResponse)
def get_service_credentials() -> ServiceCredentialsResponse:
    config = repo.load_stack()
    state = repo.load_state()
    return _build_service_credentials(config, state)


@app.post("/api/services/qbittorrent/credentials", response_model=ServiceCredential)
def update_qbittorrent_credentials(payload: CredentialUpdate) -> ServiceCredential:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")

    config = repo.load_stack()
    config.services.qbittorrent.username = username
    config.services.qbittorrent.password = payload.password
    repo.save_stack(config)

    state = repo.load_state()
    secrets = state.setdefault("secrets", {})
    qb_secret = secrets.setdefault("qbittorrent", {})
    qb_secret["username"] = username
    qb_secret["password"] = payload.password
    repo.save_state(state)

    credentials = _build_service_credentials(config, state).services
    for entry in credentials:
        if entry.service == "qbittorrent":
            return entry
    raise HTTPException(status_code=500, detail="Unable to load qBittorrent credentials")


@app.post("/api/services/jellyfin/users", response_model=CredentialUser)
def create_jellyfin_user(payload: JellyfinUserRequest) -> CredentialUser:
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")

    config = repo.load_stack()
    jellyfin_client = JellyfinClient(repo)
    try:
        created = jellyfin_client.create_user(config, username, payload.password)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - safeguard
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return CredentialUser(username=created["username"], password=created.get("password"))


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

