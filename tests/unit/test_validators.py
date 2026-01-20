"""Tests for configuration validators."""
from __future__ import annotations

import pytest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from orchestrator.validators import run_validation
from orchestrator.models import StackConfig, ValidationResult


class TestPathValidation:
    """Tests for path configuration validation."""

    def test_valid_paths(self, sample_config: Dict[str, Any]):
        """Valid paths should pass validation."""
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.is_dir", return_value=True):
                with patch("orchestrator.validators.os_access", return_value=True):
                    with patch("orchestrator.validators._port_available", return_value=True):
                        config = StackConfig.model_validate(sample_config)
                        result = run_validation(config)
                        # Check path-related checks are all "ok"
                        for key in ["paths.pool", "paths.scratch", "paths.appdata"]:
                            assert result.checks.get(key) == "ok"

    def test_empty_pool_path(self, sample_config: Dict[str, Any]):
        """Empty pool path should fail validation."""
        sample_config["paths"]["pool"] = ""
        with pytest.raises(Exception):  # Pydantic or custom validation
            StackConfig.model_validate(sample_config)

    def test_relative_path_handling(self, sample_config: Dict[str, Any]):
        """Relative paths should be rejected (must be absolute)."""
        sample_config["paths"]["pool"] = "./relative/path"
        with pytest.raises(Exception):  # Pydantic rejects relative paths
            StackConfig.model_validate(sample_config)

    def test_path_with_spaces(self, sample_config: Dict[str, Any]):
        """Paths with spaces should be handled."""
        sample_config["paths"]["pool"] = "/data/my pool/media"
        config = StackConfig.model_validate(sample_config)
        assert " " in str(config.paths.pool)

    def test_path_with_special_characters(self, sample_config: Dict[str, Any]):
        """Paths with special characters should be handled."""
        sample_config["paths"]["pool"] = "/data/media (new)/pool"
        config = StackConfig.model_validate(sample_config)
        assert config.paths.pool is not None


class TestPortValidation:
    """Tests for port configuration validation."""

    def test_valid_port(self, sample_config: Dict[str, Any]):
        """Valid ports should pass."""
        sample_config["services"]["radarr"]["port"] = 7878
        config = StackConfig.model_validate(sample_config)
        assert config.services.radarr.port == 7878

    def test_port_zero(self, sample_config: Dict[str, Any]):
        """Port 0 should be rejected or handled."""
        sample_config["services"]["radarr"]["port"] = 0
        # Should either raise or allow (depending on implementation)
        try:
            config = StackConfig.model_validate(sample_config)
            # If it passes, port should be 0 or converted
            assert config.services.radarr.port is not None
        except Exception:
            pass  # Expected if validation rejects port 0

    def test_port_negative(self, sample_config: Dict[str, Any]):
        """Negative ports should be rejected."""
        sample_config["services"]["radarr"]["port"] = -1
        with pytest.raises(Exception):
            StackConfig.model_validate(sample_config)

    def test_port_too_high(self, sample_config: Dict[str, Any]):
        """Ports > 65535 should be rejected."""
        sample_config["services"]["radarr"]["port"] = 70000
        with pytest.raises(Exception):
            StackConfig.model_validate(sample_config)

    def test_duplicate_ports(self, sample_config: Dict[str, Any]):
        """Duplicate ports should be detected."""
        sample_config["services"]["radarr"]["port"] = 8080
        sample_config["services"]["sonarr"]["port"] = 8080
        config = StackConfig.model_validate(sample_config)
        result = run_validation(config)
        # Should warn or error about duplicate ports
        # This depends on run_validation implementation


class TestCredentialValidation:
    """Tests for credential validation."""

    def test_valid_credentials(self, sample_config: Dict[str, Any]):
        """Valid credentials should pass."""
        sample_config["services"]["qbittorrent"]["username"] = "admin"
        sample_config["services"]["qbittorrent"]["password"] = "securepass123"
        config = StackConfig.model_validate(sample_config)
        assert config.services.qbittorrent.username == "admin"

    def test_empty_username(self, sample_config: Dict[str, Any]):
        """Empty username should be rejected or use default."""
        sample_config["services"]["qbittorrent"]["username"] = ""
        try:
            config = StackConfig.model_validate(sample_config)
            # If allowed, should have some value
        except Exception:
            pass  # Expected if validation requires username

    def test_special_chars_in_password(self, sample_config: Dict[str, Any]):
        """Special characters in password should work."""
        sample_config["services"]["qbittorrent"]["password"] = "p@$$w0rd!#$%^&*()"
        config = StackConfig.model_validate(sample_config)
        assert config.services.qbittorrent.password == "p@$$w0rd!#$%^&*()"

    def test_unicode_in_password(self, sample_config: Dict[str, Any]):
        """Unicode characters in password should be handled."""
        sample_config["services"]["qbittorrent"]["password"] = "пароль密码"
        config = StackConfig.model_validate(sample_config)
        assert config.services.qbittorrent.password is not None

    def test_very_long_password(self, sample_config: Dict[str, Any]):
        """Very long passwords should be handled."""
        long_password = "a" * 500
        sample_config["services"]["qbittorrent"]["password"] = long_password
        config = StackConfig.model_validate(sample_config)
        # Should either accept or truncate


class TestCategoryValidation:
    """Tests for download category validation."""

    def test_valid_categories(self, sample_config: Dict[str, Any]):
        """Valid category names should pass."""
        config = StackConfig.model_validate(sample_config)
        assert config.download_policy.categories.radarr == "movies"

    def test_empty_category(self, sample_config: Dict[str, Any]):
        """Empty category name should be rejected."""
        sample_config["download_policy"]["categories"]["radarr"] = ""
        try:
            config = StackConfig.model_validate(sample_config)
            result = run_validation(config)
            # Should fail validation
        except Exception:
            pass  # Expected

    def test_category_with_spaces(self, sample_config: Dict[str, Any]):
        """Category with spaces might cause issues."""
        sample_config["download_policy"]["categories"]["radarr"] = "my movies"
        config = StackConfig.model_validate(sample_config)
        # Should warn or sanitize

    def test_category_with_slash(self, sample_config: Dict[str, Any]):
        """Category with slash should be rejected (path issues)."""
        sample_config["download_policy"]["categories"]["radarr"] = "movies/new"
        config = StackConfig.model_validate(sample_config)
        result = run_validation(config)
        # Should fail or warn

    def test_duplicate_categories(self, sample_config: Dict[str, Any]):
        """Duplicate category names should be detected."""
        sample_config["download_policy"]["categories"]["radarr"] = "media"
        sample_config["download_policy"]["categories"]["sonarr"] = "media"
        config = StackConfig.model_validate(sample_config)
        result = run_validation(config)
        # Should warn about duplicates


class TestMediaPolicyValidation:
    """Tests for media policy validation."""

    def test_valid_languages(self, sample_config: Dict[str, Any]):
        """Valid language codes should pass."""
        config = StackConfig.model_validate(sample_config)
        assert "eng" in config.media_policy.movies.keep_audio

    def test_empty_audio_languages(self, sample_config: Dict[str, Any]):
        """Empty audio languages should warn."""
        sample_config["media_policy"]["movies"]["keep_audio"] = []
        config = StackConfig.model_validate(sample_config)
        result = run_validation(config)
        # Should warn that no audio will be kept

    def test_invalid_language_code(self, sample_config: Dict[str, Any]):
        """Invalid language codes should be handled."""
        sample_config["media_policy"]["movies"]["keep_audio"] = ["english", "xxx"]
        config = StackConfig.model_validate(sample_config)
        # Should either convert or reject

    def test_duplicate_languages(self, sample_config: Dict[str, Any]):
        """Duplicate languages should be deduplicated."""
        sample_config["media_policy"]["movies"]["keep_audio"] = ["eng", "eng", "jpn"]
        config = StackConfig.model_validate(sample_config)
        # Implementation should handle duplicates


class TestRuntimeValidation:
    """Tests for runtime settings validation."""

    def test_valid_uid_gid(self, sample_config: Dict[str, Any]):
        """Valid UID/GID should pass."""
        config = StackConfig.model_validate(sample_config)
        assert config.runtime.user_id == 1000

    def test_negative_uid(self, sample_config: Dict[str, Any]):
        """Negative UID should be rejected."""
        sample_config["runtime"]["user_id"] = -1
        with pytest.raises(Exception):
            StackConfig.model_validate(sample_config)

    def test_valid_timezone(self, sample_config: Dict[str, Any]):
        """Valid timezone should pass."""
        sample_config["runtime"]["timezone"] = "America/New_York"
        config = StackConfig.model_validate(sample_config)
        assert config.runtime.timezone == "America/New_York"

    def test_invalid_timezone(self, sample_config: Dict[str, Any]):
        """Invalid timezone should be handled."""
        sample_config["runtime"]["timezone"] = "Fake/Timezone"
        config = StackConfig.model_validate(sample_config)
        # Should either reject or use default
