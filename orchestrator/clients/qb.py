"""qBittorrent service configuration client."""
from __future__ import annotations

import json
import logging
import re
import secrets
import subprocess
from typing import Iterable, List, Optional, Tuple

import httpx

from ..models import StackConfig
from ..storage import ConfigRepository
from .base import EnsureOutcome, ServiceClient


class AuthenticationError(Exception):
    """Raised when qBittorrent authentication fails."""


log = logging.getLogger(__name__)


class QBittorrentClient(ServiceClient):
    """Configure qBittorrent using its Web API."""

    name = "qbittorrent"
    TEMP_PASSWORD_PATTERN = re.compile(
        r"temporary password (?:is provided )?for this session: (?P<password>\S+)",
        re.IGNORECASE,
    )

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    def ensure(self, config: StackConfig) -> EnsureOutcome:
        qb_cfg = config.services.qbittorrent
        base_url = f"http://127.0.0.1:{qb_cfg.port}"
        timeout = httpx.Timeout(10.0, connect=5.0)

        state = self.repo.load_state()
        secrets_state = state.setdefault("secrets", {})
        qb_state = secrets_state.setdefault("qbittorrent", {})

        desired_username = qb_cfg.username
        desired_password = qb_cfg.password

        stored_username = qb_state.get("username", desired_username)
        stored_password = qb_state.get("password")

        password_candidates = self._login_candidates(
            desired_username=desired_username,
            desired_password=desired_password,
            stored_username=stored_username,
            stored_password=stored_password,
        )

        temp_password = self._fetch_temporary_password()
        if temp_password:
            password_candidates.append(("admin", temp_password))

        default_headers = {
            "Referer": f"{base_url}/",
            "Origin": base_url,
            "User-Agent": "nas-orchestrator/1.0",
        }

        try:
            with httpx.Client(
                base_url=base_url,
                timeout=timeout,
                follow_redirects=True,
                headers=default_headers,
            ) as client:
                active_username, active_password = self._authenticate(
                    client, password_candidates
                )

                update_result, updated_password = self._configure_preferences(
                    client=client,
                    config=config,
                    current_username=active_username,
                    current_password=active_password,
                    desired_username=desired_username,
                    desired_password=desired_password,
                )

                categories_changed = self._ensure_categories(client, config)

        except httpx.RequestError as exc:
            return EnsureOutcome(
                detail=f"connection failed ({exc.__class__.__name__}: {exc})",
                changed=False,
                success=False,
            )
        except AuthenticationError:
            return EnsureOutcome(
                detail="authentication failed (unable to login with known credentials)",
                changed=False,
                success=False,
            )

        state_dirty = False
        if qb_state.get("username") != desired_username:
            qb_state["username"] = desired_username
            state_dirty = True
        if qb_state.get("password") != updated_password:
            qb_state["password"] = updated_password
            state_dirty = True

        if update_result.changed or categories_changed or state_dirty:
            secrets_state["qbittorrent"] = qb_state
            self.repo.save_state(state)

        categories = config.download_policy.categories
        detail = (
            f"user={qb_state.get('username')} "
            f"categories=radarr:{categories.radarr},sonarr:{categories.sonarr},anime:{categories.anime}"
        )
        return EnsureOutcome(detail=detail, changed=update_result.changed or categories_changed)

    def _login_candidates(
        self,
        *,
        desired_username: str,
        desired_password: Optional[str],
        stored_username: str,
        stored_password: Optional[str],
    ) -> List[Tuple[str, str]]:
        candidates: List[Tuple[str, str]] = []
        if stored_password:
            candidates.append((stored_username, stored_password))
        if desired_password:
            candidates.append((desired_username, desired_password))

        default_pairs = [
            (desired_username, "adminadmin"),
            ("admin", "adminadmin"),
        ]
        for pair in default_pairs:
            if pair not in candidates:
                candidates.append(pair)
        return candidates

    def _authenticate(
        self,
        client: httpx.Client,
        candidates: Iterable[Tuple[str, str]],
    ) -> Tuple[str, str]:
        for username, password in candidates:
            if not password:
                continue
            try:
                response = client.post(
                    "/api/v2/auth/login",
                    data={"username": username, "password": password},
                )
            except httpx.RequestError:
                continue

            if response.status_code == 200 and response.text.strip() == "Ok.":
                return username, password

        raise AuthenticationError

    def _fetch_temporary_password(self) -> Optional[str]:
        """Read docker logs to capture the session temporary password."""
        try:
            result = subprocess.run(
                ["docker", "logs", "qbittorrent", "--tail", "200"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            log.debug("Unable to read qBittorrent logs: %s", exc, exc_info=True)
            return None

        if result.returncode != 0:
            log.debug(
                "docker logs qbittorrent exited with %s: %s",
                result.returncode,
                result.stderr.strip(),
            )
            return None

        for line in reversed(result.stdout.splitlines()):
            match = self.TEMP_PASSWORD_PATTERN.search(line)
            if match:
                password = match.group("password").strip()
                log.debug("Captured qBittorrent temporary password from logs")
                return password
        return None

    def _configure_preferences(
        self,
        *,
        client: httpx.Client,
        config: StackConfig,
        current_username: str,
        current_password: str,
        desired_username: str,
        desired_password: Optional[str],
    ) -> Tuple[EnsureOutcome, str]:
        qb_cfg = config.services.qbittorrent
        downloads_root = "/downloads"
        complete_path = f"{downloads_root}/complete"
        incomplete_path = f"{downloads_root}/incomplete"

        target_password = desired_password or current_password or secrets.token_urlsafe(16)
        password_changed = target_password != current_password
        username_changed = desired_username != current_username

        preferences_payload = {
            "save_path": complete_path,
            "temp_path_enabled": True,
            "temp_path": incomplete_path,
            "max_ratio_enabled": qb_cfg.stop_after_download,
            "max_ratio": 0,
            "max_ratio_action": 0,
            "auto_tmm_enabled": False,
            "scan_dirs": {complete_path: 0},
            "web_ui_username": desired_username,
            "web_ui_password": target_password,
        }

        response = client.post(
            "/api/v2/app/setPreferences",
            data={"json": json.dumps(preferences_payload)},
        )
        response.raise_for_status()

        changed = password_changed or username_changed
        outcome = EnsureOutcome(detail="preferences updated", changed=changed)
        return outcome, target_password

    def _ensure_categories(self, client: httpx.Client, config: StackConfig) -> bool:
        categories = config.download_policy.categories
        mapping = {
            categories.radarr: "/downloads/complete/movies",
            categories.sonarr: "/downloads/complete/tv",
            categories.anime: "/downloads/complete/anime",
        }
        changed = False
        for name, path in mapping.items():
            if not name:
                continue
            if self._create_or_update_category(client, name=name, save_path=path):
                changed = True
        return changed

    def _create_or_update_category(self, client: httpx.Client, *, name: str, save_path: str) -> bool:
        """Return True if the category was created or updated."""
        response = client.post(
            "/api/v2/torrents/createCategory",
            data={"category": name, "savePath": save_path},
        )
        if response.status_code == 200:
            return True
        if response.status_code != 409:
            response.raise_for_status()
            return False

        update = client.post(
            "/api/v2/torrents/editCategory",
            data={"category": name, "savePath": save_path},
        )
        if update.status_code in (200, 409):
            return update.status_code == 200
        update.raise_for_status()
        return False