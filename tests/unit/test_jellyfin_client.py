"""Tests for Jellyfin service convergence helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from unittest.mock import MagicMock

from orchestrator.clients.jellyfin import JellyfinClient
from orchestrator.models import StackConfig


class _Response:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def json(self) -> Any:
        return deepcopy(self._payload)

    def raise_for_status(self) -> None:
        return None


class _EncodingClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str) -> _Response:
        assert path == "/System/Configuration/encoding"
        return _Response(self.payload)

    def post(self, path: str, json: dict[str, Any]) -> _Response:
        self.posts.append((path, deepcopy(json)))
        self.payload = deepcopy(json)
        return _Response({})


class _UsersClient:
    def __init__(self, users: list[dict[str, Any]]) -> None:
        self.users = users
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str) -> _Response:
        assert path == "/Users"
        return _Response(self.users)

    def post(self, path: str, json: dict[str, Any]) -> _Response:
        self.posts.append((path, deepcopy(json)))
        user_id = path.removeprefix("/Users/").removesuffix("/Configuration")
        for user in self.users:
            if user.get("Id") == user_id:
                user["Configuration"] = deepcopy(json)
                break
        return _Response({})


def _jellyfin_client() -> JellyfinClient:
    return JellyfinClient(repo=MagicMock())


def test_ensure_encoding_options_updates_vaapi(sample_config: dict[str, Any]) -> None:
    sample_config["services"]["jellyfin"].update(
        {
            "hardware_acceleration": "vaapi",
            "hardware_device": "/dev/dri",
            "transcode_path": "/transcode",
        }
    )
    config = StackConfig.model_validate(sample_config)
    client = _EncodingClient(
        {
            "TranscodingTempPath": "",
            "HardwareAccelerationType": "none",
            "VaapiDevice": "",
            "EnableHardwareEncoding": False,
            "EnableTonemapping": False,
            "EnableVppTonemapping": False,
            "EnableThrottling": True,
            "EnableSegmentDeletion": True,
            "SegmentKeepSeconds": 120,
            "HardwareDecodingCodecs": ["h264", "vc1"],
        }
    )

    changed, detail = _jellyfin_client()._ensure_encoding_options(client, config)

    assert changed is True
    assert detail == "encoding=updated:vaapi,/transcode"
    assert len(client.posts) == 1
    path, payload = client.posts[0]
    assert path == "/System/Configuration/encoding"
    assert payload["TranscodingTempPath"] == "/transcode"
    assert payload["HardwareAccelerationType"] == "vaapi"
    assert payload["VaapiDevice"] == "/dev/dri/renderD128"
    assert payload["EnableHardwareEncoding"] is True
    assert payload["EnableTonemapping"] is True
    assert payload["EnableVppTonemapping"] is True
    assert payload["EnableThrottling"] is False
    assert payload["EnableSegmentDeletion"] is True
    assert payload["SegmentKeepSeconds"] == 720
    assert payload["HardwareDecodingCodecs"] == ["h264", "vc1", "hevc"]


def test_ensure_encoding_options_is_idempotent(sample_config: dict[str, Any]) -> None:
    sample_config["services"]["jellyfin"].update(
        {
            "hardware_acceleration": "vaapi",
            "hardware_device": "/dev/dri/renderD128",
            "transcode_path": "/transcode",
        }
    )
    config = StackConfig.model_validate(sample_config)
    client = _EncodingClient(
        {
            "TranscodingTempPath": "/transcode",
            "HardwareAccelerationType": "vaapi",
            "VaapiDevice": "/dev/dri/renderD128",
            "EnableHardwareEncoding": True,
            "EnableTonemapping": True,
            "EnableVppTonemapping": True,
            "EnableThrottling": False,
            "EnableSegmentDeletion": True,
            "SegmentKeepSeconds": 720,
            "HardwareDecodingCodecs": ["h264", "vc1", "hevc"],
        }
    )

    changed, detail = _jellyfin_client()._ensure_encoding_options(client, config)

    assert changed is False
    assert detail == "encoding=ready"
    assert client.posts == []


def test_ensure_encoding_options_updates_nvenc(sample_config: dict[str, Any]) -> None:
    sample_config["services"]["jellyfin"].update(
        {
            "hardware_acceleration": "nvenc",
            "hardware_device": "nvidia.com/gpu=all",
            "transcode_path": "/transcode",
        }
    )
    config = StackConfig.model_validate(sample_config)
    client = _EncodingClient(
        {
            "TranscodingTempPath": "/transcode",
            "HardwareAccelerationType": "vaapi",
            "VaapiDevice": "/dev/dri/renderD128",
            "EnableHardwareEncoding": True,
            "EnableTonemapping": True,
            "EnableVppTonemapping": True,
            "EnableThrottling": False,
            "EnableSegmentDeletion": True,
            "SegmentKeepSeconds": 720,
            "HardwareDecodingCodecs": ["h264", "hevc", "vc1"],
        }
    )

    changed, detail = _jellyfin_client()._ensure_encoding_options(client, config)

    assert changed is True
    assert detail == "encoding=updated:nvenc,/transcode"
    _, payload = client.posts[0]
    assert payload["HardwareAccelerationType"] == "nvenc"
    assert payload["EnableHardwareEncoding"] is True
    assert payload["EnableTonemapping"] is True
    assert payload["EnableVppTonemapping"] is False
    assert payload["EnableEnhancedNvdecDecoder"] is True
    assert payload["EnableDecodingColorDepth10Hevc"] is True
    assert payload["EnableDecodingColorDepth10Vp9"] is True
    assert payload["HardwareDecodingCodecs"] == [
        "h264", "hevc", "vc1", "mpeg2video", "vp8", "vp9"
    ]


def test_ensure_user_configurations_updates_subtitle_defaults(
    sample_config: dict[str, Any],
) -> None:
    sample_config["services"]["jellyfin"].update(
        {
            "subtitle_mode": "OnlyForced",
            "remember_subtitle_selections": False,
        }
    )
    config = StackConfig.model_validate(sample_config)
    client = _UsersClient(
        [
            {
                "Id": "user-1",
                "Name": "admin",
                "Configuration": {
                    "SubtitleMode": "Default",
                    "RememberSubtitleSelections": True,
                },
            }
        ]
    )

    changed, detail = _jellyfin_client()._ensure_user_configurations(client, config)

    assert changed is True
    assert detail == "users=updated:admin"
    assert client.posts == [
        (
            "/Users/user-1/Configuration",
            {
                "SubtitleMode": "OnlyForced",
                "RememberSubtitleSelections": False,
            },
        )
    ]


def test_ensure_user_configurations_is_idempotent(
    sample_config: dict[str, Any],
) -> None:
    sample_config["services"]["jellyfin"].update(
        {
            "subtitle_mode": "OnlyForced",
            "remember_subtitle_selections": False,
        }
    )
    config = StackConfig.model_validate(sample_config)
    client = _UsersClient(
        [
            {
                "Id": "user-1",
                "Name": "admin",
                "Configuration": {
                    "SubtitleMode": "OnlyForced",
                    "RememberSubtitleSelections": False,
                },
            }
        ]
    )

    changed, detail = _jellyfin_client()._ensure_user_configurations(client, config)

    assert changed is False
    assert detail == "users=ready"
    assert client.posts == []
