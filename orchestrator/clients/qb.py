"""qBittorrent service configuration client."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import httpx

from ..models import StackConfig
from ..storage import ConfigRepository
from .base import EnsureOutcome, ServiceClient
from .util import get_service_config_dir


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
    INTERNAL_PORT = 8080
    PBKDF2_ITERATIONS = 100_000

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    def ensure(self, config: StackConfig) -> EnsureOutcome:
        qb_cfg = config.services.qbittorrent
        base_url = f"http://qbittorrent:{self.INTERNAL_PORT}"
        internal_host = f"localhost:{self.INTERNAL_PORT}"
        internal_origin = f"http://{internal_host}"
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
            "Referer": f"{internal_origin}/",
            "Origin": internal_origin,
            "User-Agent": "nas-orchestrator/1.0",
            "Host": internal_host,
        }

        client: Optional[httpx.Client] = None
        try:
            client = httpx.Client(
                base_url=base_url,
                timeout=timeout,
                follow_redirects=True,
                headers=default_headers,
            )
            try:
                active_username, active_password = self._authenticate(
                    client, password_candidates
                )
            except AuthenticationError:
                client.close()
                target_password = (
                    desired_password or stored_password or "adminadmin"
                )
                if not self._repair_credentials(
                    config=config,
                    username=desired_username,
                    password=target_password,
                ):
                    return EnsureOutcome(
                        detail="authentication failed (unable to reconcile credentials)",
                        changed=False,
                        success=False,
                    )

                stored_username = desired_username
                stored_password = target_password
                password_candidates = self._login_candidates(
                    desired_username=desired_username,
                    desired_password=desired_password,
                    stored_username=stored_username,
                    stored_password=stored_password,
                )

                client = httpx.Client(
                    base_url=base_url,
                    timeout=timeout,
                    follow_redirects=True,
                    headers=default_headers,
                )
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
        finally:
            if client is not None:
                client.close()

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
            f"categories=radarr:{categories.radarr},sonarr:{categories.sonarr}"
        )
        return EnsureOutcome(detail=detail, changed=update_result.changed or categories_changed)

    def verify(self, config: StackConfig) -> EnsureOutcome:
        qb_cfg = config.services.qbittorrent
        base_url = f"http://qbittorrent:{self.INTERNAL_PORT}"
        internal_host = f"localhost:{self.INTERNAL_PORT}"
        internal_origin = f"http://{internal_host}"
        timeout = httpx.Timeout(10.0, connect=5.0)

        state = self.repo.load_state()
        secrets_state = state.get("secrets", {})
        qb_state = secrets_state.get("qbittorrent", {})

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
            "Referer": f"{internal_origin}/",
            "Origin": internal_origin,
            "User-Agent": "nas-orchestrator/1.0",
            "Host": internal_host,
        }

        client: Optional[httpx.Client] = None
        try:
            client = httpx.Client(
                base_url=base_url,
                timeout=timeout,
                follow_redirects=True,
                headers=default_headers,
            )
            try:
                active_username, _ = self._authenticate(client, password_candidates)
            except AuthenticationError:
                return EnsureOutcome(
                    detail="auth failed (unable to login with known credentials)",
                    changed=False,
                    success=False,
                )

            detail_parts: List[str] = [f"auth=ok ({active_username})"]
            verification_ok = True

            preferences = None
            pref_response = client.get("/api/v2/app/preferences")
            if pref_response.status_code == 200:
                preferences = pref_response.json()
            if isinstance(preferences, dict):
                ui_username = preferences.get("web_ui_username")
                if ui_username and ui_username != desired_username:
                    verification_ok = False
                    detail_parts.append(
                        f"web_ui_username mismatch (expected {desired_username}, got {ui_username})"
                    )

            categories_response = client.get("/api/v2/torrents/categories")
            categories_response.raise_for_status()
            categories_payload = categories_response.json() or {}

            expected = {
                config.download_policy.categories.radarr: "/downloads/complete/movies",
                config.download_policy.categories.sonarr: "/downloads/complete/tv",
            }
            missing = []
            mismatched = []
            for name, path in expected.items():
                record = categories_payload.get(name)
                if not record:
                    missing.append(name)
                    continue
                current_path = record.get("savePath")
                if current_path != path:
                    mismatched.append(f"{name}=>{current_path or 'unset'}")

            if missing:
                verification_ok = False
                detail_parts.append(f"missing categories: {', '.join(missing)}")
            if mismatched:
                verification_ok = False
                detail_parts.append(f"category paths mismatch: {', '.join(mismatched)}")

            if verification_ok:
                detail_parts.append("categories=ok")

            return EnsureOutcome(
                detail="; ".join(detail_parts),
                changed=False,
                success=verification_ok,
            )
        except httpx.RequestError as exc:
            return EnsureOutcome(
                detail=f"connection failed ({exc.__class__.__name__}: {exc})",
                changed=False,
                success=False,
            )
        finally:
            if client is not None:
                client.close()

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

    def _repair_credentials(
        self,
        *,
        config: StackConfig,
        username: str,
        password: str,
    ) -> bool:
        config_dir = get_service_config_dir("qbittorrent", config)
        config_dir.mkdir(parents=True, exist_ok=True)
        candidate_paths = [
            config_dir / "qBittorrent" / "qBittorrent.conf",
            config_dir / "qbittorrent" / "qBittorrent.conf",
            config_dir / "config" / "qBittorrent.conf",
            config_dir / "qBittorrent.conf",
        ]
        config_path = next((path for path in candidate_paths if path.exists()), None)
        if config_path is None:
            log.debug(
                "qBittorrent config not found (checked: %s)",
                ", ".join(str(path) for path in candidate_paths),
            )
            return False

        try:
            raw_text = config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            log.debug("qBittorrent config missing at %s", config_path)
            return False
        except OSError as exc:
            log.debug("Unable to read qBittorrent config: %s", exc, exc_info=True)
            return False

        lines = raw_text.splitlines()
        changed = False

        changed |= self._set_config_value(lines, "WebUI\\Username", username)

        stored_hash = self._get_config_value(lines, "WebUI\\Password_PBKDF2")
        if not stored_hash or not self._password_matches(stored_hash, password):
            new_hash = self._generate_password_hash(password)
            changed |= self._set_config_value(
                lines, "WebUI\\Password_PBKDF2", new_hash
            )

        changed |= self._set_config_value(lines, "WebUI\\Password_ha1", "")
        changed |= self._set_config_value(lines, "WebUI\\Port", str(self.INTERNAL_PORT))

        if changed:
            try:
                config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            except OSError as exc:
                log.debug("Unable to write qBittorrent config: %s", exc, exc_info=True)
                return False

        if not self._restart_container():
            return False

        return self._wait_for_ready(
            f"http://qbittorrent:{self.INTERNAL_PORT}"
        )

    def _restart_container(self) -> bool:
        try:
            result = subprocess.run(
                ["docker", "restart", "qbittorrent"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            log.debug("Unable to restart qBittorrent container: %s", exc, exc_info=True)
            return False

        if result.returncode != 0:
            log.debug(
                "docker restart qbittorrent failed with %s: %s",
                result.returncode,
                result.stderr.strip(),
            )
            return False
        return True

    def _wait_for_ready(self, base_url: str, timeout: float = 60.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                response = httpx.get(
                    f"{base_url}/api/v2/app/version",
                    timeout=5.0,
                    headers={"Host": f"localhost:{self.INTERNAL_PORT}"},
                )
            except httpx.HTTPError:
                time.sleep(1.0)
                continue

            if response.status_code == 200 and response.text.strip():
                return True
            if response.status_code in {401, 403}:
                return True
            time.sleep(1.0)
        return False

    def _set_config_value(self, lines: List[str], key: str, value: str) -> bool:
        if value is None:
            return False
        target = f"{key}={value}"
        prefix = f"{key}="
        for idx, line in enumerate(lines):
            if line.startswith(prefix):
                if line == target:
                    return False
                lines[idx] = target
                return True

        try:
            insert_index = lines.index("[Preferences]") + 1
        except ValueError:
            insert_index = len(lines)

        lines.insert(insert_index, target)
        return True

    def _get_config_value(self, lines: Iterable[str], key: str) -> Optional[str]:
        prefix = f"{key}="
        for line in lines:
            if line.startswith(prefix):
                value = line[len(prefix) :].strip()
                if len(value) >= 2 and value[0] == value[-1] == '"':
                    value = value[1:-1]
                return value
        return None

    def _password_matches(self, encoded: str, password: str) -> bool:
        if not encoded.startswith("@ByteArray(") or not encoded.endswith(")"):
            return False
        try:
            inner = encoded[len("@ByteArray(") : -1]
            salt_b64, digest_b64 = inner.split(":", 1)
            salt = base64.b64decode(salt_b64)
            digest = base64.b64decode(digest_b64)
        except (ValueError, base64.binascii.Error):
            return False

        candidate = hashlib.pbkdf2_hmac(
            "sha512",
            password.encode("utf-8"),
            salt,
            self.PBKDF2_ITERATIONS,
        )
        return hmac.compare_digest(candidate, digest)

    def _generate_password_hash(self, password: str) -> str:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac(
            "sha512",
            password.encode("utf-8"),
            salt,
            self.PBKDF2_ITERATIONS,
        )
        salt_b64 = base64.b64encode(salt).decode("ascii")
        digest_b64 = base64.b64encode(digest).decode("ascii")
        return f"@ByteArray({salt_b64}:{digest_b64})"

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
            # Authentication bypass for local networks (LAN, Tailscale, Docker)
            "bypass_local_auth": True,
            "bypass_auth_subnet_whitelist_enabled": True,
            "bypass_auth_subnet_whitelist": "10.0.0.0/8\n172.16.0.0/12\n192.168.0.0/16\n100.64.0.0/10\nfd00::/8",
            # Disable security features that block remote access
            "web_ui_host_header_validation_enabled": False,
            "web_ui_csrf_protection_enabled": False,
            "web_ui_clickjacking_protection_enabled": False,
            "web_ui_secure_cookie_enabled": False,
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