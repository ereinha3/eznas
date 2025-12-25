"""Common helpers for working with *arr HTTP APIs."""
from __future__ import annotations

import time
from contextlib import AbstractContextManager
from typing import Any, Dict, Iterable, Optional, Tuple

import httpx


class ArrAPI(AbstractContextManager):
    """Thin wrapper around an *arr API endpoint."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 10.0) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=5.0),
            headers={"X-Api-Key": api_key},
        )

    def __enter__(self) -> "ArrAPI":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._client.close()

    def get_json(self, path: str, **kwargs: Any) -> Any:
        response = self._client.get(path, **kwargs)
        response.raise_for_status()
        return response.json()

    def post_json(self, path: str, json: Any, **kwargs: Any) -> Any:
        response = self._client.post(path, json=json, **kwargs)
        response.raise_for_status()
        if response.content:
            try:
                return response.json()
            except ValueError:
                return response.text
        return None

    def put_json(self, path: str, json: Any, **kwargs: Any) -> Any:
        response = self._client.put(path, json=json, **kwargs)
        response.raise_for_status()
        if response.content:
            try:
                return response.json()
            except ValueError:
                return response.text
        return None


def wait_for_http_ready(
    url: str,
    timeout: float = 120.0,
    interval: float = 5.0,
    verify: bool = False,
) -> tuple[bool, str]:
    """Poll an HTTP endpoint until it responds or timeout expires."""
    deadline = time.monotonic() + timeout
    last_error: Optional[str] = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=5.0, verify=verify)
            if response.status_code < 500:
                return True, f"{url} ready ({response.status_code})"
            last_error = f"HTTP {response.status_code}"
        except httpx.RequestError as exc:
            last_error = str(exc)
        time.sleep(interval)
    return False, f"timeout waiting for {url}: {last_error or 'no response'}"


def set_field_values(fields: Iterable[Dict[str, Any]], overrides: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Return a new list of `fields` with `value` entries overridden."""
    updated = []
    for field in fields:
        item = dict(field)
        name = item.get("name")
        if name and name in overrides:
            item["value"] = overrides[name]
        elif "value" not in item and name in overrides:
            item["value"] = overrides[name]
        updated.append(item)
    return updated


def describe_changes(changes: Iterable[Tuple[bool, str]]) -> Tuple[bool, str]:
    """Aggregate a set of (changed, message) tuples into a summary string."""
    messages = []
    changed = False
    for did_change, message in changes:
        if did_change:
            changed = True
        if message:
            messages.append(message)
    return changed, "; ".join(messages) if messages else ""


