"""Bazarr subtitle management client."""
from __future__ import annotations

import json
import logging
import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlencode

import httpx

from .arr import wait_for_http_ready
from .base import EnsureOutcome, ServiceClient
from .util import get_service_config_dir, resolve_service_host, service_base_url
from ..models import StackConfig
from ..storage import ConfigRepository


log = logging.getLogger(__name__)

# ISO 639-2/B (3-letter) -> ISO 639-1 (2-letter) for Bazarr language codes.
# Bazarr uses 2-letter codes internally for language identification.
_ISO3_TO_ISO2: dict[str, str] = {
    "eng": "en", "fre": "fr", "ger": "de", "spa": "es", "ita": "it",
    "por": "pt", "dut": "nl", "jpn": "ja", "kor": "ko", "chi": "zh",
    "ara": "ar", "hin": "hi", "rus": "ru", "pol": "pl", "tur": "tr",
    "cze": "cs", "dan": "da", "fin": "fi", "gre": "el", "heb": "he",
    "hun": "hu", "nor": "no", "swe": "sv", "tha": "th", "vie": "vi",
    "rom": "ro", "bul": "bg", "hrv": "hr", "ind": "id", "may": "ms",
    "ukr": "uk", "cat": "ca", "ice": "is", "lit": "lt", "lav": "lv",
    "est": "et", "ben": "bn", "tam": "ta", "tel": "te", "per": "fa",
    "bos": "bs", "slv": "sl", "srp": "sr", "kan": "kn", "gle": "ga",
}

# Default subtitle providers — free, no account/API-key required.
# opensubtitlescom and subdl were removed because they raise
# ConfigurationError without credentials, causing permanent throttle.
_DEFAULT_PROVIDERS = [
    "podnapisi",          # Large multilingual database, no auth
    "animetosho",         # Excellent for anime subtitles, no auth
    "subf2m",             # Broad English coverage, no auth
    "gestdown",           # Addic7ed mirror, good for TV, no auth
    "yifysubtitles",      # Movie-focused, no auth
    "embeddedsubtitles",  # Extracts subs already in the file, no auth
    "tvsubtitles",        # TV-focused, no auth
]


@dataclass
class _EnsureResult:
    changed: bool
    detail: str


class BazarrClient(ServiceClient):
    """Provision Bazarr via HTTP API.

    IMPORTANT: Bazarr's settings API uses form-encoded POST (not JSON).
    Settings keys use the format ``settings-section-key`` (e.g.
    ``settings-general-enabled_providers``).  Language enablement and
    profile creation use separate form fields (``languages-enabled``,
    ``languages-profiles``).
    """

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

            # Configure subtitle languages from user media_policy
            langs_result = self._ensure_languages(client, config)
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

                # Check profiles exist
                failures = []
                profiles = client.get("/api/system/languages/profiles")
                profiles.raise_for_status()
                if not profiles.json():
                    failures.append("no language profiles")

                # Check providers
                settings = self._get_settings(client)
                providers = (settings.get("general") or {}).get("enabled_providers") or []
                if not providers:
                    failures.append("no subtitle providers")

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

    def _post_form(
        self,
        client: httpx.Client,
        data: Union[dict[str, str], list[tuple[str, str]]],
    ) -> None:
        """POST form-encoded data to the Bazarr settings API.

        Bazarr's settings endpoint expects form-encoded data, NOT JSON.
        Keys use the format ``settings-section-key`` for config values,
        or special fields like ``languages-enabled`` and ``languages-profiles``.

        Accepts both a dict (unique keys) and a list of tuples (repeated keys).
        Uses manual urlencode + content= to work with httpx >=0.28.
        """
        encoded = urlencode(data, doseq=True)
        resp = client.post(
            "/api/system/settings",
            content=encoded,
            headers={"content-type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()

    # ------------------------------------------------------------------ arr links

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
        target_port = 7878

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
            self._post_form(client, {
                "settings-general-use_radarr": "true",
                "settings-radarr-ip": target_host,
                "settings-radarr-port": str(target_port),
                "settings-radarr-apikey": api_key,
                "settings-radarr-ssl": "false",
                "settings-radarr-base_url": "",
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
        target_port = 8989

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
            self._post_form(client, {
                "settings-general-use_sonarr": "true",
                "settings-sonarr-ip": target_host,
                "settings-sonarr-port": str(target_port),
                "settings-sonarr-apikey": api_key,
                "settings-sonarr-ssl": "false",
                "settings-sonarr-base_url": "",
            })
        except httpx.HTTPError as exc:
            return _EnsureResult(False, f"sonarr=failed (settings update: {exc})")

        return _EnsureResult(True, "sonarr=linked")

    # ------------------------------------------------------------------ providers

    def _ensure_providers(
        self,
        client: httpx.Client,
    ) -> _EnsureResult:
        """Ensure subtitle providers are configured."""
        try:
            settings = self._get_settings(client)
        except httpx.HTTPError as exc:
            return _EnsureResult(False, f"providers=failed ({exc})")

        general = settings.get("general") or {}
        current_providers = general.get("enabled_providers") or []
        if current_providers:
            return _EnsureResult(False, f"providers=ready ({', '.join(current_providers)})")

        try:
            # Form data with repeated key for list values
            form: list[tuple[str, str]] = [
                ("settings-general-enabled_providers", p)
                for p in _DEFAULT_PROVIDERS
            ]
            self._post_form(client, form)
            return _EnsureResult(True, f"providers={'+'.join(_DEFAULT_PROVIDERS)}")
        except httpx.HTTPError as exc:
            log.debug("Could not configure providers: %s", exc)
            return _EnsureResult(False, "providers=auto-config failed")

    # ------------------------------------------------------------------ languages

    def _ensure_languages(
        self,
        client: httpx.Client,
        config: StackConfig,
    ) -> _EnsureResult:
        """Configure Bazarr language profiles from the user's media_policy.

        Steps:
        1. Enable the required languages in Bazarr's language table
        2. Create a language profile with those languages
        3. Set the profile as default for both series and movies
        """
        # Derive desired subtitle languages from media_policy.keep_subs
        # (ISO 639-2/B codes like "eng", "spa", etc.)
        desired_iso3 = config.media_policy.movies.keep_subs
        if not desired_iso3:
            return _EnsureResult(False, "languages=skipped (no keep_subs configured)")

        # Convert to Bazarr's 2-letter codes
        desired_codes: list[tuple[str, str]] = []  # (code2, code3) pairs
        for iso3 in desired_iso3:
            code2 = _ISO3_TO_ISO2.get(iso3.lower())
            if code2:
                desired_codes.append((code2, iso3.lower()))
            else:
                log.warning("bazarr: unknown language code '%s', skipping", iso3)

        if not desired_codes:
            return _EnsureResult(False, "languages=skipped (no mappable codes)")

        # Check current state
        try:
            settings = self._get_settings(client)
            profiles = client.get("/api/system/languages/profiles").json()
        except httpx.HTTPError as exc:
            return _EnsureResult(False, f"languages=failed ({exc})")

        general = settings.get("general") or {}

        # Check if we already have a matching profile set as default
        desired_code2_set = {c[0] for c in desired_codes}
        for profile in profiles:
            items = profile.get("items") or []
            profile_langs = {item.get("language") for item in items}
            if desired_code2_set == profile_langs:
                profile_id = profile.get("profileId")
                serie_default = general.get("serie_default_profile")
                movie_default = general.get("movie_default_profile")
                serie_enabled = general.get("serie_default_enabled", False)
                movie_enabled = general.get("movie_default_enabled", False)
                if (
                    serie_default == profile_id
                    and movie_default == profile_id
                    and serie_enabled
                    and movie_enabled
                ):
                    return _EnsureResult(False, "languages=ready")
                # Profile exists but not set as default — fix that
                break

        changed = False
        details: list[str] = []

        # Step 1: Enable the required languages in Bazarr's language table
        try:
            enabled_langs = client.get("/api/system/languages").json()
        except httpx.HTTPError as exc:
            return _EnsureResult(False, f"languages=failed (fetch: {exc})")

        enabled_code2s = {
            lang["code2"] for lang in enabled_langs if lang.get("enabled")
        }
        need_enable = desired_code2_set - enabled_code2s

        if need_enable:
            # Enable desired languages (must send ALL desired codes — Bazarr
            # replaces the entire enabled set with what we send)
            all_to_enable = enabled_code2s | desired_code2_set
            form: list[tuple[str, str]] = [
                ("languages-enabled", code) for code in sorted(all_to_enable)
            ]
            try:
                self._post_form(client, form)
                details.append(f"enabled {', '.join(sorted(need_enable))}")
                changed = True
            except httpx.HTTPError as exc:
                return _EnsureResult(False, f"languages=failed (enable: {exc})")

        # Step 2: Create or update a language profile
        # Build profile name from language names
        lang_names = []
        for code2, _ in desired_codes:
            for lang in enabled_langs:
                if lang.get("code2") == code2:
                    lang_names.append(lang.get("name", code2))
                    break
            else:
                lang_names.append(code2.upper())

        profile_name = " + ".join(lang_names)

        # Check if a matching profile already exists
        target_profile_id = None
        for profile in profiles:
            items = profile.get("items") or []
            profile_langs = {item.get("language") for item in items}
            if desired_code2_set == profile_langs:
                target_profile_id = profile.get("profileId")
                break

        if target_profile_id is None:
            # Create new profile — pick an unused profileId
            existing_ids = {p.get("profileId", 0) for p in profiles}
            target_profile_id = 1
            while target_profile_id in existing_ids:
                target_profile_id += 1

            profile_items = [
                {
                    "id": idx + 1,
                    "language": code2,
                    "hi": False,
                    "forced": False,
                    "audio_exclude": False,
                    "audio_only_include": False,
                }
                for idx, (code2, _) in enumerate(desired_codes)
            ]

            new_profile = {
                "profileId": target_profile_id,
                "name": profile_name,
                "items": profile_items,
                "cutoff": None,
                "mustContain": [],
                "mustNotContain": [],
                "originalFormat": False,
            }

            # Include existing profiles + new one
            all_profiles = list(profiles) + [new_profile]
            try:
                self._post_form(
                    client,
                    {"languages-profiles": json.dumps(all_profiles)},
                )
                details.append(f"profile '{profile_name}' created (id={target_profile_id})")
                changed = True
            except httpx.HTTPError as exc:
                return _EnsureResult(False, f"languages=failed (profile create: {exc})")

        # Step 3: Set as default for series and movies
        try:
            self._post_form(client, {
                "settings-general-serie_default_profile": str(target_profile_id),
                "settings-general-serie_default_enabled": "true",
                "settings-general-movie_default_profile": str(target_profile_id),
                "settings-general-movie_default_enabled": "true",
            })
            if not details:
                details.append("defaults updated")
            changed = True
        except httpx.HTTPError as exc:
            return _EnsureResult(False, f"languages=failed (defaults: {exc})")

        # Step 4: Apply profile to existing series/movies that have no profile
        assigned = self._apply_profile_to_existing(client, target_profile_id)
        if assigned:
            details.append(assigned)
            changed = True

        detail = "languages=" + (", ".join(details) if details else "ready")
        return _EnsureResult(changed, detail)

    def _apply_profile_to_existing(
        self,
        client: httpx.Client,
        profile_id: int,
    ) -> Optional[str]:
        """Assign language profile to all existing series/movies missing one.

        Bazarr's POST /api/series and /api/movies use reqparse query params:
        ``seriesid``/``radarrid`` and ``profileid`` as parallel repeated params.
        """
        parts: list[str] = []

        # Series without a profile
        try:
            resp = client.get("/api/series", params={"start": 0, "length": -1})
            resp.raise_for_status()
            series = resp.json().get("data", [])
            unassigned = [
                s["sonarrSeriesId"]
                for s in series
                if s.get("profileId") is None
            ]
            if unassigned:
                params: list[tuple[str, str]] = []
                for sid in unassigned:
                    params.append(("seriesid", str(sid)))
                    params.append(("profileid", str(profile_id)))
                r = client.post("/api/series", params=params)
                r.raise_for_status()
                parts.append(f"{len(unassigned)} series assigned")
        except httpx.HTTPError as exc:
            log.warning("bazarr: failed to assign profile to series: %s", exc)

        # Movies without a profile
        try:
            resp = client.get("/api/movies", params={"start": 0, "length": -1})
            resp.raise_for_status()
            movies = resp.json().get("data", [])
            unassigned = [
                m["radarrId"]
                for m in movies
                if m.get("profileId") is None
            ]
            if unassigned:
                params = []
                for mid in unassigned:
                    params.append(("radarrid", str(mid)))
                    params.append(("profileid", str(profile_id)))
                r = client.post("/api/movies", params=params)
                r.raise_for_status()
                parts.append(f"{len(unassigned)} movies assigned")
        except httpx.HTTPError as exc:
            log.warning("bazarr: failed to assign profile to movies: %s", exc)

        return ", ".join(parts) if parts else None
