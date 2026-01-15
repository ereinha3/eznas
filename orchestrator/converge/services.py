"""Service configuration orchestration."""
from __future__ import annotations

from typing import Dict, List

from ..clients.base import EnsureOutcome, ServiceClient
from ..clients.jellyfin import JellyfinClient
from ..clients.jellyseerr import JellyseerrClient
from ..clients.prowlarr import ProwlarrClient
from ..clients.qb import QBittorrentClient
from ..clients.radarr import RadarrClient
from ..clients.sonarr import SonarrClient
from ..models import StackConfig, StageEvent
from ..storage import ConfigRepository


class ServiceConfigurator:
    """Orchestrates ensure_* operations across enabled services."""

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
        events: List[StageEvent] = []
        service_map = config.services.model_dump(mode="python")
        ordered_services = [
            "qbittorrent",
            "radarr",
            "sonarr",
            "prowlarr",
            "jellyfin",
            "jellyseerr",
            "pipeline",
        ]
        for name in ordered_services:
            settings = service_map.get(name, {})
            is_enabled = settings.get("enabled", True)
            stage_name = f"configure.{name}"
            if not is_enabled:
                events.append(
                    StageEvent(stage=stage_name, status="ok", detail="skipped (disabled)")
                )
                continue

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
                continue

            outcome = self._safe_ensure(client, config)
            status = "ok" if outcome.success else "failed"
            detail = outcome.detail or ""
            events.append(StageEvent(stage=stage_name, status=status, detail=detail))

        return events

    def verify(self, config: StackConfig) -> List[StageEvent]:
        events: List[StageEvent] = []
        service_map = config.services.model_dump(mode="python")
        ordered_services = [
            "qbittorrent",
            "radarr",
            "sonarr",
            "prowlarr",
            "jellyfin",
            "jellyseerr",
            "pipeline",
        ]
        for name in ordered_services:
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

    def _safe_ensure(self, client: ServiceClient, config: StackConfig) -> EnsureOutcome:
        try:
            return client.ensure(config)
        except Exception as exc:  # pragma: no cover - placeholder for real implementations
            return EnsureOutcome(detail=str(exc), changed=False, success=False)


