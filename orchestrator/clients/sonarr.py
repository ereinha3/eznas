"""Sonarr automation client."""
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


class SonarrClient(ServiceClient):
    """Provision and configure Sonarr via its HTTP API."""

    name = "sonarr"

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    def ensure(self, config: StackConfig) -> EnsureOutcome:
        state = self.repo.load_state()
        services_state = state.setdefault("services", {})
        sonarr_state = services_state.setdefault("sonarr", {})
        secrets_state = state.setdefault("secrets", {})
        sonarr_secrets = secrets_state.setdefault("sonarr", {})
        qb_secrets = secrets_state.setdefault("qbittorrent", {})

        detail_messages: List[str] = []
        changed = False
        state_dirty = False

        config_dir = Path(config.paths.appdata) / "sonarr"
        config_dir.mkdir(parents=True, exist_ok=True)
        api_key = sonarr_secrets.get("api_key")
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
                    detail=f"Sonarr API key missing in config.xml at {config_dir}",
                    changed=False,
                    success=False,
                )
            sonarr_secrets["api_key"] = api_key
            state_dirty = True
            detail_messages.append("stored API key")

        if state_dirty:
            self.repo.save_state(state)
            state_dirty = False

        sonarr_cfg = config.services.sonarr
        base_url = f"http://127.0.0.1:{sonarr_cfg.port}/api/v3"
        try:
            with ArrAPI(base_url, api_key) as api:
                status = api.get_json("/system/status")
                version = status.get("version")
                detail_messages.append(
                    f"online (v{version})" if version else "online"
                )

                rf_changes = []
                tv_changed, tv_msg, tv_id = self._ensure_root_folder(
                    api, "/data/media/tv", "Standard"
                )
                rf_changes.append((tv_changed, tv_msg))
                if tv_id is not None:
                    sonarr_state["root_tv_id"] = tv_id
                    state_dirty = True

                anime_changed, anime_msg, anime_id = self._ensure_root_folder(
                    api, "/data/media/anime", "Anime"
                )
                rf_changes.append((anime_changed, anime_msg))
                if anime_id is not None:
                    sonarr_state["root_anime_id"] = anime_id
                    state_dirty = True

                qb_username = qb_secrets.get("username", config.services.qbittorrent.username)
                qb_password = qb_secrets.get("password", config.services.qbittorrent.password)
                dl_changed, dl_msg, client_id = self._ensure_download_client(
                    api, config, qb_username, qb_password
                )
                changed_any, aggregated = describe_changes(rf_changes + [(dl_changed, dl_msg)])
                if aggregated:
                    detail_messages.append(aggregated)
                changed = changed or changed_any
                if client_id is not None:
                    sonarr_state["download_client_id"] = client_id
                    state_dirty = True

        except httpx.RequestError as exc:
            log.debug("Sonarr request error", exc_info=True)
            return EnsureOutcome(
                detail=f"Sonarr unreachable at {base_url}: {exc}",
                changed=changed,
                success=False,
            )
        except httpx.HTTPStatusError as exc:
            log.debug("Sonarr API error", exc_info=True)
            return EnsureOutcome(
                detail=f"Sonarr API error {exc.response.status_code}: {exc.response.text}",
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
        self,
        api: ArrAPI,
        target: str,
        default_profile_name: str,
    ) -> Tuple[bool, str, Optional[int]]:
        existing = api.get_json("/rootfolder")
        for entry in existing:
            if entry.get("path") == target:
                return False, f"root folder ready {target}", entry.get("id")

        quality_profiles = api.get_json("/qualityprofile")
        try:
            language_profiles = api.get_json("/languageprofile")
        except httpx.HTTPStatusError:
            language_profiles = []
        default_quality = self._find_profile_id(quality_profiles, default_profile_name)
        default_language = language_profiles[0]["id"] if language_profiles else 1

        payload: Dict[str, object] = {
            "path": target,
            "name": Path(target).name or "Series",
            "defaultQualityProfileId": default_quality,
            "defaultLanguageProfileId": default_language,
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
        category = config.download_policy.categories.sonarr
        desired_fields = {
            "host": "qbittorrent",
            "port": config.services.qbittorrent.port,
            "useSsl": False,
            "urlBase": "",
            "username": username,
            "password": password,
            "category": category,
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
                and current.get("category") == config.download_policy.categories.sonarr
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
            "configContract": template.get("configContract", "QbittorrentSettings"),
            "enable": True,
            "priority": 1,
            "removeCompletedDownloads": True,
            "fields": set_field_values(template.get("fields", []), desired_fields),
            "tags": [],
        }
        created = api.post_json("/downloadclient", payload)
        client_id = created.get("id") if isinstance(created, dict) else None
        return True, "created download client", client_id

    @staticmethod
    def _find_profile_id(profiles: List[Dict[str, object]], name: str) -> int:
        for profile in profiles:
            if str(profile.get("name", "")) == name:
                return int(profile.get("id", 1))
        return int(profiles[0]["id"]) if profiles else 1
