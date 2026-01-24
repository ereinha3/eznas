"""FastAPI entrypoint for the NAS orchestrator prototype."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import traceback
from pathlib import Path
from uuid import uuid4
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel, Field, ConfigDict

from .converge.runner import ApplyRunner
from .converge.services import ServiceConfigurator
from .clients.jellyfin import JellyfinClient
from .models import (
    AddIndexersRequest,
    ApplyResponse,
    AvailableIndexersResponse,
    ConfiguredIndexersResponse,
    HealthCheck,
    HealthResponse,
    IndexerInfo,
    IndexerSchema,
    RenderResult,
    StackConfig,
    StatusResponse,
    ServiceStatus,
    ValidationResult,
)
from .clients.prowlarr import ProwlarrClient
from .rendering import ComposeRenderer
from .storage import ConfigRepository
from .validators import run_validation

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_ROOT = Path(os.getenv("ORCH_ROOT", ROOT_DIR))
FRONTEND_DIST_DIR = ROOT_DIR / "frontend" / "dist"
UI_FALLBACK_DIR = ROOT_DIR / "ui"
UI_DIR = FRONTEND_DIST_DIR if FRONTEND_DIST_DIR.exists() else UI_FALLBACK_DIR

app = FastAPI(title="NAS Orchestrator", version="0.1.0")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global error handler to prevent crashes and return useful error info."""
    error_id = str(uuid4())[:8]
    logger.error(f"Unhandled error [{error_id}]: {exc}\n{traceback.format_exc()}")

    # Don't expose internal details in production, but be helpful in dev
    detail = str(exc) if os.getenv("DEBUG", "").lower() in ("1", "true") else "Internal server error"

    return JSONResponse(
        status_code=500,
        content={
            "detail": detail,
            "error_id": error_id,
            "hint": "Check server logs for more details",
        },
    )


repo = ConfigRepository(CONFIG_ROOT)
renderer = ComposeRenderer(ROOT_DIR / "templates")
services = ServiceConfigurator(repo=repo)
runner = ApplyRunner(repo=repo, renderer=renderer, services=services)

# Only mount static files if the directory exists (not in dev mode with separate frontend)
if UI_DIR.exists():
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

    # Prowlarr UI credentials
    prowlarr_secret = secrets.get("prowlarr", {})
    if prowlarr_secret.get("ui_username"):
        services_credentials.append(
            ServiceCredential(
                service="prowlarr-ui",
                label="Prowlarr UI",
                username=prowlarr_secret.get("ui_username"),
                password=prowlarr_secret.get("ui_password"),
                can_view_password=prowlarr_secret.get("ui_password") is not None,
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

    # Jellyseerr credentials (uses same admin as Jellyfin for auth)
    jellyseerr_secret = secrets.get("jellyseerr", {})
    if jellyseerr_secret.get("admin_username"):
        services_credentials.append(
            ServiceCredential(
                service="jellyseerr",
                label="Jellyseerr (via Jellyfin)",
                username=jellyseerr_secret.get("admin_username"),
                password=jellyseerr_secret.get("admin_password"),
                can_view_password=True,
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


def _check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# Default internal ports for services (used for container-to-container health checks)
_SERVICE_INTERNAL_PORTS: dict[str, int] = {
    "qbittorrent": 8080,
    "radarr": 7878,
    "sonarr": 8989,
    "prowlarr": 9696,
    "jellyseerr": 5055,
    "jellyfin": 8096,
}


@app.get("/api/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Return health status with actual port connectivity checks."""
    cfg = repo.load_stack()
    checks: List[HealthCheck] = []
    healthy_count = 0
    total_enabled = 0

    for name, settings in cfg.services.model_dump(mode="python").items():
        enabled = settings.get("enabled", True)
        port = settings.get("port")

        if not enabled:
            checks.append(HealthCheck(
                name=name,
                healthy=False,
                port=port,
                message="disabled",
            ))
            continue

        total_enabled += 1

        if not port:
            checks.append(HealthCheck(
                name=name,
                healthy=True,
                port=None,
                message="no port configured",
            ))
            healthy_count += 1
            continue

        # Use container name as host and internal port for container-to-container checks
        # Fall back to localhost if not in Docker or service unknown
        # When orchestrator is in a container, try host.docker.internal (Docker Desktop)
        # or gateway IP (Linux) to reach services on host ports
        internal_port = _SERVICE_INTERNAL_PORTS.get(name, port)
        is_healthy = (
            _check_port(name, internal_port) 
            or _check_port("127.0.0.1", port)
            or _check_port("host.docker.internal", port)  # Docker Desktop
        )
        # If still not healthy and we're in Docker, try to get host gateway
        if not is_healthy:
            try:
                import pathlib
                if pathlib.Path("/.dockerenv").exists():
                    # Try common gateway IPs for Docker bridge networks
                    import subprocess
                    result = subprocess.run(
                        ["ip", "route", "show", "default"],
                        capture_output=True,
                        text=True,
                        timeout=1.0
                    )
                    if result.returncode == 0:
                        for line in result.stdout.splitlines():
                            if "default via" in line:
                                parts = line.split()
                                if len(parts) >= 3:
                                    gateway_ip = parts[2]
                                    if _check_port(gateway_ip, port):
                                        is_healthy = True
                                        break
            except Exception:
                pass  # Fall back to previous result
        checks.append(HealthCheck(
            name=name,
            healthy=is_healthy,
            port=port,
            message="responding" if is_healthy else "not responding",
        ))
        if is_healthy:
            healthy_count += 1

    if total_enabled == 0:
        overall = "unhealthy"
    elif healthy_count == total_enabled:
        overall = "healthy"
    elif healthy_count > 0:
        overall = "degraded"
    else:
        overall = "unhealthy"

    return HealthResponse(status=overall, services=checks)


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


# Indexer management endpoints
prowlarr_client = ProwlarrClient(repo)


@app.get("/api/indexers/available", response_model=AvailableIndexersResponse)
def get_available_indexers() -> AvailableIndexersResponse:
    """Get list of available public indexers that can be added."""
    try:
        config = repo.load_stack()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Stack configuration not found")

    if not config.services.prowlarr.enabled:
        raise HTTPException(status_code=400, detail="Prowlarr is not enabled")

    indexers = prowlarr_client.get_available_indexers(config)
    return AvailableIndexersResponse(indexers=indexers)


@app.get("/api/indexers", response_model=ConfiguredIndexersResponse)
def get_configured_indexers() -> ConfiguredIndexersResponse:
    """Get list of currently configured indexers in Prowlarr."""
    try:
        config = repo.load_stack()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Stack configuration not found")

    if not config.services.prowlarr.enabled:
        raise HTTPException(status_code=400, detail="Prowlarr is not enabled")

    indexers = prowlarr_client.get_configured_indexers(config)
    return ConfiguredIndexersResponse(indexers=indexers)


class AddIndexersResponse(BaseModel):
    added: List[str]
    failed: List[str]


@app.post("/api/indexers", response_model=AddIndexersResponse)
def add_indexers(payload: AddIndexersRequest) -> AddIndexersResponse:
    """Add indexers by their definition names."""
    try:
        config = repo.load_stack()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Stack configuration not found")

    if not config.services.prowlarr.enabled:
        raise HTTPException(status_code=400, detail="Prowlarr is not enabled")

    if not payload.indexers:
        raise HTTPException(status_code=400, detail="No indexers specified")

    added, failed = prowlarr_client.add_indexers(config, payload.indexers)
    return AddIndexersResponse(added=added, failed=failed)


@app.delete("/api/indexers/{indexer_id}")
def remove_indexer(indexer_id: int):
    """Remove an indexer by ID."""
    try:
        config = repo.load_stack()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Stack configuration not found")

    if not config.services.prowlarr.enabled:
        raise HTTPException(status_code=400, detail="Prowlarr is not enabled")

    success = prowlarr_client.remove_indexer(config, indexer_id)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to remove indexer")

    return {"ok": True}


class AutoPopulateIndexersResponse(BaseModel):
    """Response from auto-populating indexers."""
    added: List[str]
    skipped: List[str]
    failed: List[str]
    message: str


@app.post("/api/indexers/auto-populate", response_model=AutoPopulateIndexersResponse)
def auto_populate_indexers() -> AutoPopulateIndexersResponse:
    """Auto-populate indexers based on user language preferences.

    This endpoint will:
    - Find all public indexers that support Movies (2000) and/or TV (5000) categories
    - Filter by the user's language preferences from media_policy
    - Add all matching indexers that aren't already configured
    """
    try:
        config = repo.load_stack()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Stack configuration not found")

    if not config.services.prowlarr.enabled:
        raise HTTPException(status_code=400, detail="Prowlarr is not enabled")

    added, skipped, failed = prowlarr_client.auto_populate_indexers(config)

    # Build summary message
    parts = []
    if added:
        parts.append(f"Added {len(added)} indexer{'s' if len(added) != 1 else ''}")
    if skipped:
        parts.append(f"{len(skipped)} already configured")
    if failed:
        parts.append(f"{len(failed)} failed")

    message = "; ".join(parts) if parts else "No matching indexers found"

    return AutoPopulateIndexersResponse(
        added=added,
        skipped=skipped,
        failed=failed,
        message=message,
    )


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

