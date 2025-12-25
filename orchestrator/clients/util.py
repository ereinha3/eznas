"""Utility helpers shared across service clients."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional
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


def wait_for_arr_config(config_dir: Path, timeout: int = 180, interval: float = 2.0) -> bool:
    """Poll until config.xml is present for an *arr application."""
    config_file = config_dir / "config.xml"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if config_file.exists():
            return True
        time.sleep(interval)
    return config_file.exists()








