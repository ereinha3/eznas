"""Config diff engine — detects changes and maps them to affected services.

Compares two StackConfig instances and produces a structured diff showing:
  - Exactly which fields changed (old → new)
  - Which services need a Docker restart (port, path, runtime changes)
  - Which services need API reconfiguration (credentials, policy changes)

This powers the "BIOS-like" settings experience: the user edits config,
sees exactly what changed, and confirms before applying.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from ..models import StackConfig


@dataclass
class ConfigChange:
    """A single field-level configuration change."""

    path: str  # dot-separated path, e.g. "services.radarr.port"
    old_value: Any
    new_value: Any
    affected_services: List[str]  # services impacted by this change


@dataclass
class ConfigDiff:
    """Result of comparing two StackConfigs."""

    changes: List[ConfigChange] = field(default_factory=list)
    services_to_restart: Set[str] = field(default_factory=set)
    services_to_reconfigure: Set[str] = field(default_factory=set)

    @property
    def has_changes(self) -> bool:
        return len(self.changes) > 0

    def summary_lines(self) -> List[str]:
        """Return a human-readable summary of all changes."""
        if not self.has_changes:
            return ["No changes detected"]

        lines: List[str] = []
        for change in self.changes:
            lines.append(f"{change.path}: {_format_value(change.old_value)} → {_format_value(change.new_value)}")
        if self.services_to_restart:
            lines.append(f"Services to restart: {', '.join(sorted(self.services_to_restart))}")
        if self.services_to_reconfigure:
            lines.append(f"Services to reconfigure: {', '.join(sorted(self.services_to_reconfigure))}")
        return lines

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API responses."""
        return {
            "has_changes": self.has_changes,
            "changes": [
                {
                    "path": c.path,
                    "old_value": c.old_value,
                    "new_value": c.new_value,
                    "affected_services": c.affected_services,
                }
                for c in self.changes
            ],
            "services_to_restart": sorted(self.services_to_restart),
            "services_to_reconfigure": sorted(self.services_to_reconfigure),
            "summary": self.summary_lines(),
        }


# ---------------------------------------------------------------------------
# Impact mapping: config path prefix → which services are affected and how.
#
# "restart"     = Docker container must be recreated (port, volume, or env change)
# "reconfigure" = API-level reconfiguration via ensure() is sufficient
#
# Uses longest-prefix matching: "services.radarr.port" matches before
# "services.radarr" which matches before "services".
# ---------------------------------------------------------------------------
_ALL_MEDIA_SERVICES = [
    "qbittorrent", "radarr", "sonarr", "prowlarr", "jellyfin", "jellyseerr", "pipeline",
]

CHANGE_IMPACT: Dict[str, Dict[str, List[str]]] = {
    # ---- Path changes affect all services (Docker volume mounts change) ----
    "paths.pool": {
        "restart": _ALL_MEDIA_SERVICES,
    },
    "paths.scratch": {
        "restart": ["qbittorrent", "pipeline"],
    },
    "paths.appdata": {
        "restart": ["qbittorrent", "radarr", "sonarr", "prowlarr", "jellyfin", "jellyseerr"],
    },

    # ---- Service port changes: restart self + reconfigure dependents ----
    "services.qbittorrent.port": {
        "restart": ["qbittorrent"],
        "reconfigure": ["radarr", "sonarr"],
    },
    "services.radarr.port": {
        "restart": ["radarr"],
        "reconfigure": ["prowlarr", "jellyseerr"],
    },
    "services.sonarr.port": {
        "restart": ["sonarr"],
        "reconfigure": ["prowlarr", "jellyseerr"],
    },
    "services.prowlarr.port": {
        "restart": ["prowlarr"],
    },
    "services.jellyfin.port": {
        "restart": ["jellyfin"],
        "reconfigure": ["jellyseerr"],
    },
    "services.jellyseerr.port": {
        "restart": ["jellyseerr"],
    },

    # ---- Service enabled/disabled: restart self + reconfigure dependents ----
    "services.qbittorrent.enabled": {
        "restart": ["qbittorrent"],
        "reconfigure": ["radarr", "sonarr"],
    },
    "services.radarr.enabled": {
        "restart": ["radarr"],
        "reconfigure": ["prowlarr", "jellyseerr"],
    },
    "services.sonarr.enabled": {
        "restart": ["sonarr"],
        "reconfigure": ["prowlarr", "jellyseerr"],
    },
    "services.prowlarr.enabled": {
        "restart": ["prowlarr"],
    },
    "services.jellyfin.enabled": {
        "restart": ["jellyfin"],
        "reconfigure": ["jellyseerr"],
    },
    "services.jellyseerr.enabled": {
        "restart": ["jellyseerr"],
    },
    "services.pipeline.enabled": {
        "restart": ["pipeline"],
    },

    # ---- qBittorrent-specific settings ----
    "services.qbittorrent.username": {
        "reconfigure": ["qbittorrent"],
    },
    "services.qbittorrent.password": {
        "reconfigure": ["qbittorrent"],
    },
    "services.qbittorrent.stop_after_download": {
        "reconfigure": ["qbittorrent"],
    },

    # ---- Prowlarr-specific settings ----
    "services.prowlarr.language_filter": {
        "reconfigure": ["prowlarr"],
    },

    # ---- Proxy URL changes on any service need Traefik reconfigure ----
    "services.qbittorrent.proxy_url": {"restart": ["qbittorrent"]},
    "services.radarr.proxy_url": {"restart": ["radarr"]},
    "services.sonarr.proxy_url": {"restart": ["sonarr"]},
    "services.prowlarr.proxy_url": {"restart": ["prowlarr"]},
    "services.jellyfin.proxy_url": {"restart": ["jellyfin"]},
    "services.jellyseerr.proxy_url": {"restart": ["jellyseerr"]},

    # ---- Download policy — affects qBittorrent categories + arr naming ----
    "download_policy": {
        "reconfigure": ["qbittorrent", "radarr", "sonarr"],
    },

    # ---- Media policy — affects pipeline behavior ----
    "media_policy": {
        "reconfigure": ["pipeline"],
    },

    # ---- Quality settings — affect Radarr/Sonarr profiles ----
    "quality": {
        "reconfigure": ["radarr", "sonarr"],
    },

    # ---- Proxy (Traefik) changes ----
    "proxy": {
        "restart": _ALL_MEDIA_SERVICES,
    },

    # ---- Runtime (UID/GID/TZ) — all containers need restart ----
    "runtime": {
        "restart": _ALL_MEDIA_SERVICES,
    },

    # ---- UI port change — only affects orchestrator itself ----
    "ui.port": {
        "restart": [],
    },
}


def compute_diff(old: StackConfig, new: StackConfig) -> ConfigDiff:
    """Compare two configurations and return a structured diff.

    Walks the full config tree, identifies every changed leaf value,
    and maps each change to the services it affects.
    """
    old_dict = old.model_dump(mode="json")
    new_dict = new.model_dump(mode="json")

    old_leaves = _flatten(old_dict)
    new_leaves = _flatten(new_dict)

    all_paths = set(old_leaves.keys()) | set(new_leaves.keys())

    changes: List[ConfigChange] = []
    restart: Set[str] = set()
    reconfigure: Set[str] = set()

    for path in sorted(all_paths):
        old_val = old_leaves.get(path)
        new_val = new_leaves.get(path)

        if old_val == new_val:
            continue

        impact = _resolve_impact(path)
        restart_svcs = impact.get("restart", [])
        reconfig_svcs = impact.get("reconfigure", [])
        all_affected = sorted(set(restart_svcs + reconfig_svcs))

        changes.append(
            ConfigChange(
                path=path,
                old_value=old_val,
                new_value=new_val,
                affected_services=all_affected,
            )
        )

        restart.update(restart_svcs)
        reconfigure.update(reconfig_svcs)

    # Services that need restart don't also need separate reconfigure —
    # restart implies a full re-ensure cycle.
    reconfigure -= restart

    return ConfigDiff(
        changes=changes,
        services_to_restart=restart,
        services_to_reconfigure=reconfigure,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _flatten(d: dict, prefix: str = "") -> Dict[str, Any]:
    """Flatten a nested dict into dot-separated path → value pairs.

    Lists are treated as atomic values (not diffed element-by-element)
    to avoid noisy diffs on reordering.
    """
    result: Dict[str, Any] = {}
    for key, value in d.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            result.update(_flatten(value, path))
        else:
            # Lists, scalars, None — all treated as leaf values
            result[path] = value
    return result


def _resolve_impact(path: str) -> Dict[str, List[str]]:
    """Find the best matching impact rule for a config path.

    Uses longest-prefix matching so "services.radarr.port" is matched
    before "services.radarr" before "services".
    """
    parts = path.split(".")
    for i in range(len(parts), 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in CHANGE_IMPACT:
            return CHANGE_IMPACT[candidate]
    return {}


def _format_value(val: Any) -> str:
    """Format a value for human-readable display."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, str):
        return f'"{val}"'
    if isinstance(val, list):
        if len(val) == 0:
            return "[]"
        if len(val) <= 3:
            return "[" + ", ".join(_format_value(v) for v in val) + "]"
        return f"[{len(val)} items]"
    return str(val)
