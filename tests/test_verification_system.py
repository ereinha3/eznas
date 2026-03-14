#!/usr/bin/env python3
"""
pytest-compatible test for the verification system components.
"""

import pytest
import sys
from pathlib import Path

# Add project root to Python path
sys.path.append(str(Path(__file__).parent))

from orchestrator.converge.verification_models import (
    ValidationResult,
    ValidationError,
    create_validation_error,
    ValidationResponse,
)
from orchestrator.converge.verification_engine import VerificationEngine
from orchestrator.converge.validators.path_validator import PathValidator
from orchestrator.converge.validators.port_validator import PortValidator


class TestVerificationSystem:
    """Test the verification system components"""

    def test_validation_models(self):
        """Test validation model creation and functionality"""
        # Test ValidationResult
        result = ValidationResult(success=True, errors=[], warnings=[])
        assert result.success is True
        assert len(result.errors) == 0
        assert len(result.warnings) == 0

        # Test ValidationError
        error = ValidationError(
            field="test_field",
            message="Test error message",
            severity="error",
            suggestions=["Fix the field"],
            code="TEST_ERROR",
        )
        assert error.field == "test_field"
        assert error.message == "Test error message"
        assert error.severity == "error"
        assert len(error.suggestions) == 1

        # Test create_validation_error utility
        error = create_validation_error("TEST", "field", {"key": "value"})
        assert error.code == "TEST"
        assert error.field == "field"

    def test_path_validator(self):
        """Test path validation functionality"""
        test_config = {
            "media_path": "/tmp/test_media",
            "downloads_path": "/tmp/test_downloads",
            "appdata_path": "/tmp/test_appdata",
        }

        validator = PathValidator(test_config)

        # Should fail with non-existent paths
        result = validator.validate_all_paths()
        assert not result.success
        assert len(result.errors) > 0

    def test_port_validator(self):
        """Test port validation functionality"""
        test_config = {
            "qbittorrent": {"port": 8080},
            "prowlarr": {"port": 9696},
            "radarr": {"port": 7878},
        }

        validator = PortValidator(test_config)

        # Should pass with valid ports
        result = validator.validate_all_ports()
        assert result.success
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_verification_engine(self):
        """Test the complete verification engine"""
        engine = VerificationEngine()

        test_config = {
            "media_path": "/tmp/test_media",
            "downloads_path": "/tmp/test_downloads",
            "appdata_path": "/tmp/test_appdata",
            "qbittorrent": {"port": 8080},
            "prowlarr": {"port": 9696},
            "radarr": {"port": 7878},
        }

        # Test with skip_service_checks to avoid network calls
        result = await engine.verify_configuration(
            test_config, skip_service_checks=True
        )

        # Should have ValidationResponse structure
        assert isinstance(result, ValidationResponse)
        assert hasattr(result, "result")
        assert hasattr(result, "next_steps")
        assert hasattr(result, "estimated_time")

        # Result should be ValidationResult
        assert isinstance(result.result, ValidationResult)
        assert hasattr(result.result, "success")
        assert hasattr(result.result, "errors")
        assert hasattr(result.result, "warnings")


if __name__ == "__main__":
    # Allow running directly
    pytest.main([__file__, "-v"])
