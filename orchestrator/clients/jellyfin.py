"""Jellyfin automation client."""
from __future__ import annotations

from pathlib import Path
from typing import List

from .base import EnsureOutcome, ServiceClient
from ..models import StackConfig
from ..storage import ConfigRepository


class JellyfinClient(ServiceClient):
    """Currently reports configured library mounts and stored admin password."""

    name = "jellyfin"

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    def ensure(self, config: StackConfig) -> EnsureOutcome:
        state = self.repo.load_state()
        secrets = state.get("secrets", {})
        jellyfin_secrets = secrets.get("jellyfin", {})
        admin_password = jellyfin_secrets.get("admin_password")
        admin_username = jellyfin_secrets.get("admin_username")

        libraries: List[Path] = [
            Path(config.paths.pool) / "media" / section for section in ("movies", "tv", "anime")
        ]
        detail_parts = ["libraries=" + ",".join(str(lib) for lib in libraries)]
        detail_parts.append("admin_username set" if admin_username else "admin_username pending")
        detail_parts.append("admin_password set" if admin_password else "admin_password pending")
        return EnsureOutcome(detail="; ".join(detail_parts), changed=False)







