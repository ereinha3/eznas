"""Sonarr automation client."""
from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx

from .arr import ArrAPI, describe_changes, set_field_values, wait_for_http_ready
from .base import EnsureOutcome, ServiceClient
from .qb import QBittorrentClient
from .util import arr_password_matches, read_arr_api_key, wait_for_arr_config
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

        ui_username = sonarr_secrets.get("ui_username")
        if not ui_username:
            ui_username = "sonarr-admin"
            sonarr_secrets["ui_username"] = ui_username
            state_dirty = True

        ui_password = sonarr_secrets.get("ui_password")
        if not ui_password:
            ui_password = secrets.token_urlsafe(12)
            sonarr_secrets["ui_password"] = ui_password
            state_dirty = True

        if state_dirty:
            self.repo.save_state(state)
            state_dirty = False

        sonarr_cfg = config.services.sonarr
        db_path = config_dir / "sonarr.db"

        base_url = f"http://127.0.0.1:{sonarr_cfg.port}/api/v3"
        status_url = f"{base_url}/system/status"
        ok, status_detail = wait_for_http_ready(
            status_url,
            timeout=180.0,
            interval=5.0,
        )
        if not ok:
            return EnsureOutcome(
                detail=f"Sonarr not ready ({status_detail})",
                changed=changed,
                success=False,
            )
        try:
            host_changed = self._ensure_host_settings(
                base_url=base_url,
                port=sonarr_cfg.port,
                api_key=api_key,
                db_path=db_path,
                username=ui_username,
                password=ui_password,
            )
            if host_changed:
                detail_messages.append("ui credentials synced")
                changed = True
            with ArrAPI(base_url, api_key) as api:
                status = api.get_json("/system/status")
                version = status.get("version")
                detail_messages.append(
                    f"online (v{version})" if version else "online"
                )

                host_config = api.get_json("/config/host")
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
                    detail_messages.append("ui credentials synced")
                    changed = True

                rf_changes = []
                tv_changed, tv_msg, tv_id = self._ensure_root_folder(
                    api, config, "/data/media/tv"
                )
                rf_changes.append((tv_changed, tv_msg))
                if tv_id is not None:
                    sonarr_state["root_tv_id"] = tv_id
                    state_dirty = True

                anime_changed, anime_msg, anime_id = self._ensure_root_folder(
                    api, config, "/data/media/anime", anime=True
                )
                rf_changes.append((anime_changed, anime_msg))
                if anime_id is not None:
                    sonarr_state["root_anime_id"] = anime_id
                    state_dirty = True

                qb_username = qb_secrets.get("username", config.services.qbittorrent.username)
                qb_password = qb_secrets.get("password", config.services.qbittorrent.password)
                prev_dl_username = sonarr_state.get("download_client_username")
                prev_dl_password = sonarr_state.get("download_client_password")
                dl_changed, dl_msg, client_id = self._ensure_download_client(
                    api,
                    config,
                    qb_username,
                    qb_password,
                    prev_dl_username,
                    prev_dl_password,
                )
                changed_any, aggregated = describe_changes(rf_changes + [(dl_changed, dl_msg)])
                if aggregated:
                    detail_messages.append(aggregated)
                changed = changed or changed_any
                if client_id is not None:
                    sonarr_state["download_client_id"] = client_id
                    state_dirty = True
                if sonarr_state.get("download_client_username") != qb_username:
                    sonarr_state["download_client_username"] = qb_username
                    state_dirty = True
                if sonarr_state.get("download_client_password") != qb_password:
                    sonarr_state["download_client_password"] = qb_password
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
        except RuntimeError as exc:
            return EnsureOutcome(
                detail=f"host settings sync failed ({exc})",
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

    def verify(self, config: StackConfig) -> EnsureOutcome:
        state = self.repo.load_state()
        secrets_state = state.get("secrets", {})
        sonarr_secrets = secrets_state.get("sonarr", {})
        qb_secrets = secrets_state.get("qbittorrent", {})

        api_key = sonarr_secrets.get("api_key")
        if not api_key:
            return EnsureOutcome(detail="missing api key", changed=False, success=False)

        sonarr_cfg = config.services.sonarr
        base_url = f"http://127.0.0.1:{sonarr_cfg.port}/api/v3"

        qb_username = qb_secrets.get("username", config.services.qbittorrent.username)
        desired_fields = {
            "host": "qbittorrent",
            "port": QBittorrentClient.INTERNAL_PORT,
            "useSsl": False,
            "urlBase": "",
            "username": qb_username,
            "category": config.download_policy.categories.sonarr,
        }

        try:
            with ArrAPI(base_url, api_key) as api:
                clients = api.get_json("/downloadclient") or []
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

        for client in clients:
            if (client.get("implementation") or "").lower() != "qbittorrent":
                continue
            current = {f["name"]: f.get("value") for f in client.get("fields", [])}
            mismatches = []
            for key, expected in desired_fields.items():
                if str(current.get(key)) != str(expected):
                    mismatches.append(f"{key}={current.get(key)}")
            if mismatches:
                return EnsureOutcome(
                    detail="download client mismatch: " + ", ".join(mismatches),
                    changed=False,
                    success=False,
                )
            return EnsureOutcome(detail="download client ok", changed=False, success=True)

        return EnsureOutcome(
            detail="download client missing (qbittorrent)",
            changed=False,
            success=False,
        )

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
        import httpx

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
            f"http://127.0.0.1:{port}/api/v3/system/status",
            timeout=120.0,
            interval=5.0,
        )
        if not ok:
            raise RuntimeError(message)
        return True


    def _ensure_root_folder(
        self,
        api: ArrAPI,
        config: StackConfig,
        target: str,
        anime: bool = False,
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
        default_quality = self._select_quality_profile_id(quality_profiles, config)
        preferred_languages = (
            config.media_policy.anime.keep_audio if anime else config.media_policy.movies.keep_audio
        )
        default_language = self._select_language_profile_id(language_profiles, preferred_languages)

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
        previous_username: Optional[str],
        previous_password: Optional[str],
    ) -> Tuple[bool, str, Optional[int]]:
        category = config.download_policy.categories.sonarr
        desired_fields = {
            "host": "qbittorrent",
            "port": QBittorrentClient.INTERNAL_PORT,
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
            host_ok = current.get("host") == "qbittorrent"
            port_ok = str(current.get("port")) == str(QBittorrentClient.INTERNAL_PORT)
            category_ok = current.get("category") == category
            url_base_ok = (current.get("urlBase") or "") == ""
            username_ok = current.get("username") == username
            password_ok = previous_password is not None and previous_password == password

            if host_ok and port_ok and category_ok and url_base_ok and username_ok and password_ok:
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

    def _select_quality_profile_id(
        self, profiles: List[Dict[str, object]], config: StackConfig
    ) -> int:
        if not profiles:
            return 1
        target = config.quality.target_resolution
        preset = config.quality.preset
        candidates = [profile for profile in profiles if isinstance(profile, dict)]
        if target:
            token = str(target.value).lower()
            for profile in candidates:
                name = str(profile.get("name", "")).lower()
                if token in name or token.replace("p", "") in name:
                    return int(profile.get("id", candidates[0].get("id", 1)))
        if preset and preset != "balanced":
            token = str(preset).lower()
            for profile in candidates:
                name = str(profile.get("name", "")).lower()
                if token in name or token.replace("p", "") in name:
                    return int(profile.get("id", candidates[0].get("id", 1)))
        return int(candidates[0].get("id", 1))

    def _select_language_profile_id(
        self, profiles: List[Dict[str, object]], preferred: List[str]
    ) -> int:
        if not profiles:
            return 1
        if not preferred:
            return int(profiles[0].get("id", 1))

        language_map = {
            "eng": "english",
            "jpn": "japanese",
            "spa": "spanish",
            "fra": "french",
            "deu": "german",
            "ita": "italian",
            "kor": "korean",
            "chi": "chinese",
            "por": "portuguese",
            "rus": "russian",
        }
        preferred_names = {language_map.get(code, code).lower() for code in preferred}
        for profile in profiles:
            name = str(profile.get("name", "")).lower()
            if any(token in name for token in preferred_names):
                return int(profile.get("id", profiles[0].get("id", 1)))
        return int(profiles[0].get("id", 1))
