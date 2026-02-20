"""Service configuration orchestration with dependency awareness."""
from __future__ import annotations

import logging
from typing import Dict, List, Set

from ..clients.base import EnsureOutcome, ServiceClient
from ..clients.jellyfin import JellyfinClient
from ..clients.jellyseerr import JellyseerrClient
from ..clients.prowlarr import ProwlarrClient
from ..clients.qb import QBittorrentClient
from ..clients.radarr import RadarrClient
from ..clients.sonarr import SonarrClient
from ..constants import SERVICE_DEPENDENCY_ORDER
from ..models import StackConfig, StageEvent
from ..storage import ConfigRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dependency graph: if a key fails, all its values are skipped.
# Read as: "service X depends on these services being configured first"
# ---------------------------------------------------------------------------
SERVICE_DEPENDENCIES: Dict[str, List[str]] = {
    "qbittorrent": [],
    "radarr": ["qbittorrent"],
    "sonarr": ["qbittorrent"],
    "prowlarr": ["radarr", "sonarr"],
    "jellyfin": [],
    "jellyseerr": ["jellyfin", "radarr", "sonarr"],
    "pipeline": [],
}


class ServiceConfigurator:
    """Orchestrates ensure/verify operations across enabled services.

    Dependency-aware: if a service fails during ensure(), all services
    that depend on it are automatically skipped with a clear message.
    """

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo
        self.clients: Dict[str, ServiceClient] = {
            "qbittorrent": QBittorrentClient(repo=repo),
            "radarr": RadarrClient(repo=repo),
            "sonarr": SonarrClient(repo=repo),
            "prowlarr": ProwlarrClient(repo=repo),
            "jellyseerr": JellyseerrClient(repo=repo),
            "jellyfin": JellyfinClient(repo=repo),
        }

    def ensure(self, config: StackConfig) -> List[StageEvent]:
        """Configure all enabled services in dependency order.

        If a service fails, all services that depend on it are skipped
        with a descriptive message about which dependency failed.
        """
        events: List[StageEvent] = []
        service_map = config.services.model_dump(mode="python")
        failed_services: Set[str] = set()

        for name in SERVICE_DEPENDENCY_ORDER:
            settings = service_map.get(name, {})
            is_enabled = settings.get("enabled", True)
            stage_name = f"configure.{name}"

            # Skip disabled services
            if not is_enabled:
                events.append(
                    StageEvent(stage=stage_name, status="ok", detail="skipped (disabled)")
                )
                continue

            # Check if any dependencies failed
            blocked_by = self._get_blocked_dependencies(name, failed_services)
            if blocked_by:
                failed_services.add(name)
                detail = f"skipped (dependency failed: {', '.join(sorted(blocked_by))})"
                events.append(
                    StageEvent(stage=stage_name, status="failed", detail=detail)
                )
                logger.warning(f"Skipping {name}: blocked by failed dependencies {blocked_by}")
                continue

            # Pipeline has no client — it's a compose-only service
            client = self.clients.get(name)
            if client is None:
                if name == "pipeline":
                    events.append(
                        StageEvent(
                            stage=stage_name, status="ok", detail="skipped (no ensure required)"
                        )
                    )
                else:
                    events.append(
                        StageEvent(
                            stage=stage_name, status="failed", detail="unsupported service"
                        )
                    )
                    failed_services.add(name)
                continue

            # Run ensure and track failures
            outcome = self._safe_ensure(client, config)
            status = "ok" if outcome.success else "failed"
            detail = outcome.detail or ""
            events.append(StageEvent(stage=stage_name, status=status, detail=detail))

            if not outcome.success:
                failed_services.add(name)
                logger.warning(f"Service {name} failed to configure: {detail}")

        return events

    def verify(self, config: StackConfig) -> List[StageEvent]:
        """Verify all enabled services in dependency order.

        Unlike ensure(), verification does NOT skip on dependency failure —
        we want to see the full picture of what's healthy and what's not.
        """
        events: List[StageEvent] = []
        service_map = config.services.model_dump(mode="python")

        for name in SERVICE_DEPENDENCY_ORDER:
            settings = service_map.get(name, {})
            is_enabled = settings.get("enabled", True)
            stage_name = f"verify.{name}"

            if not is_enabled:
                events.append(
                    StageEvent(stage=stage_name, status="ok", detail="skipped (disabled)")
                )
                continue

            client = self.clients.get(name)
            if client is None:
                events.append(
                    StageEvent(stage=stage_name, status="ok", detail="skipped (no client)")
                )
                continue

            verify = getattr(client, "verify", None)
            if verify is None:
                events.append(
                    StageEvent(stage=stage_name, status="ok", detail="skipped (no verification)")
                )
                continue

            outcome = verify(config)
            status = "ok" if outcome.success else "failed"
            detail = outcome.detail or ""
            events.append(StageEvent(stage=stage_name, status=status, detail=detail))

        return events

    def _get_blocked_dependencies(
        self, service: str, failed_services: Set[str]
    ) -> Set[str]:
        """Return the set of failed services that block this service."""
        deps = SERVICE_DEPENDENCIES.get(service, [])
        return {dep for dep in deps if dep in failed_services}

    def _safe_ensure(self, client: ServiceClient, config: StackConfig) -> EnsureOutcome:
        try:
            return client.ensure(config)
        except Exception as exc:
            logger.error(f"Unhandled exception in {type(client).__name__}.ensure(): {exc}")
            return EnsureOutcome(detail=str(exc), changed=False, success=False)
