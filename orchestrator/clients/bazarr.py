"""Bazarr subtitle management client."""
from __future__ import annotations

import logging
import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx

from .arr import wait_for_http_ready
from .base import EnsureOutcome, ServiceClient
from .util import get_service_config_dir, resolve_service_host, service_base_url
from ..models import StackConfig
from ..storage import ConfigRepository


log = logging.getLogger(__name__)


@dataclass
class _EnsureResult:
    changed: bool
    detail: str


class BazarrClient(ServiceClient):
    """Provision Bazarr via HTTP API."""

    name = "bazarr"
    INTERNAL_PORT = 6767

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    def ensure(self, config: StackConfig) -> EnsureOutcome:
        base_url = service_base_url("bazarr", config, self.INTERNAL_PORT)

        # Wait for Bazarr to be ready (use root URL which serves the UI without auth)
        ok, status_detail = wait_for_http_ready(
            base_url,
            timeout=180.0,
            interval=5.0,
        )
        if not ok:
            return EnsureOutcome(
                detail=f"Bazarr not ready ({status_detail})",
                changed=False,
                success=False,
            )

        api_key = self._read_api_key(config)
        if not api_key:
            return EnsureOutcome(
                detail="api key not yet available (Bazarr may still be initializing)",
                changed=False,
                success=False,
            )

        headers = {"x-api-key": api_key}
        client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=5.0),
        )

        detail_parts: list[str] = []
        changed = False

        try:
            # Configure Radarr connection
            radarr_result = self._ensure_radarr(client, config)
            changed = changed or radarr_result.changed
            if radarr_result.detail:
                detail_parts.append(radarr_result.detail)

            # Configure Sonarr connection
            sonarr_result = self._ensure_sonarr(client, config)
            changed = changed or sonarr_result.changed
            if sonarr_result.detail:
                detail_parts.append(sonarr_result.detail)

            # Configure subtitle providers
            providers_result = self._ensure_providers(client)
            changed = changed or providers_result.changed
            if providers_result.detail:
                detail_parts.append(providers_result.detail)

            # Configure subtitle languages
            langs_result = self._ensure_languages(client)
            changed = changed or langs_result.changed
            if langs_result.detail:
                detail_parts.append(langs_result.detail)

        finally:
            client.close()

        detail = "; ".join(part for part in detail_parts if part) or "ok"
        return EnsureOutcome(detail=detail, changed=changed, success=True)

    def verify(self, config: StackConfig) -> EnsureOutcome:
        api_key = self._read_api_key(config)
        if not api_key:
            return EnsureOutcome(detail="api key missing", changed=False, success=False)

        base_url = service_base_url("bazarr", config, self.INTERNAL_PORT)
        headers = {"x-api-key": api_key}

        try:
            with httpx.Client(
                base_url=base_url,
                headers=headers,
                timeout=httpx.Timeout(20.0, connect=5.0),
            ) as client:
                # Check system status
                status = client.get("/api/system/status")
                status.raise_for_status()

                # Check Radarr connection
                failures = []
                if config.services.radarr.enabled:
                    try:
                        resp = client.get("/api/system/health")
                        resp.raise_for_status()
                    except httpx.HTTPError:
                        failures.append("health check failed")

                detail = "ok" if not failures else "; ".join(failures)
                return EnsureOutcome(
                    detail=detail, changed=False, success=not failures
                )
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

    # ------------------------------------------------------------------ helpers

    def _read_api_key(self, config: StackConfig) -> Optional[str]:
        config_dir = get_service_config_dir("bazarr", config)
        config_path = config_dir / "config" / "config.yaml"
        if not config_path.exists():
            # Bazarr may also store config.ini in older versions
            config_ini = config_dir / "config" / "config.ini"
            if config_ini.exists():
                return self._read_api_key_from_ini(config_ini)
            log.debug("Bazarr config not found at %s", config_path)
            return None
        try:
            data = yaml.safe_load(config_path.read_text())
            return data.get("auth", {}).get("apikey")
        except (yaml.YAMLError, OSError) as exc:
            log.debug("Unable to parse Bazarr config: %s", exc, exc_info=True)
            return None

    @staticmethod
    def _read_api_key_from_ini(path: Path) -> Optional[str]:
        import configparser
        cp = configparser.ConfigParser()
        try:
            cp.read(str(path))
            return cp.get("auth", "apikey", fallback=None)
        except (configparser.Error, OSError):
            return None

    def _get_settings(self, client: httpx.Client) -> dict:
        """Fetch current Bazarr settings."""
        resp = client.get("/api/system/settings")
        resp.raise_for_status()
        return resp.json()

    def _update_settings(self, client: httpx.Client, payload: dict) -> None:
        """Update Bazarr settings via POST."""
        resp = client.post("/api/system/settings", json=payload)
        resp.raise_for_status()

    def _ensure_radarr(
        self,
        client: httpx.Client,
        config: StackConfig,
    ) -> _EnsureResult:
        if not config.services.radarr.enabled:
            return _EnsureResult(False, "radarr=skipped (disabled)")

        state = self.repo.load_state()
        radarr_secrets = state.get("secrets", {}).get("radarr", {})
        api_key = radarr_secrets.get("api_key")
        if not api_key:
            return _EnsureResult(False, "radarr=skipped (no api key)")

        target_host = resolve_service_host("radarr", config, caller="bazarr")
        target_port = 7878  # Internal port

        try:
            settings = self._get_settings(client)
        except httpx.HTTPError as exc:
            return _EnsureResult(False, f"radarr=failed (settings read: {exc})")

        radarr_settings = settings.get("radarr") or {}
        general_settings = settings.get("general") or {}
        use_radarr = general_settings.get("use_radarr", False)

        already_configured = (
            use_radarr
            and radarr_settings.get("ip") == target_host
            and radarr_settings.get("port") == target_port
            and radarr_settings.get("apikey") == api_key
        )
        if already_configured:
            return _EnsureResult(False, "radarr=ready")

        try:
            self._update_settings(client, {
                "radarr": {
                    "ip": target_host,
                    "port": target_port,
                    "apikey": api_key,
                    "ssl": False,
                    "base_url": "",
                },
                "general": {"use_radarr": True},
            })
        except httpx.HTTPError as exc:
            return _EnsureResult(False, f"radarr=failed (settings update: {exc})")

        return _EnsureResult(True, "radarr=linked")

    def _ensure_sonarr(
        self,
        client: httpx.Client,
        config: StackConfig,
    ) -> _EnsureResult:
        if not config.services.sonarr.enabled:
            return _EnsureResult(False, "sonarr=skipped (disabled)")

        state = self.repo.load_state()
        sonarr_secrets = state.get("secrets", {}).get("sonarr", {})
        api_key = sonarr_secrets.get("api_key")
        if not api_key:
            return _EnsureResult(False, "sonarr=skipped (no api key)")

        target_host = resolve_service_host("sonarr", config, caller="bazarr")
        target_port = 8989  # Internal port

        try:
            settings = self._get_settings(client)
        except httpx.HTTPError as exc:
            return _EnsureResult(False, f"sonarr=failed (settings read: {exc})")

        sonarr_settings = settings.get("sonarr") or {}
        general_settings = settings.get("general") or {}
        use_sonarr = general_settings.get("use_sonarr", False)

        already_configured = (
            use_sonarr
            and sonarr_settings.get("ip") == target_host
            and sonarr_settings.get("port") == target_port
            and sonarr_settings.get("apikey") == api_key
        )
        if already_configured:
            return _EnsureResult(False, "sonarr=ready")

        try:
            self._update_settings(client, {
                "sonarr": {
                    "ip": target_host,
                    "port": target_port,
                    "apikey": api_key,
                    "ssl": False,
                    "base_url": "",
                },
                "general": {"use_sonarr": True},
            })
        except httpx.HTTPError as exc:
            return _EnsureResult(False, f"sonarr=failed (settings update: {exc})")

        return _EnsureResult(True, "sonarr=linked")

    def _ensure_providers(
        self,
        client: httpx.Client,
    ) -> _EnsureResult:
        """Ensure at least one subtitle provider is configured."""
        try:
            settings = self._get_settings(client)
        except httpx.HTTPError as exc:
            return _EnsureResult(False, f"providers=failed ({exc})")

        general = settings.get("general") or {}
        current_providers = general.get("enabled_providers") or []
        if current_providers:
            return _EnsureResult(False, "providers=ready")

        # Enable OpenSubtitles.com as a default free provider
        try:
            self._update_settings(client, {
                "general": {"enabled_providers": ["opensubtitlescom"]},
            })
            return _EnsureResult(True, "providers=opensubtitlescom enabled")
        except httpx.HTTPError as exc:
            log.debug("Could not configure providers: %s", exc)
            return _EnsureResult(False, "providers=auto-config failed")

    def _ensure_languages(
        self,
        client: httpx.Client,
    ) -> _EnsureResult:
        """Check if language profiles are configured."""
        try:
            settings = self._get_settings(client)
        except httpx.HTTPError as exc:
            return _EnsureResult(False, f"languages=failed ({exc})")

        general = settings.get("general") or {}
        languages = general.get("serie_default_language") or []
        movie_languages = general.get("movie_default_language") or []

        has_en_series = any(
            lang.get("code2") == "en" or lang.get("code3") == "eng"
            for lang in (languages if isinstance(languages, list) else [])
        )
        has_en_movies = any(
            lang.get("code2") == "en" or lang.get("code3") == "eng"
            for lang in (movie_languages if isinstance(movie_languages, list) else [])
        )

        if has_en_series and has_en_movies:
            return _EnsureResult(False, "languages=ready")

        return _EnsureResult(False, "languages=needs manual setup (configure via Bazarr UI)")
