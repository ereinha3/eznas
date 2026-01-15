"""Jellyfin automation client."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import httpx

from .arr import wait_for_http_ready
from .base import EnsureOutcome, ServiceClient
from ..models import StackConfig
from ..storage import ConfigRepository


log = logging.getLogger(__name__)


@dataclass
class _StartupResult:
    changed: bool
    detail: str
    token: Optional[str] = None


class JellyfinClient(ServiceClient):
    """Provision Jellyfin via HTTP API during converge apply."""

    name = "jellyfin"

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    def ensure(self, config: StackConfig) -> EnsureOutcome:
        jellyfin_cfg = config.services.jellyfin
        base_url = f"http://127.0.0.1:{jellyfin_cfg.port}"
        status_url = f"{base_url}/System/Ping"
        ok, status_detail = wait_for_http_ready(
            status_url,
            timeout=180.0,
            interval=5.0,
        )
        if not ok:
            return EnsureOutcome(
                detail=f"Jellyfin not ready ({status_detail})",
                success=False,
                changed=False,
            )

        state = self.repo.load_state()
        secrets = state.get("secrets", {})
        jellyfin_secrets: Dict[str, str] = secrets.get("jellyfin", {})
        admin_username = jellyfin_secrets.get("admin_username", "admin")
        admin_password = jellyfin_secrets.get("admin_password", "adminadmin")

        headers = {
            "Accept": "application/json",
            "X-Emby-Authorization": (
                'MediaBrowser Client="nas-orchestrator", '
                'Device="nas-orchestrator", DeviceId="nas-orchestrator", Version="1.0.0"'
            ),
        }
        client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(20.0, connect=5.0),
        )

        detail_parts: List[str] = []
        changed = False

        try:
            startup = self._ensure_startup_wizard(
                client=client,
                username=admin_username,
                password=admin_password,
                config=config,
            )
        except httpx.RequestError as exc:
            log.debug("Failed to contact Jellyfin at %s: %s", base_url, exc, exc_info=True)
            return EnsureOutcome(
                detail=f"connection failed ({exc.__class__.__name__}: {exc})",
                success=False,
                changed=False,
            )

        changed = changed or startup.changed
        if startup.detail:
            detail_parts.append(startup.detail)

        if startup.token is None:
            return EnsureOutcome(
                detail="authentication failed (no token returned)",
                success=False,
                changed=changed,
            )

        client.headers["X-Emby-Token"] = startup.token

        try:
            libs_changed, libs_detail = self._ensure_libraries(client, config)
        except httpx.HTTPStatusError as exc:
            log.debug("Jellyfin library API error: %s", exc, exc_info=True)
            return EnsureOutcome(
                detail=f"library ensure failed ({exc.response.status_code})",
                success=False,
                changed=changed,
            )
        except httpx.RequestError as exc:
            log.debug("Jellyfin request error while ensuring libraries: %s", exc, exc_info=True)
            return EnsureOutcome(
                detail=f"library ensure failed ({exc.__class__.__name__}: {exc})",
                success=False,
                changed=changed,
            )

        changed = changed or libs_changed
        if libs_detail:
            detail_parts.append(libs_detail)

        detail = "; ".join(detail_parts) if detail_parts else "ok"
        return EnsureOutcome(detail=detail, changed=changed, success=True)

    # ------------------------------------------------------------------ helpers
    def _ensure_startup_wizard(
        self,
        *,
        client: httpx.Client,
        username: str,
        password: str,
        config: StackConfig,
    ) -> _StartupResult:
        """Run the Jellyfin startup wizard if it has not yet completed."""
        status = self._get_system_status(client)
        detail_messages: List[str] = []
        changed = False

        if not status.get("StartupWizardCompleted", False):
            log.info("Completing Jellyfin startup wizard")
            self._post_startup_configuration(client, config)
            detail_messages.append("startup=configuration")

            self._post_remote_access(client)
            detail_messages.append("startup=remote-access")

            self._ensure_startup_user(client, username=username, password=password)
            detail_messages.append("startup=admin-created")

            self._post_startup_complete(client)
            detail_messages.append("startup=completed")
            changed = True

        token = self._authenticate(client, username=username, password=password)
        detail = "; ".join(detail_messages)
        return _StartupResult(changed=changed, detail=detail, token=token)

    def _get_system_status(self, client: httpx.Client) -> Dict:
        response = client.get("/System/Info/Public")
        response.raise_for_status()
        return response.json()

    def _post_startup_configuration(self, client: httpx.Client, config: StackConfig) -> None:
        payload = {
            "ServerName": f"NAS Orchestrator ({config.paths.pool.name})",
            "UICulture": "en-US",
            "MetadataCountryCode": "US",
            "PreferredMetadataLanguage": "en",
        }
        client.post("/Startup/Configuration", json=payload).raise_for_status()

    def _post_remote_access(self, client: httpx.Client) -> None:
        payload = {
            "EnableRemoteAccess": True,
            "EnableAutomaticPortMapping": False,
        }
        client.post("/Startup/RemoteAccess", json=payload).raise_for_status()

    def _ensure_startup_user(self, client: httpx.Client, *, username: str, password: str) -> None:
        client.get("/Startup/FirstUser").raise_for_status()
        client.post(
            "/Startup/User",
            json={"Name": username, "Password": password},
        ).raise_for_status()

    def _post_startup_complete(self, client: httpx.Client) -> None:
        client.post("/Startup/Complete").raise_for_status()

    def _authenticate(self, client: httpx.Client, *, username: str, password: str) -> Optional[str]:
        response = client.post(
            "/Users/AuthenticateByName",
            json={"Username": username, "Pw": password},
        )
        response.raise_for_status()
        data = response.json()
        token = data.get("AccessToken")
        if not token:
            log.warning("Jellyfin authentication succeeded without access token")
        return token

    def _ensure_libraries(self, client: httpx.Client, config: StackConfig) -> Tuple[bool, str]:
        response = client.get("/Library/VirtualFolders")
        response.raise_for_status()
        existing = response.json() or []

        desired = [
            ("Movies", "movies", "/data/media/movies"),
            ("TV", "tvshows", "/data/media/tv"),
            ("Anime", "tvshows", "/data/media/anime"),
        ]

        created: List[str] = []
        for name, collection_type, path in desired:
            if self._library_exists(existing, path):
                continue
            log.info("Creating Jellyfin library %s at %s", name, path)
            created.append(name)
            self._create_virtual_folder(client, name, collection_type, path)

        if not created:
            return False, "libraries=ready"
        return True, "libraries=created:" + ",".join(created)

    def _library_exists(self, existing: Iterable[Dict], path: str) -> bool:
        for entry in existing:
            locations = entry.get("Locations") or []
            if any(loc == path for loc in locations):
                return True
        return False

    def _create_virtual_folder(
        self,
        client: httpx.Client,
        name: str,
        collection_type: str,
        path: str,
    ) -> None:
        query = (
            f"/Library/VirtualFolders?name={quote(name)}"
            f"&collectionType={quote(collection_type)}"
            f"&Paths={quote(path)}"
            "&refreshLibrary=false"
        )
        payload = {"LibraryOptions": {}}
        client.post(query, json=payload).raise_for_status()

    # ------------------------------------------------------------------ mutations
    def create_user(self, config: StackConfig, username: str, password: str) -> Dict[str, str]:
        """Create or update a Jellyfin user using stored admin credentials."""
        base_url = f"http://127.0.0.1:{config.services.jellyfin.port}"
        state = self.repo.load_state()
        secrets = state.setdefault("secrets", {})
        jellyfin_secrets: Dict[str, object] = secrets.setdefault("jellyfin", {})
        admin_username = jellyfin_secrets.get("admin_username", "admin")
        admin_password = jellyfin_secrets.get("admin_password", "adminadmin")

        headers = {
            "Accept": "application/json",
            "X-Emby-Authorization": (
                'MediaBrowser Client="nas-orchestrator", '
                'Device="nas-orchestrator", DeviceId="nas-orchestrator", Version="1.0.0"'
            ),
        }
        with httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(20.0, connect=5.0),
        ) as client:
            token = self._authenticate(client, username=admin_username, password=admin_password)
            if not token:
                raise RuntimeError("Unable to authenticate with Jellyfin admin")
            client.headers["X-Emby-Token"] = token

            response = client.get("/Users")
            response.raise_for_status()
            existing_users = response.json() or []
            existing_user_id = None
            for user in existing_users:
                if user.get("Name") == username:
                    existing_user_id = user.get("Id")
                    break

            if existing_user_id is None:
                create_response = client.post("/Users/New", json={"Name": username})
                create_response.raise_for_status()
                user_data = create_response.json()
                existing_user_id = user_data.get("Id")
                if not existing_user_id:
                    raise RuntimeError("Jellyfin did not return a user identifier")

            password_payload = {
                "Id": existing_user_id,
                "CurrentPassword": "",
                "NewPassword": password,
            }
            client.post(f"/Users/{existing_user_id}/Password", json=password_payload).raise_for_status()

        users_list: List[Dict[str, str]] = jellyfin_secrets.setdefault("users", [])
        users_list = [entry for entry in users_list if entry.get("username") != username]
        users_list.append({"username": username, "password": password})
        jellyfin_secrets["users"] = users_list
        self.repo.save_state(state)

        return {"username": username, "password": password}
