"""Prowlarr automation client."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from .arr import ArrAPI, set_field_values, wait_for_http_ready
from .base import EnsureOutcome, ServiceClient
from .util import read_arr_api_key, read_arr_port, read_arr_url_base, wait_for_arr_config
from ..models import StackConfig
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

        config_dir = Path(config.paths.appdata) / "prowlarr"
        config_dir.mkdir(parents=True, exist_ok=True)
        api_key = prowlarr_secrets.get("api_key")
        if not api_key:
            if not wait_for_arr_config(config_dir):
                return EnsureOutcome(
                    detail=f"config.xml did not appear at {config_dir}",
                    changed=False,
                    success=False,
                )
            api_key = read_arr_api_key(config_dir)
            if not api_key:
                return EnsureOutcome(
                    detail=f"Prowlarr API key missing in config.xml at {config_dir}",
                    changed=False,
                    success=False,
                )
            prowlarr_secrets["api_key"] = api_key
            state_dirty = True
            detail_messages.append("stored API key")

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

        base_url = f"http://127.0.0.1:{prowlarr_cfg.port}/api/v1"
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
        try:
            with ArrAPI(base_url, api_key) as api:
                status = api.get_json("/system/status")
                version = status.get("version")
                detail_messages.append(
                    f"online (v{version})" if version else "online"
                )

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

        except httpx.RequestError as exc:
            log.debug("Prowlarr request error", exc_info=True)
            return EnsureOutcome(
                detail=f"Prowlarr unreachable at {base_url}: {exc}",
                changed=changed,
                success=False,
            )
        except httpx.HTTPStatusError as exc:
            log.debug("Prowlarr API error", exc_info=True)
            return EnsureOutcome(
                detail=f"Prowlarr API error {exc.response.status_code}: {exc.response.text}",
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
        base_url = f"http://127.0.0.1:{prowlarr_cfg.port}/api/v1"

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
            prowlarr_config_dir = Path(config.paths.appdata) / "prowlarr"
            prowlarr_url = self._build_service_url(
                service_name="prowlarr",
                host="prowlarr",
                config_dir=prowlarr_config_dir,
                default_port=self.INTERNAL_PORT,
            )
            service_config_dir = Path(config.paths.appdata) / service_name
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
        prowlarr_config_dir = Path(config.paths.appdata) / "prowlarr"
        prowlarr_url = self._build_service_url(
            service_name="prowlarr",
            host="prowlarr",
            config_dir=prowlarr_config_dir,
            default_port=self.INTERNAL_PORT,
        )

        service_config_dir = Path(config.paths.appdata) / service_name
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

