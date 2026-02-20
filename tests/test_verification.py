import pytest
import tempfile
import os
from pathlib import Path

from orchestrator.converge.verification_engine import VerificationEngine
from orchestrator.converge.validators.path_validator import PathValidator
from orchestrator.converge.validators.port_validator import PortValidator
from orchestrator.converge.verification_models import (
    ValidationResult,
    ValidationError,
    create_validation_error,
)


class TestVerificationEngine:
    """Test the main verification engine"""

    @pytest.fixture
    def valid_config(self):
        """A valid configuration for testing"""
        return {
            "media_path": "/tmp/media",
            "downloads_path": "/tmp/downloads",
            "appdata_path": "/tmp/appdata",
            "scratch_path": "/tmp/scratch",
            "qbittorrent": {
                "host": "localhost",
                "web_port": 8080,
                "download_dir": "/tmp/downloads",
            },
            "prowlarr": {
                "host": "localhost",
                "port": 9696,
                "api_key": "test_api_key_12345678901234567890",
            },
            "radarr": {
                "host": "localhost",
                "port": 7878,
                "api_key": "test_api_key_12345678901234567890",
                "root_folder": "/tmp/media/movies",
            },
            "sonarr": {
                "host": "localhost",
                "port": 8989,
                "api_key": "test_api_key_12345678901234567890",
                "root_folder": "/tmp/media/tv",
            },
        }

    @pytest.fixture
    def temp_dirs(self):
        """Create temporary directories for testing"""
        dirs = {}
        for name in ["media", "downloads", "appdata", "scratch"]:
            temp_dir = tempfile.mkdtemp(prefix=f"nas_test_{name}_")
            dirs[name] = temp_dir
            os.makedirs(os.path.join(temp_dir, "movies"), exist_ok=True)
            os.makedirs(os.path.join(temp_dir, "tv"), exist_ok=True)

        yield dirs

        # Cleanup
        for temp_dir in dirs.values():
            if os.path.exists(temp_dir):
                import shutil

                shutil.rmtree(temp_dir)

    @pytest.mark.asyncio
    async def test_verify_configuration_success(self, valid_config, temp_dirs):
        """Test successful configuration verification"""
        # Update config with temp paths
        valid_config["media_path"] = temp_dirs["media"]
        valid_config["downloads_path"] = temp_dirs["downloads"]
        valid_config["appdata_path"] = temp_dirs["appdata"]
        valid_config["scratch_path"] = temp_dirs["scratch"]
        valid_config["qbittorrent"]["download_dir"] = temp_dirs["downloads"]
        valid_config["radarr"]["root_folder"] = os.path.join(
            temp_dirs["media"], "movies"
        )
        valid_config["sonarr"]["root_folder"] = os.path.join(temp_dirs["media"], "tv")

        engine = VerificationEngine()
        result = await engine.verify_configuration(
            config=valid_config,
            skip_service_checks=True,  # Skip service connectivity for tests
        )

        assert result.result.success is True or result.result.has_warnings()
        assert len(result.result.errors) == 0
        assert result.next_steps is not None
        assert result.estimated_time is not None

    @pytest.mark.asyncio
    async def test_verify_configuration_missing_paths(self, valid_config):
        """Test verification with missing paths"""
        # Use non-existent paths
        invalid_config = valid_config.copy()
        invalid_config["media_path"] = "/nonexistent/path"

        engine = VerificationEngine()
        result = await engine.verify_configuration(
            config=invalid_config, skip_service_checks=True
        )

        assert result.result.success is False
        assert len(result.result.errors) > 0

        # Check for path error
        path_errors = [e for e in result.result.errors if "PATH_NOT_FOUND" in e.code]
        assert len(path_errors) > 0

    @pytest.mark.asyncio
    async def test_verify_configuration_invalid_ports(self, valid_config, temp_dirs):
        """Test verification with invalid ports"""
        # Update config with temp paths
        valid_config["media_path"] = temp_dirs["media"]
        valid_config["downloads_path"] = temp_dirs["downloads"]
        valid_config["appdata_path"] = temp_dirs["appdata"]

        # Use invalid port
        invalid_config = valid_config.copy()
        invalid_config["qbittorrent"]["web_port"] = 999999

        engine = VerificationEngine()
        result = await engine.verify_configuration(
            config=invalid_config, skip_service_checks=True
        )

        assert result.result.success is False

        # Check for port error
        port_errors = [e for e in result.result.errors if "PORT_OUT_OF_RANGE" in e.code]
        assert len(port_errors) > 0

    @pytest.mark.asyncio
    async def test_verify_partial_configuration(self, valid_config, temp_dirs):
        """Test partial configuration verification"""
        # Only include paths
        partial_config = {
            "media_path": temp_dirs["media"],
            "downloads_path": temp_dirs["downloads"],
            "appdata_path": temp_dirs["appdata"],
        }

        engine = VerificationEngine()
        result = await engine.verify_configuration(
            config=partial_config, partial=True, skip_service_checks=True
        )

        # Should succeed but have warnings about missing services
        assert result.result.success is True or result.result.has_warnings()

        # Check for partial validation warning
        partial_warnings = [
            w for w in result.result.warnings if w.code == "PARTIAL_VALIDATION"
        ]
        assert len(partial_warnings) > 0


class TestPathValidator:
    """Test the path validator"""

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for testing"""
        temp_dir = tempfile.mkdtemp(prefix="nas_test_")
        yield temp_dir
        import shutil

        shutil.rmtree(temp_dir)

    def test_validate_existing_paths(self, temp_dir):
        """Test validation of existing paths"""
        config = {
            "media_path": temp_dir,
            "downloads_path": temp_dir,
            "appdata_path": temp_dir,
        }

        validator = PathValidator(config)
        result = validator.validate_all_paths()

        assert result.success is True
        assert len(result.errors) == 0

    def test_validate_missing_paths(self):
        """Test validation of missing paths"""
        config = {
            "media_path": "/nonexistent/path",
            "downloads_path": "/another/nonexistent/path",
            "appdata_path": "/yet/another/nonexistent/path",
        }

        validator = PathValidator(config)
        result = validator.validate_all_paths()

        assert result.success is False
        assert len(result.errors) >= 3

        # Check error codes
        error_codes = [error.code for error in result.errors]
        assert "PATH_NOT_FOUND" in error_codes

    def test_validate_client_side_rules(self, temp_dir):
        """Test client-side validation rules are generated"""
        config = {
            "media_path": temp_dir,
            "downloads_path": temp_dir,
            "appdata_path": temp_dir,
            "scratch_path": temp_dir,
        }

        validator = PathValidator(config)
        result = validator.validate_all_paths()

        # Check that client-side rules were generated
        assert len(result.client_side_rules) >= 4

        required_fields = [
            "media_path",
            "downloads_path",
            "appdata_path",
            "scratch_path",
        ]
        for field in required_fields:
            assert field in result.client_side_rules
            rule = result.client_side_rules[field]
            assert rule.type == "string"
            assert rule.min_length == 3
            assert rule.max_length == 255
            assert rule.required == (field != "scratch_path")


class TestValidationModels:
    """Test validation model functions"""

    def test_create_validation_error(self):
        """Test creating validation errors from codes"""
        error = create_validation_error(
            "PATH_NOT_FOUND", "test_field", {"path": "/test/path"}
        )

        assert error.field == "test_field"
        assert error.code == "PATH_NOT_FOUND"
        assert error.severity == "error"
        assert "/test/path" in error.message
        assert len(error.suggestions) > 0

    def test_validation_result_helper_methods(self):
        """Test ValidationResult helper methods"""
        result = ValidationResult(success=True)

        # Test with no errors/warnings
        assert result.has_errors() is False
        assert result.has_warnings() is False

        # Add an error
        error = ValidationError(
            field="test",
            message="Test error",
            severity="error",
            suggestions=[],
            code="TEST_ERROR",
        )
        result.errors.append(error)

        assert result.has_errors() is True
        assert result.has_warnings() is False

        # Add a warning
        warning = ValidationError(
            field="test",
            message="Test warning",
            severity="warning",
            suggestions=[],
            code="TEST_WARNING",
        )
        result.warnings.append(warning)

        assert result.has_errors() is True
        assert result.has_warnings() is True

        # Test field-specific methods
        field_errors = result.get_field_errors("test")
        assert len(field_errors) == 1
        assert field_errors[0].code == "TEST_ERROR"

        field_warnings = result.get_field_warnings("test")
        assert len(field_warnings) == 1
        assert field_warnings[0].code == "TEST_WARNING"


class TestPortValidator:
    """Test the port validator"""

    def test_validate_valid_ports(self):
        """Test validation of valid ports"""
        config = {
            "qbittorrent": {"port": 8080},
            "prowlarr": {"port": 9696},
            "radarr": {"port": 7878},
            "sonarr": {"port": 8989},
            "jellyfin": {"port": 8096},
            "jellyseerr": {"port": 5055},
        }

        validator = PortValidator(config)
        result = validator.validate_all_ports()

        # Should succeed unless ports are actually in use
        # We'll check that valid ports within range don't cause range errors
        range_errors = [e for e in result.errors if "PORT_OUT_OF_RANGE" in e.code]
        assert len(range_errors) == 0

    def test_validate_invalid_ports(self):
        """Test validation of invalid ports"""
        config = {
            "qbittorrent": {"port": 999999},  # Too high
            "prowlarr": {"port": 0},  # Too low
            "radarr": {"port": -1},  # Negative
        }

        validator = PortValidator(config)
        result = validator.validate_all_ports()

        assert result.success is False

        # Check for port range errors
        range_errors = [e for e in result.errors if "PORT_OUT_OF_RANGE" in e.code]
        assert len(range_errors) >= 3

    def test_validate_duplicate_ports(self):
        """Test validation of duplicate port assignments"""
        config = {
            "qbittorrent": {"port": 8080},
            "prowlarr": {"port": 8080},  # Same as qBittorrent
            "radarr": {"port": 8080},  # Same as others
        }

        validator = PortValidator(config)
        result = validator.validate_all_ports()

        assert result.success is False

        # Check for duplicate port error
        duplicate_errors = [
            e for e in result.errors if "DUPLICATE_PORT_ASSIGNMENT" in e.code
        ]
        assert len(duplicate_errors) == 1
