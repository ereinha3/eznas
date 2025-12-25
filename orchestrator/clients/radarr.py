"""Radarr automation client."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from .arr import ArrAPI, describe_changes, set_field_values
from .base import EnsureOutcome, ServiceClient
from .util import read_arr_api_key, wait_for_arr_config
from ..models import StackConfig
from ..storage import ConfigRepository

log = logging.getLogger(__name__)


class RadarrClient(ServiceClient):
    """Provision and configure Radarr via its HTTP API."""

    name = "radarr"

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    def ensure(self, config: StackConfig) -> EnsureOutcome:
        state = self.repo.load_state()
        services_state = state.setdefault("services", {})
        radarr_state = services_state.setdefault("radarr", {})
        secrets_state = state.setdefault("secrets", {})
        radarr_secrets = secrets_state.setdefault("radarr", {})
        qb_secrets = secrets_state.setdefault("qbittorrent", {})

        detail_messages: List[str] = []
        changed = False
        state_dirty = False

        config_dir = Path(config.paths.appdata) / "radarr"
        config_dir.mkdir(parents=True, exist_ok=True)
        api_key = radarr_secrets.get("api_key")
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
                    detail=f"Radarr API key missing in config.xml at {config_dir}",
                    changed=False,
                    success=False,
                )
            radarr_secrets["api_key"] = api_key
            state_dirty = True
            detail_messages.append("stored API key")

        if state_dirty:
            self.repo.save_state(state)
            state_dirty = False

        radarr_cfg = config.services.radarr
        base_url = f"http://127.0.0.1:{radarr_cfg.port}/api/v3"
        try:
            with ArrAPI(base_url, api_key) as api:
                status = api.get_json("/system/status")
                version = status.get("version")
                detail_messages.append(
                    f"online (v{version})" if version else "online"
                )

                qb_username = qb_secrets.get("username", config.services.qbittorrent.username)
                qb_password = qb_secrets.get("password", config.services.qbittorrent.password)

                rf_changed, rf_msg, folder_id = self._ensure_root_folder(api, config)
                dl_changed, dl_msg, client_id = self._ensure_download_client(
                    api, config, qb_username, qb_password
                )
                changed_any, aggregated = describe_changes(
                    [(rf_changed, rf_msg), (dl_changed, dl_msg)]
                )
                if aggregated:
                    detail_messages.append(aggregated)
                changed = changed or changed_any

                if folder_id is not None:
                    radarr_state["root_folder_id"] = folder_id
                    state_dirty = True
                if client_id is not None:
                    radarr_state["download_client_id"] = client_id
                    state_dirty = True

        except httpx.RequestError as exc:
            log.debug("Radarr request error", exc_info=True)
            return EnsureOutcome(
                detail=f"Radarr unreachable at {base_url}: {exc}",
                changed=changed,
                success=False,
            )
        except httpx.HTTPStatusError as exc:
            log.debug("Radarr API error", exc_info=True)
            return EnsureOutcome(
                detail=f"Radarr API error {exc.response.status_code}: {exc.response.text}",
                changed=changed,
                success=False,
            )

        if state_dirty:
            self.repo.save_state(state)

        return EnsureOutcome(
            detail="; ".join(detail_messages) if detail_messages else "ok",
            changed=changed,
            success=True,
        )

    # ------------------------------------------------------------------ helpers

    def _ensure_root_folder(
        self, api: ArrAPI, config: StackConfig
    ) -> Tuple[bool, str, Optional[int]]:
        target = "/data/media/movies"
        existing = api.get_json("/rootfolder")
        for entry in existing:
            if entry.get("path") == target:
                return False, f"root folder ready {target}", entry.get("id")

        profiles = api.get_json("/qualityprofile")
        try:
            meta_profiles = api.get_json("/metadataprofile")
        except httpx.HTTPStatusError:
            meta_profiles = []
        quality_id = profiles[0]["id"] if profiles else 1
        metadata_id = meta_profiles[0]["id"] if meta_profiles else 1

        payload: Dict[str, object] = {
            "path": target,
            "name": Path(target).name or "Movies",
            "defaultQualityProfileId": quality_id,
            "defaultMetadataProfileId": metadata_id,
            "defaultTags": [],
        }
        created = api.post_json("/rootfolder", payload)
        folder_id = created.get("id") if isinstance(created, dict) else None
        return True, f"root folder created {target}", folder_id

    def _ensure_download_client(
        self,
        api: ArrAPI,
        config: StackConfig,
        username: str,
        password: str,
    ) -> Tuple[bool, str, Optional[int]]:
        desired_fields = {
            "host": "qbittorrent",
            "port": config.services.qbittorrent.port,
            "useSsl": False,
            "urlBase": "",
            "username": username,
            "password": password,
            "category": config.download_policy.categories.radarr,
        }

        clients = api.get_json("/downloadclient")
        for client in clients:
            if (client.get("implementation") or "").lower() != "qbittorrent":
                continue
            client_id = client.get("id")
            current = {f["name"]: f.get("value") for f in client.get("fields", [])}
            if (
                current.get("host") == "qbittorrent"
                and str(current.get("port")) == str(config.services.qbittorrent.port)
                and current.get("category") == config.download_policy.categories.radarr
            ):
                return False, "download client ready", client_id

            updated = dict(client)
            updated["enable"] = True
            updated["fields"] = set_field_values(client.get("fields", []), desired_fields)
            api.put_json(f"/downloadclient/{client_id}", updated)
            return True, f"updated download client {client_id}", client_id

        schema = api.get_json("/downloadclient/schema")
        template = next(
            (
                entry
                for entry in schema
                if (entry.get("implementation") or "").lower() == "qbittorrent"
            ),
            None,
        )
        if not template:
            return False, "qBittorrent schema unavailable", None

        payload = {
            "name": "qBittorrent",
            "implementation": template.get("implementation", "QBitTorrent"),
            "implementationName": template.get("implementationName", "qBittorrent"),
            "protocol": template.get("protocol", "torrent"),
            "configContract": template.get("configContract", "QBitTorrentSettings"),
            "enable": True,
            "priority": 1,
            "removeCompletedDownloads": True,
            "fields": set_field_values(template.get("fields", []), desired_fields),
            "tags": [],
        }
        created = api.post_json("/downloadclient", payload)
        client_id = created.get("id") if isinstance(created, dict) else None
        return True, "created download client", client_id
