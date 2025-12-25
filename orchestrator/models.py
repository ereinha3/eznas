"""Pydantic models representing user-facing NAS stack configuration."""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, validator


class PathConfig(BaseModel):
    pool: Path
    scratch: Optional[Path] = None
    appdata: Path

    @validator("pool", "appdata")
    def ensure_absolute(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("Paths must be absolute")
        return value

    @validator("scratch")
    def ensure_absolute_optional(cls, value: Optional[Path]) -> Optional[Path]:
        if value is not None and not value.is_absolute():
            raise ValueError("Paths must be absolute")
        return value


class DownloadCategories(BaseModel):
    radarr: str = "movies"
    sonarr: str = "tv"
    anime: str = "anime"


class DownloadPolicy(BaseModel):
    categories: DownloadCategories = Field(default_factory=DownloadCategories)


class MediaPolicyEntry(BaseModel):
    keep_audio: List[str] = Field(default_factory=lambda: ["eng", "und"])
    keep_subs: List[str] = Field(default_factory=lambda: ["eng"])


class MediaPolicy(BaseModel):
    movies: MediaPolicyEntry = Field(default_factory=MediaPolicyEntry)
    anime: MediaPolicyEntry = Field(
        default_factory=lambda: MediaPolicyEntry(
            keep_audio=["jpn", "eng", "und"],
            keep_subs=["eng"],
        )
    )


class QualityPreset(str, Enum):
    hd = "1080p"
    uhd = "4k"
    balanced = "balanced"


class QualityConfig(BaseModel):
    preset: QualityPreset = QualityPreset.balanced


class UIConfig(BaseModel):
    port: int = Field(default=8443, ge=1, le=65535)


class RuntimeConfig(BaseModel):
    user_id: int = Field(default=1000, ge=0)
    group_id: int = Field(default=1000, ge=0)
    timezone: str = "UTC"


class ServiceBaseConfig(BaseModel):
    enabled: bool = True
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    proxy_url: Optional[str] = None


class QbittorrentConfig(ServiceBaseConfig):
    port: int = Field(default=8080, ge=1, le=65535)
    proxy_url: Optional[str] = None
    stop_after_download: bool = True
    username: str = "admin"
    password: str = "adminadmin"


class RadarrConfig(ServiceBaseConfig):
    port: int = Field(default=7878, ge=1, le=65535)
    proxy_url: Optional[str] = None


class SonarrConfig(ServiceBaseConfig):
    port: int = Field(default=8989, ge=1, le=65535)
    proxy_url: Optional[str] = None


class ProwlarrConfig(ServiceBaseConfig):
    port: int = Field(default=9696, ge=1, le=65535)
    proxy_url: Optional[str] = None


class JellyseerrConfig(ServiceBaseConfig):
    port: int = Field(default=5055, ge=1, le=65535)
    proxy_url: Optional[str] = None


class JellyfinConfig(ServiceBaseConfig):
    port: int = Field(default=8096, ge=1, le=65535)
    proxy_url: Optional[str] = None


class PipelineConfig(ServiceBaseConfig):
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    proxy_url: Optional[str] = None


class ServicesConfig(BaseModel):
    qbittorrent: QbittorrentConfig = Field(default_factory=QbittorrentConfig)
    radarr: RadarrConfig = Field(default_factory=RadarrConfig)
    sonarr: SonarrConfig = Field(default_factory=SonarrConfig)
    prowlarr: ProwlarrConfig = Field(default_factory=ProwlarrConfig)
    jellyseerr: JellyseerrConfig = Field(default_factory=JellyseerrConfig)
    jellyfin: JellyfinConfig = Field(default_factory=JellyfinConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)


class UserEntry(BaseModel):
    username: str
    email: Optional[str] = None
    role: str = "viewer"


class StackConfig(BaseModel):
    version: int = 1
    paths: PathConfig
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    download_policy: DownloadPolicy = Field(default_factory=DownloadPolicy)
    media_policy: MediaPolicy = Field(default_factory=MediaPolicy)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    users: List[UserEntry] = Field(default_factory=list)

    @validator("users", each_item=True)
    def validate_user_role(cls, value: UserEntry) -> UserEntry:
        if value.role not in {"owner", "admin", "editor", "viewer"}:
            raise ValueError("Unsupported role")
        return value


class ValidationResult(BaseModel):
    ok: bool
    checks: Dict[str, str]


class RenderResult(BaseModel):
    compose_path: Path
    env_path: Path
    secrets_dir: Optional[Path] = None
    secret_files: Dict[str, Path] = Field(default_factory=dict)


class StageEvent(BaseModel):
    stage: str
    status: Literal["started", "ok", "failed"]
    detail: Optional[str] = None


class ApplyResponse(BaseModel):
    ok: bool
    run_id: str
    events: List[StageEvent]


class RunRecord(BaseModel):
    run_id: str
    ok: Optional[bool] = None
    events: List[StageEvent] = Field(default_factory=list)
    summary: Optional[str] = None


class ServiceStatus(BaseModel):
    """Represents the reported status of a managed service for the UI."""

    name: str
    status: Literal["up", "down", "unknown"] = "unknown"
    message: Optional[str] = None


class StatusResponse(BaseModel):
    """Wrapper returned from ``GET /api/status`` with the state of services."""

    services: List[ServiceStatus] = Field(default_factory=list)

