from typing import Dict, Any, Optional, List
import asyncio
from datetime import datetime

from orchestrator.converge.verification_models import (
    ValidationResult,
    ValidationError,
    ValidationRequest,
    ValidationResponse,
    ValidationSummary,
)
from orchestrator.converge.validators.path_validator import PathValidator
from orchestrator.converge.validators.port_validator import PortValidator
from orchestrator.converge.validators.service_validator import ServiceValidator


class VerificationEngine:
    """Main verification engine that coordinates all validators"""

    def __init__(self):
        self.path_validator = None
        self.port_validator = None
        self.service_validator = None

    async def verify_configuration(
        self,
        config: Dict[str, Any],
        partial: bool = False,
        skip_service_checks: bool = False,
    ) -> ValidationResponse:
        """
        Verify a complete configuration

        Args:
            config: Configuration dictionary to validate
            partial: Whether this is a partial configuration
            skip_service_checks: Skip service connectivity checks

        Returns:
            ValidationResponse with detailed results
        """
        start_time = datetime.utcnow()

        # Initialize validators
        self.path_validator = PathValidator(config)
        self.port_validator = PortValidator(config)
        self.service_validator = ServiceValidator(config)

        # Run all validations
        path_result = self.path_validator.validate_all_paths()
        port_result = self.port_validator.validate_all_ports()

        if skip_service_checks:
            service_result = ValidationResult(success=True, duration_ms=0.0)
        else:
            service_result = await self.service_validator.validate_all_services()

        # Combine results
        combined_result = self._combine_validation_results(
            [path_result, port_result, service_result]
        )

        # Add validation-specific information
        if partial:
            combined_result.warnings.append(
                ValidationError(
                    field="validation_mode",
                    message="This is a partial configuration validation",
                    severity="info",
                    suggestions=["Complete all required fields for full validation"],
                    code="PARTIAL_VALIDATION",
                )
            )

        if skip_service_checks:
            combined_result.warnings.append(
                ValidationError(
                    field="validation_mode",
                    message="Service connectivity checks were skipped",
                    severity="info",
                    suggestions=[
                        "Run with service checks enabled when services are running"
                    ],
                    code="SERVICE_CHECKS_SKIPPED",
                )
            )

        # Calculate total duration
        total_duration = (datetime.utcnow() - start_time).total_seconds() * 1000
        combined_result.duration_ms = total_duration

        # Generate next steps
        next_steps = self._generate_next_steps(combined_result)

        # Estimate setup time
        estimated_time = self._estimate_setup_time(combined_result, config)

        return ValidationResponse(
            result=combined_result, next_steps=next_steps, estimated_time=estimated_time
        )

    def _combine_validation_results(
        self, results: List[ValidationResult]
    ) -> ValidationResult:
        """Combine multiple validation results into one"""
        combined = ValidationResult(
            success=True,
            errors=[],
            warnings=[],
            client_side_rules={},
            timestamp=datetime.utcnow(),
            duration_ms=0.0,
        )

        total_duration = 0.0

        for result in results:
            if not result.success:
                combined.success = False

            combined.errors.extend(result.errors)
            combined.warnings.extend(result.warnings)
            combined.client_side_rules.update(result.client_side_rules)
            total_duration += result.duration_ms or 0.0

        combined.duration_ms = total_duration
        return combined

    def _generate_next_steps(self, result: ValidationResult) -> list[str]:
        """Generate recommended next steps based on validation results"""
        next_steps = []

        if result.has_errors():
            next_steps.append("Fix validation errors before proceeding")
            next_steps.append("Review error messages and suggestions")
        elif result.has_warnings():
            next_steps.append("Review warnings and consider recommendations")
            next_steps.append("Configuration appears valid but may need optimization")
        else:
            next_steps.append("Configuration is valid and ready to apply")
            next_steps.append("You can proceed with service deployment")

        # Specific next steps based on error types
        error_codes = [error.code for error in result.errors]
        warning_codes = [warning.code for warning in result.warnings]

        if "PATH_NOT_FOUND" in error_codes:
            next_steps.append("Create missing directories or select existing paths")

        if "PORT_IN_USE" in error_codes:
            next_steps.append("Stop conflicting services or choose different ports")

        if "SERVICE_UNREACHABLE" in error_codes:
            next_steps.append("Start services before applying configuration")

        if "DEPENDENCY_NOT_FOUND" in error_codes:
            next_steps.append("Install missing system dependencies")

        return next_steps

    def _estimate_setup_time(
        self, result: ValidationResult, config: Dict[str, Any]
    ) -> str:
        """Estimate time required for setup based on configuration"""
        if result.has_errors():
            return "Depends on fixing errors"

        # Count services to deploy
        service_count = sum(
            1
            for service in [
                "qbittorrent",
                "prowlarr",
                "radarr",
                "sonarr",
                "jellyfin",
                "jellyseerr",
                "traefik",
            ]
            if config.get(service)
        )

        # Base time per service
        base_time = service_count * 2  # minutes

        # Add time for initial setup
        if not any(config.get(service) for service in config):
            # Fresh setup
            estimated_minutes = base_time + 5
        else:
            # Updating existing setup
            estimated_minutes = base_time + 2

        # Add buffer for service downloads
        if service_count > 0:
            estimated_minutes += 3  # for Docker image pulls

        return f"~{estimated_minutes} minutes"

    async def verify_paths_only(self, config: Dict[str, Any]) -> ValidationResult:
        """Validate only the paths in configuration"""
        validator = PathValidator(config)
        return validator.validate_all_paths()

    async def verify_ports_only(self, config: Dict[str, Any]) -> ValidationResult:
        """Validate only the ports in configuration"""
        validator = PortValidator(config)
        return validator.validate_all_ports()

    async def verify_service_only(
        self, service: str, config: Dict[str, Any]
    ) -> ValidationResult:
        """Validate only a specific service"""
        validator = ServiceValidator(config)

        if service == "qbittorrent":
            await validator._validate_qbittorrent()
        elif service == "prowlarr":
            await validator._validate_prowlarr()
        elif service == "radarr":
            await validator._validate_radarr()
        elif service == "sonarr":
            await validator._validate_sonarr()
        elif service == "jellyfin":
            await validator._validate_jellyfin()
        elif service == "jellyseerr":
            await validator._validate_jellyseerr()
        elif service == "remux_agent":
            await validator._validate_remux_agent()

        return validator.results

    def get_validation_summary(self, result: ValidationResult) -> ValidationSummary:
        """Get a summary of validation results"""
        return ValidationSummary.from_validation_result(result)
