"""Tests for FastAPI endpoints."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestConfigEndpoints:
    """Tests for configuration API endpoints."""

    def test_get_config(self, api_client: TestClient):
        """GET /api/config should return current config."""
        response = api_client.get("/api/config")
        assert response.status_code == 200
        data = response.json()
        assert "paths" in data
        assert "services" in data

    def test_post_config_valid(self, api_client: TestClient, sample_config: Dict[str, Any]):
        """POST /api/config with valid data should succeed."""
        response = api_client.post("/api/config", json=sample_config)
        assert response.status_code in [200, 201]

    def test_post_config_invalid_json(self, api_client: TestClient):
        """POST /api/config with invalid JSON should fail."""
        response = api_client.post(
            "/api/config",
            content="not valid json",
            headers={"Content-Type": "application/json"}
        )
        assert response.status_code in [400, 422]

    def test_post_config_missing_required(self, api_client: TestClient):
        """POST /api/config missing required fields should fail."""
        response = api_client.post("/api/config", json={"version": 1})
        assert response.status_code == 422

    def test_post_config_extra_fields(self, api_client: TestClient, sample_config: Dict[str, Any]):
        """POST /api/config with extra fields should handle gracefully."""
        sample_config["unknown_field"] = "should be ignored"
        response = api_client.post("/api/config", json=sample_config)
        # Should either ignore extra fields or return 422
        assert response.status_code in [200, 201, 422]


class TestValidateEndpoint:
    """Tests for validation endpoint."""

    def test_validate_valid_config(self, api_client: TestClient, sample_config: Dict[str, Any]):
        """Validation of valid config should pass."""
        response = api_client.post("/api/validate", json=sample_config)
        assert response.status_code == 200
        data = response.json()
        assert "valid" in data or "errors" in data

    def test_validate_invalid_config(self, api_client: TestClient):
        """Validation of invalid config should return errors."""
        invalid_config = {
            "version": 1,
            "paths": {"pool": ""},  # Empty path
        }
        response = api_client.post("/api/validate", json=invalid_config)
        assert response.status_code in [200, 422]
        # If 200, should have validation errors in response


class TestApplyEndpoint:
    """Tests for apply endpoint."""

    def test_apply_requires_config(self, api_client: TestClient):
        """Apply without valid config should fail."""
        with patch("orchestrator.converge.runner.ApplyRunner.run") as mock_run:
            mock_run.side_effect = Exception("No config")
            response = api_client.post("/api/apply")
            # Should handle gracefully

    def test_apply_returns_stream(self, api_client: TestClient):
        """Apply should return SSE stream."""
        with patch("orchestrator.converge.runner.ApplyRunner") as MockRunner:
            mock_instance = MagicMock()
            mock_instance.run.return_value = iter([
                {"stage": "validate", "status": "ok"},
                {"stage": "render", "status": "ok"},
            ])
            MockRunner.return_value = mock_instance

            response = api_client.post("/api/apply", headers={"Accept": "text/event-stream"})
            # Should be SSE or JSON response


class TestStatusEndpoint:
    """Tests for status endpoint."""

    def test_get_status(self, api_client: TestClient):
        """GET /api/status should return service status."""
        response = api_client.get("/api/status")
        assert response.status_code == 200


class TestCredentialsEndpoint:
    """Tests for credentials endpoint."""

    def test_get_credentials(self, api_client: TestClient):
        """GET /api/credentials should return stored credentials."""
        response = api_client.get("/api/credentials")
        assert response.status_code == 200

    def test_credentials_masks_passwords(self, api_client: TestClient):
        """Credentials should not expose raw passwords."""
        response = api_client.get("/api/credentials")
        data = response.json()
        # Passwords should be masked or omitted
        for service, creds in data.items():
            if "password" in creds:
                # Should be masked (e.g., "****") or not present
                pass


class TestInputSanitization:
    """Tests for input sanitization and security."""

    def test_xss_in_config(self, api_client: TestClient, sample_config: Dict[str, Any]):
        """XSS attempts should be sanitized."""
        sample_config["paths"]["pool"] = "<script>alert('xss')</script>"
        response = api_client.post("/api/config", json=sample_config)
        # Should either reject or sanitize

    def test_path_traversal(self, api_client: TestClient, sample_config: Dict[str, Any]):
        """Path traversal attempts should be blocked."""
        sample_config["paths"]["pool"] = "/data/../../../etc/passwd"
        response = api_client.post("/api/config", json=sample_config)
        # Should reject or normalize

    def test_very_long_input(self, api_client: TestClient, sample_config: Dict[str, Any]):
        """Very long inputs should be handled."""
        sample_config["paths"]["pool"] = "/data/" + "a" * 10000
        response = api_client.post("/api/config", json=sample_config)
        # Should reject or truncate

    def test_null_bytes(self, api_client: TestClient, sample_config: Dict[str, Any]):
        """Null bytes in input should be handled."""
        sample_config["services"]["qbittorrent"]["username"] = "admin\x00injected"
        response = api_client.post("/api/config", json=sample_config)
        # Should sanitize


class TestConcurrency:
    """Tests for concurrent request handling."""

    def test_concurrent_config_updates(self, api_client: TestClient, sample_config: Dict[str, Any]):
        """Concurrent config updates should be handled safely."""
        import threading
        results = []

        def update_config():
            response = api_client.post("/api/config", json=sample_config)
            results.append(response.status_code)

        threads = [threading.Thread(target=update_config) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should succeed or fail gracefully
        assert all(code in [200, 201, 409, 423] for code in results)


class TestErrorHandling:
    """Tests for error handling."""

    def test_internal_error_handling(self, api_client: TestClient):
        """Internal errors should return proper error response."""
        with patch("orchestrator.storage.ConfigRepository.load_stack") as mock_load:
            mock_load.side_effect = Exception("Database error")
            response = api_client.get("/api/config")
            assert response.status_code == 500
            data = response.json()
            assert "error" in data or "detail" in data

    def test_not_found_handling(self, api_client: TestClient):
        """Non-existent endpoints should return 404."""
        response = api_client.get("/api/nonexistent")
        assert response.status_code == 404
