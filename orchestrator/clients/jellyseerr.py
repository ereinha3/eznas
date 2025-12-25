"""Jellyseerr automation client."""
from __future__ import annotations

from pathlib import Path
from typing import List

from .base import EnsureOutcome, ServiceClient
from ..models import StackConfig
from ..storage import ConfigRepository


class JellyseerrClient(ServiceClient):
    """Currently reports stored credentials and linked services."""

    name = "jellyseerr"

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    def ensure(self, config: StackConfig) -> EnsureOutcome:
        state = self.repo.load_state()
        secrets = state.get("secrets", {})
        jelly_secrets = secrets.get("jellyseerr", {})
        admin_password = jelly_secrets.get("admin_password")
        admin_username = jelly_secrets.get("admin_username")
        linked: List[str] = []
        if config.services.radarr.enabled:
            linked.append("radarr")
        if config.services.sonarr.enabled:
            linked.append("sonarr")

        detail_parts = []
        if admin_username:
            detail_parts.append("admin_username set")
        else:
            detail_parts.append("admin_username pending")
        if admin_password:
            detail_parts.append("admin_password set")
        else:
            detail_parts.append("admin_password pending")
        detail_parts.append("linked=" + (".".join(linked) if linked else "none"))
        return EnsureOutcome(detail="; ".join(detail_parts), changed=False)







