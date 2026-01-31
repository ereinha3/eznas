"""Prowlarr automation client."""
from __future__ import annotations

import logging
import secrets as py_secrets
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .arr import ArrAPI, set_field_values, wait_for_http_ready
from .base import EnsureOutcome, ServiceClient
from .util import (
    arr_password_matches,
    get_service_config_dir,
    read_arr_api_key,
    read_arr_port,
    read_arr_url_base,
    wait_for_arr_config,
)
from ..models import StackConfig, IndexerSchema, IndexerInfo
from ..storage import ConfigRepository

log = logging.getLogger(__name__)


class ProwlarrClient(ServiceClient):
    """Provision and link Prowlarr applications."""

    name = "prowlarr"
    INTERNAL_PORT = 9696

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    def ensure(self, config: StackConfig) -> EnsureOutcome:
        state = self.repo.load_state()
        services_state = state.setdefault("services", {})
        prowlarr_state = services_state.setdefault("prowlarr", {})
        secrets_state = state.setdefault("secrets", {})
        prowlarr_secrets = secrets_state.setdefault("prowlarr", {})

        detail_messages: List[str] = []
        changed = False
        state_dirty = False

        config_dir = get_service_config_dir("prowlarr", config)
        config_dir.mkdir(parents=True, exist_ok=True)
        stored_api_key = prowlarr_secrets.get("api_key")
        config_api_key: Optional[str] = None
        if not wait_for_arr_config(config_dir):
            return EnsureOutcome(
                detail=f"config.xml did not appear at {config_dir}",
                changed=False,
                success=False,
            )
        config_api_key = read_arr_api_key(config_dir)
        if not config_api_key:
            return EnsureOutcome(
                detail=f"Prowlarr API key missing in config.xml at {config_dir}",
                changed=False,
                success=False,
            )

        api_key = config_api_key
        if stored_api_key != config_api_key:
            prowlarr_secrets["api_key"] = config_api_key
            state_dirty = True
            detail_messages.append("refreshed API key from config.xml")

        ui_username = prowlarr_secrets.get("ui_username")
        if not ui_username:
            ui_username = "prowlarr-admin"
            prowlarr_secrets["ui_username"] = ui_username
            state_dirty = True

        ui_password = prowlarr_secrets.get("ui_password")
        if not ui_password:
            ui_password = py_secrets.token_urlsafe(12)
            prowlarr_secrets["ui_password"] = ui_password
            state_dirty = True

        if state_dirty:
            self.repo.save_state(state)
            state_dirty = False

        radarr_cfg = config.services.radarr
        sonarr_cfg = config.services.sonarr
        prowlarr_cfg = config.services.prowlarr

        radarr_key = secrets_state.get("radarr", {}).get("api_key")
        sonarr_key = secrets_state.get("sonarr", {}).get("api_key")
        if radarr_cfg.enabled and not radarr_key:
            return EnsureOutcome(
                detail="waiting for Radarr API key",
                changed=False,
                success=False,
            )
        if sonarr_cfg.enabled and not sonarr_key:
            return EnsureOutcome(
                detail="waiting for Sonarr API key",
                changed=False,
                success=False,
            )

        base_url = f"http://prowlarr:{self.INTERNAL_PORT}/api/v1"
        status_url = f"{base_url}/system/status"
        ok, status_detail = wait_for_http_ready(
            status_url,
            timeout=180.0,
            interval=5.0,
        )
        if not ok:
            return EnsureOutcome(
                detail=f"Prowlarr not ready ({status_detail})",
                changed=changed,
                success=False,
            )
        def _provision(active_api_key: str) -> None:
            nonlocal changed, state_dirty
            log.info(f"Provisioning Prowlarr with API key: {active_api_key[:8]}...")
            # Configure UI authentication (first attempt)
            db_path = config_dir / "prowlarr.db"
            try:
                log.debug(f"Configuring host settings for Prowlarr at {base_url}")
                host_changed = self._ensure_host_settings(
                    base_url=base_url,
                    port=prowlarr_cfg.port,
                    api_key=active_api_key,
                    db_path=db_path,
                    username=ui_username,
                    password=ui_password,
                )
                if host_changed:
                    log.info("Host settings updated successfully")
                    detail_messages.append("ui credentials synced")
                    changed = True
                else:
                    log.debug("Host settings already configured")
            except httpx.HTTPStatusError as exc:
                # Re-raise auth errors so retry logic can catch them
                if exc.response.status_code in (401, 403):
                    raise
                # For other HTTP errors, log and continue - we'll retry inside ArrAPI context
                log.debug(f"Initial host settings config failed, will retry: {exc}")
            except httpx.RequestError as exc:
                # For connection errors, log and continue - we'll retry inside ArrAPI context
                log.debug(f"Initial host settings config failed, will retry: {exc}")

            with ArrAPI(base_url, active_api_key) as api:
                status = api.get_json("/system/status")
                version = status.get("version")
                detail_messages.append(
                    f"online (v{version})" if version else "online"
                )

                # Configure UI authentication (fallback/verification inside API context)
                log.debug("Checking host config inside API context")
                host_config = api.get_json("/config/host")
                current_auth_method = host_config.get("authenticationMethod")
                log.debug(f"Current auth method: {current_auth_method}")
                password_matches = arr_password_matches(db_path, ui_username, ui_password)
                desired_method = "forms"
                desired_required = "enabled"
                analytics_flag = host_config.get("analyticsEnabled")
                host_update_required = (
                    host_config.get("authenticationMethod") != desired_method
                    or host_config.get("authenticationRequired") != desired_required
                    or host_config.get("username") != ui_username
                    or bool(analytics_flag)
                    or not password_matches
                )
                if host_update_required:
                    log.info(f"Updating host settings: authMethod={current_auth_method} -> {desired_method}, username={ui_username}")
                    payload = dict(host_config)
                    payload.update(
                        {
                            "authenticationMethod": desired_method,
                            "authenticationRequired": desired_required,
                            "analyticsEnabled": False,
                            "username": ui_username,
                            "password": ui_password,
                            "passwordConfirmation": ui_password,
                        }
                    )
                    api.put_json("/config/host", payload)
                    log.info("Host settings updated via API context")
                    detail_messages.append("ui credentials synced")
                    changed = True
                else:
                    log.debug("Host settings already correct, no update needed")

                app_changes: List[Tuple[bool, str]] = []
                if radarr_cfg.enabled and radarr_key:
                    changed_app, msg = self._ensure_application(
                        api,
                        config,
                        display_name="Radarr",
                        service_name="radarr",
                        api_key=radarr_key,
                    )
                    app_changes.append((changed_app, msg))
                if sonarr_cfg.enabled and sonarr_key:
                    changed_app, msg = self._ensure_application(
                        api,
                        config,
                        display_name="Sonarr",
                        service_name="sonarr",
                        api_key=sonarr_key,
                    )
                    app_changes.append((changed_app, msg))

                for changed_flag, message in app_changes:
                    if message:
                        detail_messages.append(message)
                    changed = changed or changed_flag

                # Auto-populate indexers on first setup
                if not prowlarr_state.get("indexers_populated"):
                    added, skipped, failed = self.auto_populate_indexers(config)
                    if added:
                        detail_messages.append(f"added {len(added)} indexers")
                        changed = True
                    # Mark as populated even if some failed - user can retry manually
                    if added or skipped:
                        prowlarr_state["indexers_populated"] = True
                        state_dirty = True

        try:
            _provision(api_key)
        except httpx.HTTPStatusError as exc:
            log.debug("Prowlarr API error", exc_info=True)
            if exc.response.status_code in (401, 403):
                refreshed_key = read_arr_api_key(config_dir)
                if refreshed_key and refreshed_key != api_key:
                    prowlarr_secrets["api_key"] = refreshed_key
                    api_key = refreshed_key
                    state_dirty = True
                    detail_messages.append("reloaded API key after auth failure")
                    try:
                        _provision(api_key)
                    except httpx.HTTPStatusError as retry_exc:
                        log.debug("Prowlarr API error after retry", exc_info=True)
                        return EnsureOutcome(
                            detail=f"Prowlarr API error {retry_exc.response.status_code}: {retry_exc.response.text}",
                            changed=changed,
                            success=False,
                        )
            return EnsureOutcome(
                detail=f"Prowlarr API error {exc.response.status_code}: {exc.response.text}",
                changed=changed,
                success=False,
            )
        except httpx.RequestError as exc:
            log.debug("Prowlarr request error", exc_info=True)
            return EnsureOutcome(
                detail=f"Prowlarr unreachable at {base_url}: {exc}",
                changed=changed,
                success=False,
            )
        except RuntimeError as exc:
            return EnsureOutcome(
                detail=f"host settings sync failed ({exc})",
                changed=changed,
                success=False,
            )

        if detail_messages and detail_messages[0].startswith("online") and len(detail_messages) > 1:
            detail_combined = "; ".join(detail_messages)
        else:
            detail_combined = "; ".join(detail_messages) if detail_messages else "ok"

        if state_dirty:
            self.repo.save_state(state)

        return EnsureOutcome(detail=detail_combined, changed=changed, success=True)

    def verify(self, config: StackConfig) -> EnsureOutcome:
        state = self.repo.load_state()
        secrets_state = state.get("secrets", {})
        prowlarr_secrets = secrets_state.get("prowlarr", {})

        api_key = prowlarr_secrets.get("api_key")
        if not api_key:
            return EnsureOutcome(detail="missing api key", changed=False, success=False)

        prowlarr_cfg = config.services.prowlarr
        base_url = f"http://prowlarr:{self.INTERNAL_PORT}/api/v1"

        try:
            with ArrAPI(base_url, api_key) as api:
                existing = api.get_json("/applications") or []
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

        expected_apps = []
        if config.services.radarr.enabled:
            expected_apps.append(("Radarr", "radarr"))
        if config.services.sonarr.enabled:
            expected_apps.append(("Sonarr", "sonarr"))

        missing = []
        mismatched = []
        for display_name, service_name in expected_apps:
            prowlarr_config_dir = get_service_config_dir("prowlarr", config)
            prowlarr_url = self._build_service_url(
                service_name="prowlarr",
                host="prowlarr",
                config_dir=prowlarr_config_dir,
                default_port=self.INTERNAL_PORT,
            )
            service_config_dir = get_service_config_dir(service_name, config)
            service_url = self._build_service_url(
                service_name=service_name,
                host=service_name,
                config_dir=service_config_dir,
                default_port=self._default_service_port(service_name),
            )
            expected_fields = {
                "baseUrl": self._normalize_base_url(service_url),
                "prowlarrUrl": self._normalize_base_url(prowlarr_url),
            }

            found = None
            for entry in existing:
                if (entry.get("implementation") or "").lower() == display_name.lower():
                    found = entry
                    break
            if not found:
                missing.append(display_name)
                continue

            fields = {f["name"]: f.get("value") for f in found.get("fields", [])}
            current_base = self._normalize_base_url(fields.get("baseUrl"))
            current_prowlarr = self._normalize_base_url(fields.get("prowlarrUrl"))
            if current_base != expected_fields["baseUrl"] or current_prowlarr != expected_fields["prowlarrUrl"]:
                mismatched.append(display_name)

        detail_parts = []
        if missing:
            detail_parts.append(f"missing apps: {', '.join(missing)}")
        if mismatched:
            detail_parts.append(f"mismatched apps: {', '.join(mismatched)}")
        if detail_parts:
            return EnsureOutcome(detail="; ".join(detail_parts), changed=False, success=False)

        return EnsureOutcome(detail="applications ok", changed=False, success=True)

    # ------------------------------------------------------------------ helpers

    def _ensure_host_settings(
        self,
        base_url: str,
        port: int,
        api_key: str,
        db_path: Path,
        username: str,
        password: str,
    ) -> bool:
        """Configure Prowlarr's authentication settings."""
        with httpx.Client(
            base_url=base_url.rstrip('/'),
            headers={"X-Api-Key": api_key},
            timeout=httpx.Timeout(15.0, connect=5.0),
        ) as client:
            response = client.get("/config/host")
            response.raise_for_status()
            host_config = response.json()

            password_matches = arr_password_matches(db_path, username, password)
            desired_method = "forms"
            desired_required = "enabled"
            analytics_flag = host_config.get("analyticsEnabled")
            needs_update = (
                host_config.get("authenticationMethod") != desired_method
                or host_config.get("authenticationRequired") != desired_required
                or host_config.get("username") != username
                or bool(analytics_flag)
                or not password_matches
            )

            if not needs_update:
                return False

            payload = dict(host_config)
            payload.update(
                {
                    "authenticationMethod": desired_method,
                    "authenticationRequired": desired_required,
                    "analyticsEnabled": False,
                    "username": username,
                    "password": password,
                    "passwordConfirmation": password,
                }
            )
            client.put("/config/host", json=payload).raise_for_status()

        ok, message = wait_for_http_ready(
            f"http://prowlarr:{port}/api/v1/system/status",
            timeout=120.0,
            interval=5.0,
        )
        if not ok:
            raise RuntimeError(message)
        return True

    def _ensure_application(
        self,
        api: ArrAPI,
        config: StackConfig,
        *,
        display_name: str,
        service_name: str,
        api_key: str,
    ) -> Tuple[bool, str]:
        implementation = display_name
        prowlarr_config_dir = get_service_config_dir("prowlarr", config)
        prowlarr_url = self._build_service_url(
            service_name="prowlarr",
            host="prowlarr",
            config_dir=prowlarr_config_dir,
            default_port=self.INTERNAL_PORT,
        )

        service_config_dir = get_service_config_dir(service_name, config)
        service_url = self._build_service_url(
            service_name=service_name,
            host=service_name,
            config_dir=service_config_dir,
            default_port=self._default_service_port(service_name),
        )

        desired_fields = {
            "prowlarrUrl": prowlarr_url,
            "baseUrl": service_url,
            "apiKey": api_key,
        }
        normalized_targets = {
            key: self._normalize_base_url(value)
            for key, value in desired_fields.items()
            if key != "apiKey"
        }

        existing = api.get_json("/applications")
        for entry in existing:
            if (entry.get("implementation") or "").lower() == implementation.lower():
                app_id = entry.get("id")
                fields = {f["name"]: f.get("value") for f in entry.get("fields", [])}
                current_base_url = self._normalize_base_url(fields.get("baseUrl"))
                current_prowlarr_url = self._normalize_base_url(fields.get("prowlarrUrl"))
                if (
                    current_base_url == normalized_targets.get("baseUrl")
                    and current_prowlarr_url == normalized_targets.get("prowlarrUrl")
                    and fields.get("apiKey") == api_key
                ):
                    return False, f"application {display_name} ready"

                updated = dict(entry)
                overrides = dict(desired_fields)
                updated["fields"] = set_field_values(entry.get("fields", []), overrides)
                api.put_json(f"/applications/{app_id}", updated)
                return True, f"updated {display_name} application"

        schema = api.get_json("/applications/schema")
        template = next(
            (
                item
                for item in schema
                if (item.get("implementation") or "").lower() == implementation.lower()
            ),
            None,
        )
        if not template:
            return False, f"schema for {display_name} not found"

        overrides = dict(desired_fields)
        payload: Dict[str, object] = {
            key: value
            for key, value in template.items()
            if key not in {"fields", "id", "protocol"}
        }
        payload.update(
            {
                "name": display_name,
                "implementation": template.get("implementation", display_name),
                "implementationName": template.get("implementationName", display_name),
                "protocol": template.get("protocol", "torrent"),
                "configContract": template.get("configContract"),
                "enable": True,
                "syncProfileId": template.get("syncProfileId", 1),
                "tags": [],
            }
        )
        payload["fields"] = set_field_values(template.get("fields", []), overrides)
        api.post_json("/applications", payload)
        return True, f"created {display_name} application"


    def _build_service_url(
        self,
        *,
        service_name: str,
        host: str,
        config_dir: Path,
        default_port: int,
    ) -> str:
        port = read_arr_port(config_dir) or default_port
        url_base = read_arr_url_base(config_dir)
        base = f"http://{host}:{port}"
        if url_base:
            base = f"{base}/{url_base}"
        return base

    @staticmethod
    def _default_service_port(service_name: str) -> int:
        mapping = {
            "radarr": 7878,
            "sonarr": 8989,
            "prowlarr": 9696,
        }
        return mapping.get(service_name.lower(), 80)

    @staticmethod
    def _normalize_base_url(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        sanitized = str(value).strip()
        if sanitized in {"", "/"}:
            return None
        sanitized = sanitized.rstrip("/")
        return sanitized or None

    # ------------------------------------------------------------------ indexer management

    def get_available_indexers(self, config: StackConfig) -> List[IndexerSchema]:
        """Get list of available public indexer schemas from Prowlarr."""
        state = self.repo.load_state()
        secrets_state = state.get("secrets", {})
        prowlarr_secrets = secrets_state.get("prowlarr", {})
        api_key = prowlarr_secrets.get("api_key")

        if not api_key:
            log.warning("Prowlarr API key not available")
            return []

        prowlarr_cfg = config.services.prowlarr
        # Use localhost with configured port to work when orchestrator is in a container
        base_url = f"http://prowlarr:{self.INTERNAL_PORT}/api/v1"

        try:
            with ArrAPI(base_url, api_key, timeout=30.0) as api:
                schemas = api.get_json("/indexer/schema") or []

                available = []
                for schema in schemas:
                    # Only include public indexers (no authentication required)
                    privacy = schema.get("privacy", "").lower()
                    if privacy != "public":
                        continue

                    # Extract category info
                    caps = schema.get("capabilities", {})
                    categories = caps.get("categories", [])

                    available.append(IndexerSchema(
                        id=schema.get("id", 0),
                        name=schema.get("name", ""),
                        description=schema.get("description"),
                        encoding=schema.get("encoding"),
                        language=schema.get("language"),
                        privacy=privacy,
                        protocol=schema.get("protocol", "torrent"),
                        categories=categories,
                        supports_rss=caps.get("supportsRss", False),
                        supports_search=caps.get("supportsSearch", False),
                    ))

                return sorted(available, key=lambda x: x.name.lower())

        except httpx.RequestError as exc:
            log.warning(f"Failed to fetch indexer schemas: {exc}")
            return []
        except httpx.HTTPStatusError as exc:
            log.warning(f"Prowlarr API error fetching schemas: {exc.response.status_code}")
            return []

    def get_configured_indexers(self, config: StackConfig) -> List[IndexerInfo]:
        """Get list of currently configured indexers."""
        state = self.repo.load_state()
        secrets_state = state.get("secrets", {})
        prowlarr_secrets = secrets_state.get("prowlarr", {})
        api_key = prowlarr_secrets.get("api_key")

        if not api_key:
            return []

        prowlarr_cfg = config.services.prowlarr
        # Use localhost with configured port to work when orchestrator is in a container
        base_url = f"http://prowlarr:{self.INTERNAL_PORT}/api/v1"

        try:
            with ArrAPI(base_url, api_key) as api:
                indexers = api.get_json("/indexer") or []

                return [
                    IndexerInfo(
                        id=idx.get("id", 0),
                        name=idx.get("name", ""),
                        implementation=idx.get("implementation", ""),
                        enable=idx.get("enable", True),
                        priority=idx.get("priority", 25),
                        protocol=idx.get("protocol", "torrent"),
                    )
                    for idx in indexers
                ]

        except httpx.RequestError as exc:
            log.warning(f"Failed to fetch configured indexers: {exc}")
            return []
        except httpx.HTTPStatusError as exc:
            log.warning(f"Prowlarr API error: {exc.response.status_code}")
            return []

    def add_indexers(self, config: StackConfig, indexer_names: List[str]) -> Tuple[List[str], List[str]]:
        """Add indexers by their definition names.

        Returns a tuple of (added, failed) indexer names.
        """
        state = self.repo.load_state()
        secrets_state = state.get("secrets", {})
        prowlarr_secrets = secrets_state.get("prowlarr", {})
        api_key = prowlarr_secrets.get("api_key")

        if not api_key:
            return [], indexer_names

        prowlarr_cfg = config.services.prowlarr
        # Use localhost with configured port to work when orchestrator is in a container
        base_url = f"http://prowlarr:{self.INTERNAL_PORT}/api/v1"

        added: List[str] = []
        failed: List[str] = []

        try:
            with ArrAPI(base_url, api_key, timeout=30.0) as api:
                # Get schemas to find matching indexers
                schemas = api.get_json("/indexer/schema") or []

                # Get existing indexers to avoid duplicates
                existing = api.get_json("/indexer") or []
                existing_names = {idx.get("name", "").lower() for idx in existing}
                existing_impls = {idx.get("implementation", "").lower() for idx in existing}

                for name in indexer_names:
                    name_lower = name.lower()

                    # Find matching schema
                    schema = next(
                        (s for s in schemas if s.get("name", "").lower() == name_lower),
                        None
                    )

                    if not schema:
                        log.warning(f"Indexer schema not found: {name}")
                        failed.append(name)
                        continue

                    # Check if already exists
                    if name_lower in existing_names or name_lower in existing_impls:
                        log.info(f"Indexer already configured: {name}")
                        added.append(name)  # Count as success since it exists
                        continue

                    # Build payload from schema
                    payload = self._build_indexer_payload(schema)

                    try:
                        api.post_json("/indexer", payload)
                        added.append(name)
                        log.info(f"Added indexer: {name}")
                    except httpx.HTTPStatusError as exc:
                        log.warning(f"Failed to add indexer {name}: {exc.response.text}")
                        failed.append(name)

        except httpx.RequestError as exc:
            log.error(f"Failed to connect to Prowlarr: {exc}")
            return added, list(set(indexer_names) - set(added))
        except httpx.HTTPStatusError as exc:
            log.error(f"Prowlarr API error: {exc.response.status_code}")
            return added, list(set(indexer_names) - set(added))

        return added, failed

    def _build_indexer_payload(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Build an indexer creation payload from a schema."""
        # Copy relevant fields from schema
        payload: Dict[str, Any] = {
            "name": schema.get("name", ""),
            "implementation": schema.get("implementation", schema.get("name", "")),
            "implementationName": schema.get("implementationName", schema.get("name", "")),
            "configContract": schema.get("configContract", ""),
            "protocol": schema.get("protocol", "torrent"),
            "privacy": schema.get("privacy", "public"),
            "enable": True,
            "priority": 25,
            "appProfileId": 1,
            "tags": [],
        }

        # Copy fields with their default values
        fields = schema.get("fields", [])
        payload["fields"] = [
            {
                "name": f.get("name"),
                "value": f.get("value") if "value" in f else f.get("default"),
            }
            for f in fields
            if f.get("name")
        ]

        return payload

    def remove_indexer(self, config: StackConfig, indexer_id: int) -> bool:
        """Remove an indexer by ID."""
        state = self.repo.load_state()
        secrets_state = state.get("secrets", {})
        prowlarr_secrets = secrets_state.get("prowlarr", {})
        api_key = prowlarr_secrets.get("api_key")

        if not api_key:
            return False

        prowlarr_cfg = config.services.prowlarr
        # Use localhost with configured port to work when orchestrator is in a container
        base_url = f"http://prowlarr:{self.INTERNAL_PORT}/api/v1"

        try:
            with ArrAPI(base_url, api_key) as api:
                response = api._client.delete(f"/indexer/{indexer_id}")
                response.raise_for_status()
                return True
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            log.warning(f"Failed to remove indexer {indexer_id}: {exc}")
            return False

    # ------------------------------------------------------------------ auto-population

    # Prowlarr category IDs for Movies and TV
    CATEGORY_MOVIES = 2000
    CATEGORY_TV = 5000

    # Map ISO 639-2 (3-letter) language codes to Prowlarr language patterns
    # Prowlarr uses formats like "en-US", "en-GB", "ja-JP", "de-DE", etc.
    LANGUAGE_MAP = {
        "eng": ["en-"],      # English: matches en-US, en-GB, en-AU, etc.
        "jpn": ["ja-"],      # Japanese
        "spa": ["es-"],      # Spanish
        "fre": ["fr-"],      # French
        "ger": ["de-"],      # German
        "ita": ["it-"],      # Italian
        "por": ["pt-"],      # Portuguese
        "rus": ["ru-"],      # Russian
        "chi": ["zh-"],      # Chinese
        "kor": ["ko-"],      # Korean
        "ara": ["ar-"],      # Arabic
        "hin": ["hi-"],      # Hindi
        "pol": ["pl-"],      # Polish
        "dut": ["nl-"],      # Dutch
        "swe": ["sv-"],      # Swedish
        "nor": ["no-", "nb-", "nn-"],  # Norwegian
        "dan": ["da-"],      # Danish
        "fin": ["fi-"],      # Finnish
        "tur": ["tr-"],      # Turkish
        "vie": ["vi-"],      # Vietnamese
        "tha": ["th-"],      # Thai
        "ind": ["id-"],      # Indonesian
        "und": [],           # Undefined - matches any language
    }

    def auto_populate_indexers(self, config: StackConfig) -> Tuple[List[str], List[str], List[str]]:
        """Auto-populate indexers based on user preferences.

        Filters public indexers that:
        - Support Movies (2000) and/or TV (5000) categories
        - Optionally match the user's language preferences (based on language_filter setting)

        Returns a tuple of (added, skipped, failed) indexer names.
        - added: Successfully added indexers
        - skipped: Indexers that were already configured
        - failed: Indexers that failed to add
        """
        state = self.repo.load_state()
        secrets_state = state.get("secrets", {})
        prowlarr_secrets = secrets_state.get("prowlarr", {})
        api_key = prowlarr_secrets.get("api_key")

        if not api_key:
            log.warning("Prowlarr API key not available for auto-population")
            return [], [], []

        prowlarr_cfg = config.services.prowlarr
        # Use localhost with configured port to work when orchestrator is in a container
        base_url = f"http://prowlarr:{self.INTERNAL_PORT}/api/v1"

        # Check if language filtering is enabled
        language_filter = config.services.prowlarr.language_filter

        # Extract user language preferences from media_policy
        user_languages = self._extract_user_languages(config)
        if language_filter:
            log.info(f"Auto-populating indexers for languages: {user_languages}")
        else:
            log.info("Auto-populating all public indexers (language filter disabled)")

        try:
            with ArrAPI(base_url, api_key, timeout=120.0) as api:
                # Get all indexer schemas
                schemas = api.get_json("/indexer/schema") or []
                log.info(f"Fetched {len(schemas)} indexer schemas from Prowlarr")

                # Get existing indexers to check for duplicates
                existing = api.get_json("/indexer") or []
                existing_names = {idx.get("name", "").lower() for idx in existing}
                existing_impls = {idx.get("implementation", "").lower() for idx in existing}

                # Filter schemas to find matching indexers
                candidates = self._filter_indexer_candidates(
                    schemas, user_languages, language_filter=language_filter
                )
                log.info(f"Found {len(candidates)} candidate indexers matching criteria")

                added: List[str] = []
                skipped: List[str] = []
                failed: List[str] = []

                for schema in candidates:
                    name = schema.get("name", "")
                    name_lower = name.lower()

                    # Check if already exists
                    if name_lower in existing_names or name_lower in existing_impls:
                        log.debug(f"Indexer already configured: {name}")
                        skipped.append(name)
                        continue

                    # Build payload and add indexer
                    payload = self._build_indexer_payload(schema)

                    try:
                        api.post_json("/indexer", payload)
                        added.append(name)
                        log.info(f"Added indexer: {name}")
                        # Update existing sets to prevent duplicates in same batch
                        existing_names.add(name_lower)
                    except httpx.HTTPStatusError as exc:
                        log.warning(f"Failed to add indexer {name}: {exc.response.text}")
                        failed.append(name)

                return added, skipped, failed

        except httpx.RequestError as exc:
            log.error(f"Failed to connect to Prowlarr: {exc}")
            return [], [], []
        except httpx.HTTPStatusError as exc:
            log.error(f"Prowlarr API error: {exc.response.status_code}")
            return [], [], []

    def _extract_user_languages(self, config: StackConfig) -> List[str]:
        """Extract unique language codes from user's media policy."""
        languages = set()

        # Get languages from movies policy
        if hasattr(config, 'media_policy') and config.media_policy:
            movies_audio = config.media_policy.movies.keep_audio
            languages.update(movies_audio)

        # Remove 'und' (undefined) from the set for filtering purposes
        # but we'll still match multi-language indexers
        languages.discard("und")

        return list(languages) if languages else ["eng"]  # Default to English

    def _filter_indexer_candidates(
        self,
        schemas: List[Dict[str, Any]],
        user_languages: List[str],
        language_filter: bool = True,
    ) -> List[Dict[str, Any]]:
        """Filter indexer schemas to find candidates matching user preferences.

        Args:
            schemas: List of indexer schemas from Prowlarr
            user_languages: List of user's preferred language codes (e.g., ["eng", "jpn"])
            language_filter: If True, only include indexers matching user languages.
                           If False, include all public indexers with Movies/TV categories.
        """
        candidates = []

        for schema in schemas:
            # Only include public indexers
            privacy = schema.get("privacy", "").lower()
            if privacy != "public":
                continue

            # Check if indexer supports Movies or TV categories
            caps = schema.get("capabilities", {})
            categories = caps.get("categories", [])
            category_ids = {cat.get("id") for cat in categories if isinstance(cat, dict)}

            # Must support either Movies (2000) or TV (5000)
            supports_movies = self.CATEGORY_MOVIES in category_ids
            supports_tv = self.CATEGORY_TV in category_ids

            if not (supports_movies or supports_tv):
                continue

            # Check language compatibility (only if language filter is enabled)
            if language_filter:
                indexer_language = schema.get("language", "")
                if not self._language_matches(indexer_language, user_languages):
                    continue

            # Check that indexer supports at least RSS or search
            # Note: supportsRss/supportsSearch are at the schema level, not inside capabilities
            supports_rss = schema.get("supportsRss", False)
            supports_search = schema.get("supportsSearch", False)

            if not (supports_rss or supports_search):
                continue

            candidates.append(schema)

        return candidates

    def _language_matches(self, indexer_language: str, user_languages: List[str]) -> bool:
        """Check if indexer language matches any of the user's preferred languages."""
        if not indexer_language:
            # No language specified - include it (likely multi-language)
            return True

        indexer_lang_lower = indexer_language.lower()

        for user_lang in user_languages:
            patterns = self.LANGUAGE_MAP.get(user_lang, [])

            # If language code not in map, try direct prefix match
            if not patterns:
                # Try matching first 2 chars of ISO code to language tag
                if indexer_lang_lower.startswith(user_lang[:2]):
                    return True
                continue

            # Check if indexer language starts with any of the patterns
            for pattern in patterns:
                if indexer_lang_lower.startswith(pattern.lower()):
                    return True

        return False

