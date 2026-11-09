"""Pydantic models representing user-facing NAS stack configuration."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, validator

from .pipeline.remux import SUBTITLE_PREFER_TOKENS


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


class SubtitleRules(BaseModel):
    """How many subtitle tracks to keep per language in ``keep_subs``.

    Per language: up to ``max_per_language`` normal tracks ranked by
    ``prefer``, plus one forced track if ``keep_forced`` and one SDH track
    if ``keep_sdh``.  Exact duplicates (same language + codec + kind)
    always collapse to one.  The defaults match the original policy.
    """

    max_per_language: int = Field(default=1, ge=1)
    # Ordered codec classes ("text", "image") or codec names/aliases
    # ("srt", "ass", "pgs", "vobsub", ...).  Earlier entries win.
    prefer: List[str] = Field(default_factory=lambda: ["text", "image"])
    keep_forced: bool = True
    keep_sdh: bool = True

    @validator("prefer")
    def validate_prefer(cls, value: List[str]) -> List[str]:
        cleaned = [v.strip().lower() for v in value if v and v.strip()]
        unknown = [v for v in cleaned if v not in SUBTITLE_PREFER_TOKENS]
        if unknown:
            raise ValueError(
                f"unknown subtitle prefer entries {unknown}; "
                f"use one of {sorted(SUBTITLE_PREFER_TOKENS)}"
            )
        return cleaned


class MediaPolicyEntry(BaseModel):
    keep_audio: List[str] = Field(default_factory=lambda: ["eng", "und"])
    # Subtitle languages to keep.  The pseudo-language "forced" keeps
    # forced tracks of any language.
    keep_subs: List[str] = Field(default_factory=lambda: ["eng"])
    subtitles: SubtitleRules = Field(default_factory=SubtitleRules)


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


class ResolutionPreset(str, Enum):
    p720 = "720p"
    p1080 = "1080p"
    p1440 = "1440p"
    p2160 = "2160p"


class QualityConfig(BaseModel):
    preset: QualityPreset = QualityPreset.balanced
    target_resolution: Optional[ResolutionPreset] = Field(
        default=None,
        description=(
            "Central resolution target. Caps Radarr/Sonarr quality profiles, "
            "drives the pipeline keep/replace rule and the enrichment upgrade target."
        ),
    )
    allow_upgrades: Optional[bool] = Field(
        default=None,
        description=(
            "Upgrade toggle: sets upgradeAllowed on the arr profiles and lets the "
            "pipeline replace a library file with a higher resolution one within the target. "
            "None leaves arr profiles' upgradeAllowed untouched and the pipeline never replaces."
        ),
    )
    max_bitrate_mbps: Optional[int] = Field(default=None, ge=1)
    # Output container for pipeline imports: "mkv" or "mp4".
    preferred_container: str = "mkv"

    @validator("preferred_container")
    def validate_container(cls, value: str) -> str:
        normalized = (value or "").strip().lower().lstrip(".")
        if normalized not in ("mkv", "mp4"):
            raise ValueError("preferred_container must be 'mkv' or 'mp4'")
        return normalized


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
    hardware_acceleration: Literal["none", "vaapi", "nvenc"] = "none"
    # vaapi: a host device path (/dev/dri). nvenc: a CDI device name (nvidia.com/gpu=all).
    hardware_device: str = "/dev/dri"
    render_group_id: Optional[int] = Field(default=None, ge=0)
    video_group_id: Optional[int] = Field(default=None, ge=0)
    transcode_path: str = "/transcode"
    subtitle_mode: Literal["Default", "Always", "OnlyForced", "None", "Smart"] = (
        "OnlyForced"
    )
    remember_subtitle_selections: bool = False


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


class EnrichmentConfig(BaseModel):
    """Configuration for the media enrichment pipeline (audio cross-mux).

    When enabled, the enrichment engine continuously searches for missing
    audio languages (e.g., English dub for Japanese anime), downloads
    alternate releases via Prowlarr, uses chromaprint fingerprinting for
    precise audio alignment, and cross-muxes missing tracks into library files.

    Runs as an always-on daemon: maintains a queue of files needing enrichment,
    keeps up to max_concurrent_downloads active at all times, and processes
    completed downloads immediately. New library additions are prioritized.
    """

    enabled: bool = False

    # --- Concurrency ---
    max_concurrent_downloads: int = Field(
        default=1, ge=1,
        description="Maximum enrichment downloads active at once in qBittorrent",
    )
    queue_rescan_hours: int = Field(
        default=6, ge=1,
        description="How often to rebuild the candidate queue from a full library scan",
    )

    # --- Backward compat (ignored, kept so old configs don't error) ---
    search_interval_hours: int = Field(default=24, ge=1, exclude=True)
    max_grabs_per_cycle: int = Field(default=2, ge=1, exclude=True)
    search_queries: List[str] = Field(
        default_factory=lambda: ["dual audio", "english dub", "multi"],
        description="Search terms appended to title when searching Prowlarr",
    )
    min_seeders: int = Field(
        default=3, ge=1,
        description="Minimum seeders for enrichment candidate releases",
    )

    # --- Chromaprint ---
    correlation_threshold: float = Field(
        default=0.70, ge=0.1, le=1.0,
        description="Minimum chromaprint fingerprint correlation score (0-1)",
    )
    fingerprint_duration_seconds: int = Field(
        default=120, ge=30,
        description="Duration of audio to fingerprint (longer = more accurate, slower)",
    )

    # --- Target audio ---
    target_languages: List[str] = Field(
        default_factory=list,
        description=(
            "Audio languages every library file should have. Use ISO 639-2 "
            "codes (e.g. 'eng', 'jpn'). The special value 'original' resolves "
            "to each media item's original language from Radarr/Sonarr metadata. "
            "If empty, derived from media_policy.keep_audio + 'original'."
        ),
    )

    # --- Video quality upgrades ---
    upgrade_video: bool = Field(
        default=True,
        description="Search for higher-quality video when library files are below target",
    )
    target_resolution: str = Field(
        default="1080p",
        description="Minimum acceptable resolution (720p, 1080p, 1440p, 2160p)",
    )
    prefer_hdr: bool = Field(
        default=True,
        description="Prefer HDR releases over SDR when upgrading video",
    )
    prefer_hevc: bool = Field(
        default=True,
        description="Prefer HEVC/H.265 over H.264 when upgrading video",
    )

    # --- Stale grab cleanup ---
    grab_ttl_hours: int = Field(
        default=48, ge=1,
        description="Maximum hours a grabbed torrent can remain before being evicted as stale",
    )
    stall_timeout_hours: int = Field(
        default=6, ge=1,
        description="Hours of zero download progress before a torrent is considered stalled",
    )


class RecommenderConfig(BaseModel):
    """Configuration for the media recommendation engine.

    When enabled, the recommender loads pre-computed movie embeddings from
    the Remsky HuggingFace dataset (680K movies, nomic-embed-text 768-dim),
    builds a FAISS similarity index, ingests Jellyfin watch history per user,
    and generates personalized recommendations (both owned and unowned media).
    """

    enabled: bool = False

    # --- Index ---
    refresh_interval_hours: int = Field(
        default=24, ge=1,
        description="How often to rebuild the FAISS index and refresh recommendations",
    )
    min_vote_count: int = Field(
        default=10, ge=0,
        description="Minimum TMDb vote_count to include in index (reduces memory)",
    )
    use_compressed_index: bool = Field(
        default=True,
        description="Use FAISS IndexIVFPQ (~41MB) instead of IndexFlatIP (~600MB)",
    )

    # --- User profiles ---
    min_watched_for_profile: int = Field(
        default=3, ge=1,
        description="Minimum watched items before generating recommendations",
    )

    # --- Recommendations ---
    max_recommendations_per_user: int = Field(
        default=50, ge=10,
        description="Maximum stored recommendations per user",
    )
    because_you_watched_count: int = Field(
        default=10, ge=1,
        description="Items per 'Because you watched X' row",
    )
    owned_weight: float = Field(
        default=1.5, ge=1.0, le=5.0,
        description="Score multiplier for items already in Jellyfin library",
    )

    # --- LightFM collaborative filtering ---
    collaborative_enabled: bool = Field(
        default=False,
        description="Enable LightFM collaborative filtering (requires >= 5 users with profiles)",
    )
    collaborative_weight: float = Field(
        default=0.3, ge=0.0, le=1.0,
        description="Blend weight for collaborative vs content-based scores (0=content only, 1=collab only)",
    )

    # --- Auto-request via Jellyseerr ---
    auto_request_enabled: bool = Field(
        default=False,
        description="Automatically request top unowned recommendations via Jellyseerr",
    )
    auto_request_max_per_day: int = Field(
        default=2, ge=0,
        description="Maximum auto-requests per day per user",
    )

    # --- TMDb metadata enrichment ---
    tmdb_api_key: Optional[str] = Field(
        default=None,
        description="TMDb API v3 key for fetching poster images and metadata",
    )


class PipelineConfig(ServiceBaseConfig):
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    proxy_url: Optional[str] = None
    backfill: BackfillConfig = Field(default_factory=BackfillConfig)
    enrichment: EnrichmentConfig = Field(default_factory=EnrichmentConfig)
    recommender: RecommenderConfig = Field(default_factory=RecommenderConfig)


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


# Media Audit / Remediation Models


class MediaAuditBatchSummary(BaseModel):
    """Summary for one staged media remediation batch."""

    name: str
    label: str
    description: str
    mode: str = "lossless_remux"
    remuxable: bool = False
    file_count: int = 0
    total_bytes: int = 0
    severity_counts: Dict[str, int] = Field(default_factory=dict)
    issue_counts: Dict[str, int] = Field(default_factory=dict)


class MediaAuditScanResponse(BaseModel):
    """Result of running a media audit scan."""

    summary: Dict[str, Any]
    json_path: str
    markdown_path: str


class MediaAuditLatestResponse(BaseModel):
    """Latest media audit metadata, if available."""

    available: bool
    summary: Dict[str, Any] = Field(default_factory=dict)
    json_path: Optional[str] = None
    markdown_path: Optional[str] = None


class RemediationPlanResponse(BaseModel):
    """Batch remediation plan built from the latest audit."""

    needs_audit: bool = False
    audit_path: Optional[str] = None
    markdown_path: Optional[str] = None
    started_at: Optional[str] = None
    total_files: int = 0
    batches: List[MediaAuditBatchSummary] = Field(default_factory=list)


class RemediationStartRequest(BaseModel):
    """Request to start a staged remediation job."""

    batch_name: str
    dry_run: bool = False


class RemediationStartResponse(BaseModel):
    """Acknowledgement that remediation has started."""

    job_id: str
    batch_name: str
    status: str
    total_files: int = 0
    dry_run: bool = False


class RemediationStatusResponse(BaseModel):
    """Current durable remediation job status."""

    status: str = "idle"
    job_id: Optional[str] = None
    batch_name: Optional[str] = None
    mode: Optional[str] = None
    dry_run: bool = False
    progress_current: int = 0
    progress_total: int = 0
    current_file: Optional[str] = None
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    errors: List[str] = Field(default_factory=list)
    started_at: Optional[str] = None
    updated_at: Optional[str] = None
    ended_at: Optional[str] = None
    input_audit_path: Optional[str] = None
