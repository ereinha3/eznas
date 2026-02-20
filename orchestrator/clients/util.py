"""Utility helpers shared across service clients."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional, Tuple, TYPE_CHECKING
from xml.etree import ElementTree

if TYPE_CHECKING:
    from ..models import StackConfig


# Container mount points (fixed in docker-compose files)
CONTAINER_APPDATA = Path("/appdata")
CONTAINER_DATA = Path("/data")
CONTAINER_SCRATCH = Path("/scratch")


def translate_path_to_container(host_path: Path, config: "StackConfig") -> Path:
    """Translate a host path to the equivalent container path.

    The orchestrator container has fixed mount points:
    - config.paths.appdata → /appdata
    - config.paths.pool → /data
    - config.paths.scratch → /scratch

    This function converts host paths from the config to paths accessible
    inside the orchestrator container.
    """
    host_str = str(host_path)

    # Check if we're running inside a container (these mount points exist)
    # This allows the same code to work in both dev (local) and prod (container) modes
    running_in_container = (
        CONTAINER_APPDATA.exists() or os.getenv("ORCH_ROOT") == "/config"
    )

    if not running_in_container:
        # Running locally, no translation needed
        return host_path

    # Check if we can access via host mount (always prefer this if available for consistency)
    if Path("/host").exists():
        # Map host path to /host/...
        # e.g. /mnt/pool/appdata -> /host/mnt/pool/appdata
        return Path("/host") / host_str.lstrip("/")

    # Translate appdata paths
    appdata_host = str(config.paths.appdata) if config.paths.appdata else None
    if appdata_host and host_str.startswith(appdata_host):
        relative = host_str[len(appdata_host) :].lstrip("/")
        target = CONTAINER_APPDATA / relative
        if CONTAINER_APPDATA.exists():
            return target

    # Translate pool paths
    pool_host = str(config.paths.pool) if config.paths.pool else None
    if pool_host and host_str.startswith(pool_host):
        relative = host_str[len(pool_host) :].lstrip("/")
        target = CONTAINER_DATA / relative
        if CONTAINER_DATA.exists():
            return target

    # Translate scratch paths
    scratch_host = str(config.paths.scratch) if config.paths.scratch else None
    if scratch_host and host_str.startswith(scratch_host):
        relative = host_str[len(scratch_host) :].lstrip("/")
        target = CONTAINER_SCRATCH / relative
        if CONTAINER_SCRATCH.exists():
            return target

    # Fallback: Check if we can access via host mount (e.g. dev mode)
    if Path("/host").exists():
        return Path("/host") / host_str.lstrip("/")

    # No translation needed or path not recognized
    return host_path


def get_service_config_dir(service_name: str, config: "StackConfig") -> Path:
    """Get the config directory for a service, translated for container access.

    This is a convenience function that handles path translation automatically.
    """
    host_path = Path(config.paths.appdata) / service_name
    return translate_path_to_container(host_path, config)


def read_arr_api_key(config_dir: Path) -> Optional[str]:
    """Read the <ApiKey> value from an *arr application's config.xml."""
    config_file = config_dir / "config.xml"
    if not config_file.exists():
        return None
    try:
        tree = ElementTree.parse(config_file)
    except (ElementTree.ParseError, OSError):
        return None
    api_key = tree.findtext("ApiKey")
    return api_key.strip() if api_key else None


def read_arr_port(config_dir: Path) -> Optional[int]:
    """Return the TCP port configured for an *arr application."""
    config_file = config_dir / "config.xml"
    if not config_file.exists():
        return None
    try:
        tree = ElementTree.parse(config_file)
    except (ElementTree.ParseError, OSError):
        return None
    port_text = tree.findtext("Port")
    if not port_text:
        return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    return port if port > 0 else None


def read_arr_url_base(config_dir: Path) -> Optional[str]:
    """Return the UrlBase (without leading slash) for an *arr application."""
    config_file = config_dir / "config.xml"
    if not config_file.exists():
        return None
    try:
        tree = ElementTree.parse(config_file)
    except (ElementTree.ParseError, OSError):
        return None
    url_base = tree.findtext("UrlBase")
    if not url_base:
        return None
    sanitized = url_base.strip().strip("/")
    return sanitized or None


def wait_for_arr_config(
    config_dir: Path, timeout: int = 180, interval: float = 2.0
) -> bool:
    """Poll until config.xml is present for an *arr application."""
    config_file = config_dir / "config.xml"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if config_file.exists():
            return True
        time.sleep(interval)
    return config_file.exists()


def arr_hash_password(
    password: str,
    *,
    iterations: int = 10_000,
    salt_b64: Optional[str] = None,
) -> tuple[str, str, int]:
    """Return PBKDF2-HMAC-SHA512 hash data compatible with *arr Forms auth."""
    if salt_b64 is None:
        salt = secrets.token_bytes(16)
    else:
        salt = base64.b64decode(salt_b64)
    derived = hashlib.pbkdf2_hmac(
        "sha512", password.encode("utf-8"), salt, iterations, dklen=32
    )
    return (
        base64.b64encode(derived).decode("ascii"),
        base64.b64encode(salt).decode("ascii"),
        iterations,
    )


def arr_verify_password(
    password: str,
    *,
    hash_b64: str,
    salt_b64: str,
    iterations: int,
) -> bool:
    """Return True if password matches stored hash."""
    derived, _, _ = arr_hash_password(
        password, iterations=iterations, salt_b64=salt_b64
    )
    return secrets.compare_digest(derived, hash_b64)


def arr_password_record(db_path: Path) -> Optional[Tuple[str, str, str, int]]:
    """Return the stored Forms credentials for an *arr app, if any."""
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT Username, Password, Salt, Iterations FROM Users ORDER BY Id LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return None
        username, password_hash, salt_b64, iterations = row
        return username, password_hash or "", salt_b64 or "", int(iterations or 10_000)


def arr_password_matches(db_path: Path, username: str, password: str) -> bool:
    """Return True if the given username/password matches the stored Forms credentials."""
    record = arr_password_record(db_path)
    if not record:
        return False
    stored_username, password_hash, salt_b64, iterations = record
    if stored_username != username or not password_hash or not salt_b64:
        return False
    return arr_verify_password(
        password,
        hash_b64=password_hash,
        salt_b64=salt_b64,
        iterations=iterations,
    )
