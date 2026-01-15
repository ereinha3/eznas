"""Utility helpers shared across service clients."""
from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Optional, Tuple
from xml.etree import ElementTree


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
    sanitized = url_base.strip().strip('/')
    return sanitized or None


def wait_for_arr_config(config_dir: Path, timeout: int = 180, interval: float = 2.0) -> bool:
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
    derived = hashlib.pbkdf2_hmac("sha512", password.encode("utf-8"), salt, iterations, dklen=32)
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
    derived, _, _ = arr_hash_password(password, iterations=iterations, salt_b64=salt_b64)
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
