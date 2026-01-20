"""Pytest configuration and shared fixtures."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Dict, Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from orchestrator.app import app
from orchestrator.models import StackConfig
from orchestrator.storage import ConfigRepository


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_config() -> Dict[str, Any]:
    """Return a valid sample configuration."""
    return {
        "version": 1,
        "paths": {
            "pool": "/data/pool",
            "scratch": "/data/scratch",
            "appdata": "/data/appdata",
        },
        "services": {
            "qbittorrent": {
                "enabled": True,
                "port": 8077,
                "username": "admin",
                "password": "adminpassword",
            },
            "radarr": {"enabled": True, "port": 7878},
            "sonarr": {"enabled": True, "port": 8989},
            "prowlarr": {"enabled": True, "port": 9696},
            "jellyseerr": {"enabled": True, "port": 5055},
            "jellyfin": {"enabled": True, "port": 8096},
            "pipeline": {"enabled": True},
        },
        "proxy": {"enabled": False},
        "download_policy": {
            "categories": {
                "radarr": "movies",
                "sonarr": "tv",
                "anime": "anime",
            }
        },
        "media_policy": {
            "movies": {"keep_audio": ["eng"], "keep_subs": ["eng"]},
            "anime": {"keep_audio": ["jpn", "eng"], "keep_subs": ["eng"]},
        },
        "quality": {"preset": "balanced"},
        "runtime": {"user_id": 1000, "group_id": 1000, "timezone": "UTC"},
        "users": [{"username": "admin", "email": "admin@example.com", "role": "owner"}],
    }


@pytest.fixture
def config_repo(temp_dir: Path, sample_config: Dict[str, Any]) -> ConfigRepository:
    """Create a ConfigRepository with sample config."""
    stack_file = temp_dir / "stack.yaml"
    state_file = temp_dir / "state.json"

    import yaml
    stack_file.write_text(yaml.dump(sample_config))
    state_file.write_text(json.dumps({}))

    return ConfigRepository(temp_dir)


@pytest.fixture
def stack_config(sample_config: Dict[str, Any]) -> StackConfig:
    """Create a StackConfig from sample config."""
    return StackConfig.model_validate(sample_config)


@pytest.fixture
def stack_config_with_temp_paths(temp_dir: Path, sample_config: Dict[str, Any]) -> StackConfig:
    """Create a StackConfig using temp_dir for paths (for pipeline tests)."""
    sample_config["paths"]["pool"] = str(temp_dir / "pool")
    sample_config["paths"]["scratch"] = str(temp_dir / "scratch")
    sample_config["paths"]["appdata"] = str(temp_dir / "appdata")
    # Create the directories
    (temp_dir / "pool").mkdir()
    (temp_dir / "scratch").mkdir()
    (temp_dir / "appdata").mkdir()
    return StackConfig.model_validate(sample_config)


@pytest.fixture
def api_client(config_repo: ConfigRepository) -> Generator[TestClient, None, None]:
    """Create a test client for the FastAPI app."""
    # Patch the module-level repo variable used by app routes
    with patch("orchestrator.app.repo", config_repo):
        with TestClient(app) as client:
            yield client


@pytest.fixture
def mock_docker() -> Generator[MagicMock, None, None]:
    """Mock Docker operations."""
    with patch("orchestrator.runtime.docker.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        yield mock_run


@pytest.fixture
def mock_httpx() -> Generator[MagicMock, None, None]:
    """Mock httpx client for service API calls."""
    with patch("httpx.Client") as mock_client:
        mock_instance = MagicMock()
        mock_client.return_value.__enter__ = MagicMock(return_value=mock_instance)
        mock_client.return_value.__exit__ = MagicMock(return_value=False)
        yield mock_instance


# Sample media files for pipeline tests
@pytest.fixture
def sample_media_info() -> Dict[str, Any]:
    """Return sample ffprobe output for a multi-language file."""
    return {
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "tags": {"language": "eng"}},
            {"index": 1, "codec_type": "audio", "codec_name": "ac3", "tags": {"language": "eng"}},
            {"index": 2, "codec_type": "audio", "codec_name": "ac3", "tags": {"language": "rus"}},
            {"index": 3, "codec_type": "audio", "codec_name": "ac3", "tags": {"language": "jpn"}},
            {"index": 4, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "eng"}},
            {"index": 5, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "rus"}},
        ]
    }
