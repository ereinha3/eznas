"""Pydantic models representing user-facing NAS stack configuration."""

from __future__ import annotations

from datetime import datetime, timezone
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


class DownloadPolicy(BaseModel):
    categories: DownloadCategories = Field(default_factory=DownloadCategories)


class MediaPolicyEntry(BaseModel):
    keep_audio: List[str] = Field(default_factory=lambda: ["eng", "und"])
    keep_subs: List[str] = Field(default_factory=lambda: ["eng"])


class MediaPolicy(BaseModel):
    movies: MediaPolicyEntry = Field(default_factory=MediaPolicyEntry)


class QualityPreset(str, Enum):
    hd = "1080p"
    uhd = "4k"
    balanced = "balanced"


class ResolutionPreset(str, Enum):
    p720 = "720p"
    p1080 = "1080p"
    p1440 = "1440p"
    p2160 = "2160p"


class QualityConfig(BaseModel):
    preset: QualityPreset = QualityPreset.balanced
    target_resolution: Optional[ResolutionPreset] = None
    max_bitrate_mbps: Optional[int] = Field(default=None, ge=1)
    preferred_container: str = "mkv"


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
    port: int = Field(default=8081, ge=1, le=65535)
    proxy_url: Optional[str] = None
    stop_after_download: bool = True
    username: str = "admin"
    password: str = ""  # Set during setup wizard, never hardcode defaults


class RadarrConfig(ServiceBaseConfig):
    port: int = Field(default=7878, ge=1, le=65535)
    proxy_url: Optional[str] = None


class SonarrConfig(ServiceBaseConfig):
    port: int = Field(default=8989, ge=1, le=65535)
    proxy_url: Optional[str] = None


class ProwlarrConfig(ServiceBaseConfig):
    port: int = Field(default=9696, ge=1, le=65535)
    proxy_url: Optional[str] = None
    # When True, only add indexers matching user's language preferences
    # When False, add all public indexers with Movies/TV categories
    language_filter: bool = Field(default=True)


class JellyseerrConfig(ServiceBaseConfig):
    port: int = Field(default=5055, ge=1, le=65535)
    proxy_url: Optional[str] = None


class JellyfinConfig(ServiceBaseConfig):
    port: int = Field(default=8096, ge=1, le=65535)
    proxy_url: Optional[str] = None


class BazarrConfig(ServiceBaseConfig):
    port: int = Field(default=6767, ge=1, le=65535)
    proxy_url: Optional[str] = None


class FlareSolverrConfig(ServiceBaseConfig):
    """Headless browser proxy that solves CloudFlare challenges for Prowlarr."""
    enabled: bool = False
    port: int = Field(default=8191, ge=1, le=65535)
    proxy_url: Optional[str] = None


class BackfillConfig(BaseModel):
    """Configuration for automatic backfill and download health monitoring."""

    enabled: bool = False

    # --- Backfill timing ---
    interval_minutes: int = Field(default=360, ge=30)
    missing_days_threshold: int = Field(default=1, ge=0)

    # --- Stall detection (runs every tick, not on backfill interval) ---
    stall_detection_enabled: bool = True
    stall_threshold_minutes: int = Field(default=30, ge=5)
    stall_check_interval_minutes: int = Field(default=5, ge=1)
    max_stall_retries: int = Field(default=3, ge=1)

    # --- Scoring ---
    min_seeders: int = Field(default=3, ge=1)
    max_grabs_per_cycle: int = Field(default=3, ge=1)
    max_size_gb: float = Field(default=80.0, ge=1.0)

    # --- Season pack preference for Sonarr re-search ---
    prefer_season_packs: bool = True

    # --- Prowlarr direct-grab fallback ---
    prowlarr_fallback_enabled: bool = True
    prowlarr_fallback_interval_hours: int = Field(default=6, ge=1)
    prowlarr_fallback_min_age_hours: int = Field(default=6, ge=1)


class PipelineConfig(ServiceBaseConfig):
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    proxy_url: Optional[str] = None
    backfill: BackfillConfig = Field(default_factory=BackfillConfig)


# Services that always route through Gluetun when it is enabled.
VPN_ROUTED_SERVICES = frozenset({"qbittorrent", "radarr", "sonarr", "prowlarr", "flaresolverr"})


class GluetunConfig(ServiceBaseConfig):
    """VPN gateway container (Gluetun) for routing torrent traffic through WireGuard."""

    enabled: bool = False
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    proxy_url: Optional[str] = None
    wireguard_config: str = ""


class TraefikConfig(BaseModel):
    enabled: bool = False
    image: str = "traefik:v3.1"
    http_port: int = Field(default=80, ge=1, le=65535)
    https_port: Optional[int] = Field(default=None, ge=1, le=65535)
    dashboard: bool = False
    additional_args: List[str] = Field(default_factory=list)


class ServicesConfig(BaseModel):
    qbittorrent: QbittorrentConfig = Field(default_factory=QbittorrentConfig)
    radarr: RadarrConfig = Field(default_factory=RadarrConfig)
    sonarr: SonarrConfig = Field(default_factory=SonarrConfig)
    prowlarr: ProwlarrConfig = Field(default_factory=ProwlarrConfig)
    jellyseerr: JellyseerrConfig = Field(default_factory=JellyseerrConfig)
    jellyfin: JellyfinConfig = Field(default_factory=JellyfinConfig)
    bazarr: BazarrConfig = Field(default_factory=BazarrConfig)
    flaresolverr: FlareSolverrConfig = Field(default_factory=FlareSolverrConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    gluetun: GluetunConfig = Field(default_factory=GluetunConfig)


class UserEntry(BaseModel):
    username: str
    email: Optional[str] = None
    role: str = "viewer"


class StackConfig(BaseModel):
    version: int = 1
    paths: PathConfig
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    proxy: TraefikConfig = Field(default_factory=TraefikConfig)
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


class HealthCheck(BaseModel):
    """Health status of a single service."""

    name: str
    healthy: bool
    port: Optional[int] = None
    message: Optional[str] = None


class HealthResponse(BaseModel):
    """Wrapper returned from ``GET /api/health`` for container readiness checks."""

    status: Literal["healthy", "degraded", "unhealthy"]
    services: List[HealthCheck] = Field(default_factory=list)


class IndexerSchema(BaseModel):
    """Schema for an available indexer in Prowlarr."""

    id: int
    name: str
    description: Optional[str] = None
    encoding: Optional[str] = None
    language: Optional[str] = None
    privacy: str  # "public", "private", "semiPrivate"
    protocol: str  # "torrent", "usenet"
    categories: List[Dict] = Field(default_factory=list)
    supports_rss: bool = Field(default=False, alias="supportsRss")
    supports_search: bool = Field(default=False, alias="supportsSearch")

    class Config:
        populate_by_name = True


class IndexerInfo(BaseModel):
    """Information about a configured indexer."""

    id: int
    name: str
    implementation: str
    enable: bool = True
    priority: int = 25
    protocol: str = "torrent"


class AvailableIndexersResponse(BaseModel):
    """Response containing available public indexers."""

    indexers: List[IndexerSchema] = Field(default_factory=list)


class ConfiguredIndexersResponse(BaseModel):
    """Response containing currently configured indexers."""

    indexers: List[IndexerInfo] = Field(default_factory=list)


class AddIndexersRequest(BaseModel):
    """Request to add indexers by their definition names."""

    indexers: List[str]  # List of indexer definition names (e.g., "1337x", "EZTV")


# Authentication Models


class UserRole(str, Enum):
    """User roles for access control."""

    ADMIN = "admin"
    VIEWER = "viewer"


class User(BaseModel):
    """User account for authentication."""

    username: str
    password_hash: str
    role: UserRole = UserRole.ADMIN
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class Session(BaseModel):
    """Active user session."""

    token: str
    username: str
    role: UserRole
    created_at: datetime
    expires_at: datetime
    sudo_expires_at: Optional[datetime] = None

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class AuthConfig(BaseModel):
    """Authentication configuration."""

    version: int = 1
    session_timeout_hours: int = 24
    sudo_timeout_minutes: int = 10


class LoginRequest(BaseModel):
    """Request to authenticate."""

    username: str
    password: str


class LoginResponse(BaseModel):
    """Response after successful authentication."""

    success: bool
    token: Optional[str] = None
    username: Optional[str] = None
    role: Optional[UserRole] = None
    expires_at: Optional[datetime] = None
    message: Optional[str] = None

    class Config:
        json_encoders = {datetime: lambda v: v.isoformat()}


class SessionResponse(BaseModel):
    """Response with session info."""

    valid: bool
    username: Optional[str] = None
    role: Optional[UserRole] = None
    sudo_active: bool = False


class SudoVerifyRequest(BaseModel):
    """Request to verify password for sudo mode."""

    password: str


class SudoVerifyResponse(BaseModel):
    """Response after sudo verification."""

    success: bool
    message: str


class ChangePasswordRequest(BaseModel):
    """Request to change password."""

    current_password: str
    new_password: str


class CreateUserRequest(BaseModel):
    """Request to create a new user."""

    username: str
    password: str
    role: UserRole = UserRole.VIEWER


class UserListResponse(BaseModel):
    """Response with list of users."""

    users: List[dict] = Field(default_factory=list)


class VolumeInfo(BaseModel):
    """Information about a mounted volume."""

    device: str
    mountpoint: str
    size: str
    available: str
    filesystem: str
    suggested_paths: Dict[str, str]


class VolumesResponse(BaseModel):
    """Response containing available volumes."""

    volumes: List[VolumeInfo] = Field(default_factory=list)


class InitializeRequest(BaseModel):
    """Request to initialize the system for first-run."""

    admin_username: str
    admin_password: str
    pool_path: str
    scratch_path: Optional[str] = None
    appdata_path: str
    enabled_services: Optional[List[str]] = None  # e.g. ["qbittorrent", "radarr"]


class InitializeResponse(BaseModel):
    """Response from initialization."""

    success: bool
    message: str
    config_created: bool = False


# Library Sweep Models


class SweepActionDetail(BaseModel):
    """Detail about a single file that needs sweeping."""

    path: str
    size: int
    category: str
    unwanted_audio: List[str]
    unwanted_subtitles: List[str]


class SweepScanResponse(BaseModel):
    """Result of a dry-run library sweep scan."""

    total_files_scanned: int
    files_already_clean: int
    files_to_process: int
    total_bytes_to_process: int
    estimated_time_seconds: float
    actions: List[SweepActionDetail] = Field(default_factory=list)


class SweepStartResponse(BaseModel):
    """Acknowledgement that a sweep has been started."""

    sweep_id: str
    total_files: int


class SweepStatusResponse(BaseModel):
    """Current state of the sweep operation."""

    status: str  # "idle", "scanning", "running", "completed", "failed"
    sweep_id: Optional[str] = None
    progress_current: int = 0
    progress_total: int = 0
    current_file: Optional[str] = None
    succeeded: int = 0
    failed: int = 0
    errors: List[str] = Field(default_factory=list)
