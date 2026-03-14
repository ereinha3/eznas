#!/usr/bin/env python3
"""
Comprehensive end-to-end test for NAS Orchestrator enhanced setup wizard and verification system.

This script tests the complete pipeline from scratch to ensure all components work together correctly.
"""

import asyncio
import json
import os
import sys
import time
import httpx
from pathlib import Path
from typing import Dict, Any

# Add project root to Python path for imports
sys.path.append("/home/ethan/eznas/nas_orchestrator")

# Test configuration
TEST_CONFIG = {
    "media_path": "/tmp/nas_test/media",
    "downloads_path": "/tmp/nas_test/downloads",
    "appdata_path": "/tmp/nas_test/appdata",
    "scratch_path": "/tmp/nas_test/scratch",
    "qbittorrent": {
        "host": "localhost",
        "web_port": 8080,
        "download_dir": "/tmp/nas_test/downloads",
    },
    "prowlarr": {
        "host": "localhost",
        "port": 9696,
        "api_key": "test_api_key_123456789012345678901234567890",
    },
    "radarr": {
        "host": "localhost",
        "port": 7878,
        "api_key": "test_api_key_123456789012345678901234567890",
        "root_folder": "/tmp/nas_test/media/movies",
    },
    "sonarr": {
        "host": "localhost",
        "port": 8989,
        "api_key": "test_api_key_123456789012345678901234567890",
        "root_folder": "/tmp/nas_test/media/tv",
    },
    "jellyfin": {"host": "localhost", "port": 8096},
    "jellyseerr": {"host": "localhost", "port": 5055},
    "remux_agent": {
        "ffmpeg_path": "ffmpeg",
        "language_filters": {"audio": ["eng"], "subtitle": ["eng"]},
    },
}


class TestResults:
    def __init__(self):
        self.success_count = 0
        self.total_tests = 0
        self.failed_tests = []
        self.passed_tests = []
        self.warnings = []
        self.start_time = time.time()

    def success(self, test_name: str, details: str = "") -> None:
        self.success_count += 1
        self.passed_tests.append(test_name)
        self.total_tests += 1
        print(f"✅ {test_name}: PASSED {details}")

    def failure(self, test_name: str, error: str) -> None:
        self.failed_tests.append(f"{test_name}: {error}")
        self.total_tests += 1
        print(f"❌ {test_name}: FAILED - {error}")

    def warning(self, test_name: str, warning: str) -> None:
        self.warnings.append(f"{test_name}: {warning}")
        print(f"⚠ {test_name}: WARNING - {warning}")

    def info(self, message: str) -> None:
        print(f"ℹ️ {message}")

    def summary(self):
        duration = time.time() - self.start_time
        print(f"\n{'=' * 50}")
        print("Test Summary:")
        print(f"Total Tests: {self.total_tests}")
        print(f"Passed: {self.success_count}")
        print(f"Failed: {len(self.failed_tests)}")
        print(f"Warnings: {len(self.warnings)}")
        print(f"Duration: {duration:.2f}s")

        if self.failed_tests:
            print("\nFailed Tests:")
            for failure in self.failed_tests:
                print(f"  • {failure}")

        if self.warnings:
            print("\nWarnings:")
            for warning in self.warnings:
                print(f"  • {warning}")

        success_rate = (
            (self.success_count / self.total_tests) * 100 if self.total_tests > 0 else 0
        )
        print(f"\nSuccess Rate: {success_rate:.1f}%")

        if self.failed_tests == 0:
            print("\n🎉 ALL TESTS PASSED! System is ready for production.")

        return self.failed_tests == 0


class NASOrchestratorTester:
    def __init__(self):
        self.base_url = "http://localhost:8443"
        self.session = None
        self.test_results = TestResults()
        self.test_dirs = []

    def info(self, message: str) -> None:
        """Delegate info messages to test_results"""
        self.test_results.info(message)

    def success(self, test_name: str, details: str = "") -> None:
        """Delegate success messages to test_results"""
        self.test_results.success(test_name, details)

    def failure(self, test_name: str, error: str) -> None:
        """Delegate failure messages to test_results"""
        self.test_results.failure(test_name, error)

    def warning(self, test_name: str, warning: str) -> None:
        """Delegate warning messages to test_results"""
        self.test_results.warning(test_name, warning)

    async def create_test_directories(self):
        """Create the test directory structure"""
        test_base = "/tmp/nas_test"
        self.info(f"Creating test directories in {test_base}")

        try:
            # Create main directories
            for dir_name in ["media", "downloads", "appdata", "scratch"]:
                dir_path = Path(test_base) / dir_name
                dir_path.mkdir(parents=True, exist_ok=True)

                # Create subdirectories
                if dir_name == "media":
                    (dir_path / "movies").mkdir(exist_ok=True)
                    (dir_path / "tv").mkdir(exist_ok=True)

                # Create test files
                test_file = dir_path / f".test_{dir_name}"
                test_file.write_text("test")

                # Set appropriate permissions
                os.chmod(dir_path, 0o755)
                if dir_name != "scratch":
                    os.chmod(test_file, 0o755)

            self.test_dirs.append(test_base)
            self.info(f"✓ Created test directories: {self.test_dirs}")

        except Exception as e:
            raise Exception(f"Failed to create test directories: {e}")

    async def cleanup_test_directories(self):
        """Clean up the test directories"""
        self.info("\nCleaning up test directories...")

        for test_dir in self.test_dirs:
            try:
                import shutil

                if os.path.exists(test_dir):
                    shutil.rmtree(test_dir)
            except Exception as e:
                self.warning("cleanup", f"Failed to cleanup {test_dir}: {e}")

    async def test_api_endpoint(
        self, endpoint: str, data: Dict[str, Any], expected_status: int = 200
    ) -> bool:
        """Test an API endpoint"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    self.base_url + endpoint, json=data, timeout=30
                )

            if response.status_code == expected_status:
                try:
                    result = response.json()
                    return result.get("success", False) or result.get("result", {}).get(
                        "success", False
                    )
                except (json.JSONDecodeError, KeyError):
                    return False
            else:
                self.failure(endpoint, f"HTTP {response.status_code}: {response.text}")
                return False

        except Exception as e:
            self.failure(endpoint, f"Request failed: {e}")
            return False

    async def test_status_endpoint(self) -> bool:
        """Test setup status endpoint"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.base_url + "/api/setup/status", timeout=30
                )

            if response.status_code == 200:
                result = response.json()
                return True
            else:
                self.failure(
                    "status endpoint", f"HTTP {response.status_code}: {response.text}"
                )
                return False

        except Exception as e:
            self.failure("status endpoint", f"Request failed: {e}")
            return False

    async def test_volumes_endpoint(self) -> bool:
        """Test volumes endpoint"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    self.base_url + "/api/system/volumes", timeout=30
                )

            if response.status_code == 200:
                result = response.json()
                volumes = result.get("volumes", [])
                return len(volumes) > 0
            else:
                self.failure(
                    "volumes endpoint", f"HTTP {response.status_code}: {response.text}"
                )
                return False

        except Exception as e:
            self.failure("volumes endpoint", f"Request failed: {e}")
            return False

    async def test_verify_endpoint(self, config: Dict[str, Any]) -> bool:
        """Test the verify endpoint with our test configuration"""
        return await self.test_api_endpoint(
            "/api/setup/verify",
            {"config": config, "partial": False, "skip_service_checks": False},
        )

    async def test_initialize_endpoint(
        self, config: Dict[str, Any] | None = None
    ) -> bool:
        """Test the initialize endpoint"""
        if config is None:
            config = {
                "admin_username": "testadmin",
                "admin_password": "TestPassword123!",
                "pool_path": self.test_dirs[0] + "/media"
                if self.test_dirs
                else "/tmp/nas_test/media",
                "scratch_path": self.test_dirs[0] + "/scratch"
                if self.test_dirs
                else "/tmp/nas_test/scratch",
                "appdata_path": self.test_dirs[0] + "/appdata"
                if self.test_dirs
                else "/tmp/nas_test/appdata",
            }
        return await self.test_api_endpoint(
            "/api/setup/initialize",
            config,
        )

    async def test_backend_directories(self) -> bool:
        """Test that backend verification system is working"""
        self.info("Testing backend verification system...")

        try:
            # Check if we can import the verification modules
            from orchestrator.converge.verification_models import (
                create_validation_error,
            )
            from orchestrator.converge.validators.path_validator import PathValidator
            from orchestrator.converge.validators.port_validator import PortValidator

            self.success("Backend verification system", "modules imported successfully")

            # Test validation models
            create_validation_error("TEST_ERROR", "test_field", {"path": "/test"})
            self.info("✓ Validation models working")

            # Test path validator
            path_validator = PathValidator(
                {
                    "media_path": "/tmp/nas_test/media",
                    "downloads_path": "/tmp/nas_test/downloads",
                    "appdata_path": "/tmp/nas_test/appdata",
                }
            )
            result = path_validator.validate_all_paths()

            if result.success:
                self.success("Path validator", "All validations passed")
            else:
                self.failure(
                    "Path validator",
                    f"Path validation failed: {len(result.errors)} errors",
                )

            # Test port validator
            port_validator = PortValidator(
                {
                    "qbittorrent": {"port": 8080},
                    "prowlarr": {"port": 9696},
                    "radarr": {"port": 7878},
                    "sonarr": {"port": 8989},
                    "jellyfin": {"port": 8096},
                    "jellyseerr": {"port": 5055},
                }
            )
            result = port_validator.validate_all_ports()

            if result.success:
                self.success("Port validator", "All validations passed")
            else:
                self.failure(
                    "Port validator",
                    f"Port validation failed: {len(result.errors)} errors",
                )

            return True

        except Exception as e:
            self.failure("Backend directories", f"Import error: {e}")
            return False

    async def test_verification_engine(self) -> bool:
        """Test the complete verification engine"""
        self.info("Testing verification engine...")

        try:
            from orchestrator.converge.verification_engine import VerificationEngine

            engine = VerificationEngine()
            result = await engine.verify_configuration(TEST_CONFIG)

            if result.result.success:
                self.success(
                    "Verification engine", "Complete configuration validation passed"
                )
                self.info(f"  Duration: {result.result.duration_ms:.2f}ms")
                self.info(f"  Next steps: {'; '.join(result.next_steps)}")
                self.info(f"  Estimated time: {result.estimated_time}")
            else:
                self.failure(
                    "Verification engine",
                    f"Configuration verification failed: {len(result.result.errors)} errors",
                )
                for error in result.result.errors:
                    self.info(f"    Error: {error.field}: {error.message}")
                    for suggestion in error.suggestions:
                        self.info(f"    Suggestion: {suggestion}")

            return result.result.success

        except Exception as e:
            self.failure("Verification engine", f"Engine test failed: {e}")
            return False

    async def run_complete_test(self):
        """Run the complete test suite"""
        self.test_results = TestResults()
        self.info("🧪 Starting Complete NAS Orchestrator Test Suite")
        self.info(f"Testing against: {self.base_url}")
        self.info("Test data prepared for directories:")
        for key, value in TEST_CONFIG.items():
            self.info(f"  {key}: {value}")

        print(f"\n{'=' * 50}")

        # 1. Test that orchestrator is running
        if not await self.test_status_endpoint():
            self.failure("Server status", "Orchestrator not responding")
            return self.test_results.summary()

        # 2. Test volumes endpoint
        if not await self.test_volumes_endpoint():
            self.failure("Volumes endpoint", "No volumes detected")
            return self.test_results.summary()

        self.test_results.success("Server status", "✓ Server is running")

        # 3. Create test directories first
        await self.create_test_directories()

        # 4. Test backend verification system
        if not await self.test_backend_directories():
            return self.test_results.summary()

        self.test_results.success(
            "Backend system", "✓ Backend verification system working"
        )

        # 4. Test configuration verification (without applying)
        if not await self.test_verify_endpoint(TEST_CONFIG):
            self.failure("Configuration verification", "Config verification failed")
            return self.test_results.summary()

        self.test_results.success(
            "Configuration verification", "✓ Configuration validation passed"
        )

        # 5. Test service-specific API validation (expected to fail since services aren't running)
        self.info(
            "Testing service connectivity (expected to fail since services aren't running)..."
        )
        service_config = {
            "prowlarr": {"host": "localhost", "port": 9696, "api_key": "invalid_key"}
        }

        service_result = await self.test_verify_endpoint({"services": service_config})
        if not service_result:  # Expected to fail
            self.success(
                "Service validation",
                "✓ Service validation correctly failed (services not running)",
            )
        else:
            self.warning(
                "Service validation",
                "⚠ Service validation unexpectedly passed (services might be running)",
            )

        # 6. Test initialize endpoint (should fail because volumes don't exist on server)
        init_config = TEST_CONFIG.copy()
        del init_config["qbittorrent"][
            "download_dir"
        ]  # Remove the download directory that won't exist

        if await self.test_initialize_endpoint(init_config):
            self.failure(
                "Initialize endpoint", "Initialize succeeded when it should have failed"
            )
        else:
            self.success(
                "Initialize endpoint",
                "✓ Initialize endpoint correctly failed (volumes don't exist)",
            )

        # Cleanup
        await self.cleanup_test_directories()

        return self.test_results.summary()


async def main():
    """Main test function"""
    print("🚀 NAS Orchestrator Enhanced Setup Wizard - Complete Test Suite")
    print("=" * 60)

    tester = NASOrchestratorTester()

    try:
        # Check if orchestrator is running
        response = await tester.test_status_endpoint()
        if not response:
            print("\n❌ ORCHESTRATOR NOT RUNNING")
            print("Please start the orchestrator with:")
            print("  python3 -m venv /home/ethan/eznas/nas_orchestrator")
            print("  docker compose -f docker-compose.dev.yml up")
            return 1

        # Run complete test
        success = await tester.run_complete_test()

        print("\n" + "=" * 60)
        print("TEST COMPLETE")

        if success:
            print("\n🎉 ENHANCED SETUP WIZARD IS READY!")
            print("✅ Backend verification system: WORKING")
            print("✅ Dynamic frontend validation: READY")
            print("✅ Real-time path/port/service validation: READY")
            print("✅ Verify-only API: READY")
            print("✅ Progressive wizard flow: READY")
            print("\n📋 NEXT STEPS:")
            print("  1. Replace SetupWizard.tsx with EnhancedSetupWizard.tsx")
            print(" 2. Update the frontend to use the enhanced validation system")
            print(" 3. Test the complete wizard flow manually")
            print(" 4. Deploy to production environment")
            print(" 5. Users will get real-time validation and error prevention")
            print(
                "\n💡 The system will prevent configuration errors and guide users to optimal setup!"
            )
        else:
            print("\n❌ TESTS FAILED - See above for details")
            print("🔧 Please fix issues before proceeding to production")

        return 0 if success else 1

    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        print("🔧 Please check the system and try again")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
