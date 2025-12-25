"""Prowlarr automation client."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Tuple

import httpx

from .arr import ArrAPI, set_field_values
from .base import EnsureOutcome, ServiceClient
from .util import read_arr_api_key, wait_for_arr_config
from ..models import StackConfig
from ..storage import ConfigRepository

log = logging.getLogger(__name__)


class ProwlarrClient(ServiceClient):
    """Provision and link Prowlarr applications."""

    name = "prowlarr"

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
        try:
            with ArrAPI(base_url, api_key) as api:
                status = api.get_json("/system/status")
                version = status.get("version")
                detail_messages.append(
                    f"online (v{version})" if version else "online"
                )

                app_changes: List[Tuple[bool, str]] = []
                if radarr_cfg.enabled and radarr_key and radarr_cfg.port:
                    changed_app, msg = self._ensure_application(
                        api,
                        "Radarr",
                        radarr_key,
                        host="radarr",
                        port=radarr_cfg.port,
                    )
                    app_changes.append((changed_app, msg))
                if sonarr_cfg.enabled and sonarr_key and sonarr_cfg.port:
                    changed_app, msg = self._ensure_application(
                        api,
                        "Sonarr",
                        sonarr_key,
                        host="sonarr",
                        port=sonarr_cfg.port,
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

    # ------------------------------------------------------------------ helpers

    def _ensure_application(
        self,
        api: ArrAPI,
        name: str,
        api_key: str,
        *,
        host: str,
        port: int,
    ) -> Tuple[bool, str]:
        implementation = name
        existing = api.get_json("/applications")
        for entry in existing:
            if (entry.get("implementation") or "").lower() == implementation.lower():
                app_id = entry.get("id")
                fields = {f["name"]: f.get("value") for f in entry.get("fields", [])}
                if (
                    fields.get("host") == host
                    and str(fields.get("port")) == str(port)
                    and fields.get("apiKey") == api_key
                    and (fields.get("baseUrl") or "/") == "/"
                ):
                    return False, f"application {name} ready"

                updated = dict(entry)
                overrides = {
                    "host": host,
                    "port": port,
                    "apiKey": api_key,
                    "baseUrl": "/",
                }
                updated["fields"] = set_field_values(entry.get("fields", []), overrides)
                api.put_json(f"/applications/{app_id}", updated)
                return True, f"updated {name} application"

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
            return False, f"schema for {name} not found"

        overrides = {
            "host": host,
            "port": port,
            "apiKey": api_key,
            "baseUrl": "/",
        }
        payload: Dict[str, object] = {
            key: value
            for key, value in template.items()
            if key not in {"fields", "id", "protocol"}
        }
        payload.update(
            {
                "name": name,
                "implementation": template.get("implementation", name),
                "implementationName": template.get("implementationName", name),
                "protocol": template.get("protocol", "torrent"),
                "configContract": template.get("configContract"),
                "enable": True,
                "syncProfileId": template.get("syncProfileId", 1),
                "tags": [],
            }
        )
        payload["fields"] = set_field_values(template.get("fields", []), overrides)
        api.post_json("/applications", payload)
        return True, f"created {name} application"
