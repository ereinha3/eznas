"""Jellyseerr automation client."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import httpx

from .arr import wait_for_http_ready
from .base import EnsureOutcome, ServiceClient
from .util import get_service_config_dir
from ..constants import CONTAINER_PATHS
from ..models import StackConfig
from ..storage import ConfigRepository


log = logging.getLogger(__name__)


@dataclass
class _EnsureResult:
    changed: bool
    detail: str


class JellyseerrClient(ServiceClient):
    """Provision Jellyseerr via HTTP API."""

    name = "jellyseerr"
    INTERNAL_PORT = 5055

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    def ensure(self, config: StackConfig) -> EnsureOutcome:
        svc_cfg = config.services.jellyseerr
        base_url = f"http://jellyseerr:{self.INTERNAL_PORT}"
        status_url = f"{base_url}/api/v1/status"
        ok, status_detail = wait_for_http_ready(
            status_url,
            timeout=180.0,
            interval=5.0,
        )
        if not ok:
            return EnsureOutcome(
                detail=f"Jellyseerr not ready ({status_detail})",
                changed=False,
                success=False,
            )

        state = self.repo.load_state()
        secrets = state.get("secrets", {})
        jelly_secrets = secrets.get("jellyseerr", {})
        admin_username = jelly_secrets.get("admin_username", "admin")
        admin_password = jelly_secrets.get("admin_password", "adminadmin")

        detail_parts: list[str] = []
        changed = False

        try:
            public = self._get_public_settings(base_url)
        except httpx.RequestError as exc:
            log.debug("Unable to reach Jellyseerr at %s: %s", base_url, exc, exc_info=True)
            return EnsureOutcome(
                detail=f"connection failed ({exc.__class__.__name__}: {exc})",
                changed=False,
                success=False,
            )

        if not public.get("initialized", False):
            try:
                startup = self._complete_startup(
                    base_url=base_url,
                    username=admin_username,
                    password=admin_password,
                    config=config,
                )
            except httpx.HTTPError as exc:
                log.debug("Failed to complete Jellyseerr startup: %s", exc, exc_info=True)
                return EnsureOutcome(
                    detail=f"startup failed ({getattr(exc, 'response', None) and exc.response.status_code})",
                    changed=changed,
                    success=False,
                )
            changed = changed or startup.changed
            if startup.detail:
                detail_parts.append(startup.detail)

        api_key = self._read_api_key(config)
        if not api_key:
            return EnsureOutcome(
                detail="api key missing",
                changed=changed,
                success=False,
            )

        headers = {"Accept": "application/json", "X-Api-Key": api_key}
        client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(20.0, connect=5.0),
        )
        try:
            radarr_result = self._ensure_radarr(client, config, state)
            changed = changed or radarr_result.changed
            if radarr_result.detail:
                detail_parts.append(radarr_result.detail)

            sonarr_result = self._ensure_sonarr(client, config, state)
            changed = changed or sonarr_result.changed
            if sonarr_result.detail:
                detail_parts.append(sonarr_result.detail)
        finally:
            client.close()

        detail = "; ".join(part for part in detail_parts if part) or "ok"
        return EnsureOutcome(detail=detail, changed=changed, success=True)

    def verify(self, config: StackConfig) -> EnsureOutcome:
        api_key = self._read_api_key(config)
        if not api_key:
            return EnsureOutcome(detail="api key missing", changed=False, success=False)

        svc_cfg = config.services.jellyseerr
        base_url = f"http://jellyseerr:{self.INTERNAL_PORT}"
        headers = {"Accept": "application/json", "X-Api-Key": api_key}

        try:
            with httpx.Client(
                base_url=base_url,
                headers=headers,
                timeout=httpx.Timeout(20.0, connect=5.0),
            ) as client:
                public = client.get("/api/v1/settings/public")
                public.raise_for_status()
                if not public.json().get("initialized", False):
                    return EnsureOutcome(detail="startup incomplete", changed=False, success=False)

                detail_parts = []
                failures = []

                if config.services.radarr.enabled:
                    response = client.get("/api/v1/settings/radarr")
                    response.raise_for_status()
                    radarr_entries = response.json() or []
                    if not self._matching_app(radarr_entries, "radarr", config.services.radarr.port):
                        failures.append("radarr")

                if config.services.sonarr.enabled:
                    response = client.get("/api/v1/settings/sonarr")
                    response.raise_for_status()
                    sonarr_entries = response.json() or []
                    if not self._matching_app(sonarr_entries, "sonarr", config.services.sonarr.port):
                        failures.append("sonarr")

                if failures:
                    detail_parts.append(f"missing links: {', '.join(failures)}")
                if not detail_parts:
                    detail_parts.append("settings ok")

                success = not failures
                return EnsureOutcome(detail="; ".join(detail_parts), changed=False, success=success)
        except httpx.RequestError as exc:
            return EnsureOutcome(
                detail=f"connection failed ({exc.__class__.__name__}: {exc})",
                changed=False,
                success=False,
            )
        except httpx.HTTPStatusError as exc:
            return EnsureOutcome(
                detail=f"api error {exc.response.status_code}",
                changed=False,
                success=False,
            )

    # ------------------------------------------------------------------ setup helpers
    def _get_public_settings(self, base_url: str) -> Dict:
        with httpx.Client(base_url=base_url, timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = client.get("/api/v1/settings/public")
            response.raise_for_status()
            return response.json()

    def _complete_startup(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        config: StackConfig,
    ) -> _EnsureResult:
        """Complete Jellyseerr initialization, handling partial states.

        Jellyseerr requires a session cookie (connect.sid) to call /settings/initialize.
        The auth endpoint has two modes:
        - With 'hostname': Sets up a new Jellyfin connection (first-time only)
        - Without 'hostname': Logs in with existing Jellyfin credentials

        This handles the edge case where Jellyfin is configured but initialization
        wasn't completed (e.g., container restart, network timeout).
        """
        with httpx.Client(base_url=base_url, timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            # Check current state
            public = client.get("/api/v1/settings/public")
            public.raise_for_status()
            settings = public.json()

            # MediaServerType: 1=Plex, 2=Jellyfin, 3=Emby, 4=None
            jellyfin_configured = settings.get("mediaServerType") == 2

            if jellyfin_configured:
                # Jellyfin already connected - just login to get session cookie
                log.debug("Jellyfin already configured, logging in to complete initialization")
                login_resp = client.post("/api/v1/auth/jellyfin", json={
                    "username": username,
                    "password": password,
                })
                login_resp.raise_for_status()
            else:
                # First time setup - configure Jellyfin connection
                log.debug("Setting up Jellyfin connection for Jellyseerr")
                jellyfin_cfg = config.services.jellyfin
                payload = {
                    "hostname": "jellyfin",
                    "port": 8096,  # JellyfinClient.INTERNAL_PORT (container-to-container)
                    "useSsl": False,
                    "urlBase": "",
                    "serverType": 2,  # MediaServerType.JELLYFIN
                    "username": username,
                    "password": password,
                    "email": f"{username}@example.com",
                }
                client.post("/api/v1/auth/jellyfin", json=payload).raise_for_status()

            # Complete initialization (requires session cookie from auth call)
            client.post("/api/v1/settings/initialize", json={}).raise_for_status()

        return _EnsureResult(changed=True, detail="startup=completed")

    def _read_api_key(self, config: StackConfig) -> Optional[str]:
        config_dir = get_service_config_dir("jellyseerr", config)
        settings_path = config_dir / "settings.json"
        try:
            data = json.loads(settings_path.read_text())
        except FileNotFoundError:
            log.debug("Jellyseerr settings.json missing at %s", settings_path)
            return None
        except json.JSONDecodeError as exc:
            log.debug("Unable to parse Jellyseerr settings.json: %s", exc, exc_info=True)
            return None
        return data.get("main", {}).get("apiKey")

    @staticmethod
    def _matching_app(entries: Iterable[Dict], host: str, port: Optional[int]) -> bool:
        for entry in entries or []:
            if entry.get("hostname") == host and entry.get("port") == port:
                return True
        return False

    # ------------------------------------------------------------------ Radarr integration
    def _ensure_radarr(
        self,
        client: httpx.Client,
        config: StackConfig,
        state: Dict,
    ) -> _EnsureResult:
        if not config.services.radarr.enabled:
            return _EnsureResult(False, "radarr=skipped (disabled)")

        radarr_secrets = state.get("secrets", {}).get("radarr", {})
        api_key = radarr_secrets.get("api_key")
        if not api_key:
            return _EnsureResult(False, "radarr=skipped (no api key)")

        target_host = "radarr"
        target_port = config.services.radarr.port

        response = client.get("/api/v1/settings/radarr")
        response.raise_for_status()
        existing = response.json() or []
        if self._entry_exists(existing, target_host, target_port):
            return _EnsureResult(False, "radarr=ready")

        test_payload = {
            "hostname": target_host,
            "port": target_port,
            "apiKey": api_key,
            "useSsl": False,
            "baseUrl": "",
        }
        test = client.post("/api/v1/settings/radarr/test", json=test_payload)
        test.raise_for_status()
        body = test.json()

        profile = self._pick_first(body.get("profiles"))
        root_dir = self._select_root(body.get("rootFolders"), CONTAINER_PATHS["media_movies"])

        if profile is None or root_dir is None:
            return _EnsureResult(
                changed=False,
                detail="radarr=incomplete (profiles or root folders missing)",
            )

        create_payload = {
            "name": "Radarr",
            "hostname": target_host,
            "port": target_port,
            "apiKey": api_key,
            "useSsl": False,
            "baseUrl": body.get("urlBase") or "",
            "activeProfileId": profile.get("id"),
            "activeProfileName": profile.get("name"),
            "activeDirectory": root_dir,
            "is4k": False,
            "minimumAvailability": "announced",
            "isDefault": True,
            "externalUrl": "",
            "syncEnabled": True,
            "preventSearch": False,
        }
        client.post("/api/v1/settings/radarr", json=create_payload).raise_for_status()
        return _EnsureResult(True, "radarr=linked")

    # ------------------------------------------------------------------ Sonarr integration
    def _ensure_sonarr(
        self,
        client: httpx.Client,
        config: StackConfig,
        state: Dict,
    ) -> _EnsureResult:
        if not config.services.sonarr.enabled:
            return _EnsureResult(False, "sonarr=skipped (disabled)")

        sonarr_secrets = state.get("secrets", {}).get("sonarr", {})
        api_key = sonarr_secrets.get("api_key")
        if not api_key:
            return _EnsureResult(False, "sonarr=skipped (no api key)")

        target_host = "sonarr"
        target_port = config.services.sonarr.port

        response = client.get("/api/v1/settings/sonarr")
        response.raise_for_status()
        existing = response.json() or []
        if self._entry_exists(existing, target_host, target_port):
            return _EnsureResult(False, "sonarr=ready")

        test_payload = {
            "hostname": target_host,
            "port": target_port,
            "apiKey": api_key,
            "useSsl": False,
            "baseUrl": "",
        }
        test = client.post("/api/v1/settings/sonarr/test", json=test_payload)
        test.raise_for_status()
        body = test.json()

        profile = self._pick_first(body.get("profiles"))
        language_profile = self._pick_first(body.get("languageProfiles"))
        root_dir = self._select_root(body.get("rootFolders"), CONTAINER_PATHS["media_tv"])

        if profile is None or root_dir is None:
            return _EnsureResult(
                changed=False,
                detail="sonarr=incomplete (profiles or root folders missing)",
            )

        create_payload = {
            "name": "Sonarr",
            "hostname": target_host,
            "port": target_port,
            "apiKey": api_key,
            "useSsl": False,
            "baseUrl": body.get("urlBase") or "",
            "activeProfileId": profile.get("id"),
            "activeProfileName": profile.get("name"),
            "activeDirectory": root_dir,
            "activeLanguageProfileId": (language_profile or {}).get("id", 1),
            "is4k": False,
            "enableSeasonFolders": True,
            "isDefault": True,
            "externalUrl": "",
            "syncEnabled": True,
            "preventSearch": False,
            "activeAnimeDirectory": None,
            "activeAnimeProfileId": None,
            "activeAnimeProfileName": None,
            "activeAnimeLanguageProfileId": None,
        }
        client.post("/api/v1/settings/sonarr", json=create_payload).raise_for_status()
        return _EnsureResult(True, "sonarr=linked")

    # ------------------------------------------------------------------ utility helpers
    @staticmethod
    def _entry_exists(entries: Iterable[Dict], host: str, port: Optional[int]) -> bool:
        for entry in entries or []:
            if entry.get("hostname") == host and entry.get("port") == port:
                return True
        return False

    @staticmethod
    def _pick_first(items: Optional[Iterable[Dict]]) -> Optional[Dict]:
        if not items:
            return None
        for item in items:
            return item
        return None

    @staticmethod
    def _select_root(items: Optional[Iterable[Dict]], desired: str) -> Optional[str]:
        if not items:
            return desired
        for item in items:
            path = item.get("path")
            if path == desired:
                return path
        first = JellyseerrClient._pick_first(items)
        return first.get("path") if first else desired