"""Centralized constants for the NAS orchestrator.

All hardcoded service ports, container paths, and dependency ordering
should be defined here — not scattered across individual clients.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Internal container ports (the ports services listen on INSIDE their container)
# These are NOT the host-mapped ports (those come from StackConfig).
# ---------------------------------------------------------------------------
INTERNAL_PORTS: dict[str, int] = {
    "qbittorrent": 8080,
    "radarr": 7878,
    "sonarr": 8989,
    "prowlarr": 9696,
    "jellyseerr": 5055,
    "jellyfin": 8096,
}

# ---------------------------------------------------------------------------
# Default host-mapped ports (used as defaults in StackConfig models)
# Same as internal ports for most services, but separated conceptually
# because users can change host ports while internal ports stay fixed.
# ---------------------------------------------------------------------------
DEFAULT_HOST_PORTS: dict[str, int] = {
    "qbittorrent": 8080,
    "radarr": 7878,
    "sonarr": 8989,
    "prowlarr": 9696,
    "jellyseerr": 5055,
    "jellyfin": 8096,
    "ui": 8443,
}

# ---------------------------------------------------------------------------
# Container path layout
# These are the paths INSIDE containers, derived from Docker volume mounts.
# The actual host paths come from config.paths.pool / config.paths.scratch.
# ---------------------------------------------------------------------------
CONTAINER_PATHS = {
    # Media library paths (inside *arr and jellyfin containers)
    "movies": "/data/movies",
    "tv": "/data/tv",

    # Download paths (inside qbittorrent container)
    "downloads": "/downloads",
    "downloads_complete": "/downloads/complete",
    "downloads_incomplete": "/downloads/incomplete",

    # Media root (inside jellyfin/jellyseerr containers)
    "media_movies": "/data/media/movies",
    "media_tv": "/data/media/tv",
}

# ---------------------------------------------------------------------------
# Service dependency order for the converge pipeline.
# Services are configured in this order. If a service fails,
# dependents later in the list should be skipped.
# ---------------------------------------------------------------------------
SERVICE_DEPENDENCY_ORDER: list[str] = [
    "qbittorrent",
    "radarr",
    "sonarr",
    "prowlarr",
    "jellyfin",
    "jellyseerr",
    "pipeline",
]

# ---------------------------------------------------------------------------
# Service container names (used for Docker networking)
# These match the service names in docker-compose.yml.j2
# ---------------------------------------------------------------------------
CONTAINER_NAMES: dict[str, str] = {
    "qbittorrent": "qbittorrent",
    "radarr": "radarr",
    "sonarr": "sonarr",
    "prowlarr": "prowlarr",
    "jellyseerr": "jellyseerr",
    "jellyfin": "jellyfin",
}

# ---------------------------------------------------------------------------
# API path prefixes per service
# ---------------------------------------------------------------------------
API_PREFIXES: dict[str, str] = {
    "radarr": "/api/v3",
    "sonarr": "/api/v3",
    "prowlarr": "/api/v1",
    "jellyfin": "",
    "jellyseerr": "/api/v1",
}
