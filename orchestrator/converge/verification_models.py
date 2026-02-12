from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime


class ValidationError(BaseModel):
    """Represents a single validation error"""

    field: str = Field(..., description="The field that caused the error")
    message: str = Field(..., description="Human-readable error message")
    severity: str = Field(..., description="Error severity: 'error' or 'warning'")
    suggestions: List[str] = Field(
        default_factory=list, description="Suggested fixes for the error"
    )
    code: str = Field(..., description="Unique error code for programmatic handling")


class ClientValidationRule(BaseModel):
    """Client-side validation rules for dynamic UI"""

    field: str = Field(..., description="The field this rule applies to")
    type: str = Field(
        ..., description="Data type: 'string', 'number', 'email', 'url', etc."
    )
    required: bool = Field(False, description="Whether the field is required")
    min_length: Optional[int] = Field(None, description="Minimum length for strings")
    max_length: Optional[int] = Field(None, description="Maximum length for strings")
    pattern: Optional[str] = Field(None, description="Regex pattern for validation")
    min_value: Optional[float] = Field(None, description="Minimum value for numbers")
    max_value: Optional[float] = Field(None, description="Maximum value for numbers")
    custom_rules: List[str] = Field(
        default_factory=list, description="Custom validation rule names"
    )


class ValidationResult(BaseModel):
    """Complete validation result for a configuration"""

    success: bool = Field(..., description="Overall validation success status")
    errors: List[ValidationError] = Field(
        default_factory=list, description="List of validation errors"
    )
    warnings: List[ValidationError] = Field(
        default_factory=list, description="List of validation warnings"
    )
    client_side_rules: Dict[str, ClientValidationRule] = Field(
        default_factory=dict, description="Client-side validation rules"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="When validation was performed"
    )
    duration_ms: Optional[float] = Field(
        None, description="Time taken for validation in milliseconds"
    )

    def has_errors(self) -> bool:
        """Check if there are any errors"""
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        """Check if there are any warnings"""
        return len(self.warnings) > 0

    def get_field_errors(self, field: str) -> List[ValidationError]:
        """Get all errors for a specific field"""
        return [error for error in self.errors if error.field == field]

    def get_field_warnings(self, field: str) -> List[ValidationError]:
        """Get all warnings for a specific field"""
        return [warning for warning in self.warnings if warning.field == field]


class ValidationSummary(BaseModel):
    """Summary view of validation results"""

    total_errors: int = Field(..., description="Total number of errors")
    total_warnings: int = Field(..., description="Total number of warnings")
    success_rate: float = Field(..., description="Percentage of successful validations")
    has_errors: bool = Field(..., description="Whether there are any errors")
    has_warnings: bool = Field(..., description="Whether there are any warnings")

    @classmethod
    def from_validation_result(cls, result: ValidationResult) -> "ValidationSummary":
        """Create summary from full validation result"""
        total = len(result.errors) + len(result.warnings)
        success_rate = 100.0 if total == 0 else (len(result.warnings) / total) * 100

        return cls(
            total_errors=len(result.errors),
            total_warnings=len(result.warnings),
            success_rate=success_rate,
            has_errors=result.has_errors(),
            has_warnings=result.has_warnings(),
        )


class ValidationRequest(BaseModel):
    """Request payload for verification"""

    config: Dict[str, Any] = Field(..., description="Configuration to validate")
    partial: bool = Field(
        False, description="Whether to validate partial configuration"
    )
    skip_service_checks: bool = Field(
        False, description="Skip service connectivity checks"
    )

    class Config:
        arbitrary_types_allowed = True


class ValidationResponse(BaseModel):
    """Response payload for verification"""

    result: ValidationResult = Field(..., description="Validation result")
    next_steps: List[str] = Field(
        default_factory=list, description="Recommended next actions"
    )
    estimated_time: Optional[str] = Field(
        None, description="Estimated time to complete setup"
    )


# Standard error codes
STANDARD_ERROR_CODES = {
    "PATH_NOT_FOUND": {
        "message": "The selected path does not exist",
        "severity": "error",
        "suggestions": [
            "Check the path spelling",
            "Create the directory if it doesn't exist",
            "Select a different path",
        ],
    },
    "PATH_NO_PERMISSION": {
        "message": "Insufficient permissions for this path",
        "severity": "error",
        "suggestions": [
            "Check folder permissions with chmod",
            "Run as administrator",
            "Try a different path",
        ],
    },
    "PORT_IN_USE": {
        "message": "Port {port} is already in use",
        "severity": "error",
        "suggestions": [
            "Stop the service using this port",
            "Choose a different port",
            "Check for conflicting applications",
        ],
    },
    "SERVICE_UNREACHABLE": {
        "message": "Cannot connect to {service} at {endpoint}",
        "severity": "error",
        "suggestions": [
            "Check if the service is running",
            "Verify the endpoint URL",
            "Check network connectivity",
        ],
    },
    "CONFIG_MISSING_DEPENDENCY": {
        "message": "Missing required dependency: {dependency}",
        "severity": "error",
        "suggestions": [
            "Install the missing dependency",
            "Check system requirements",
            "Review the configuration",
        ],
    },
    "INVALID_FORMAT": {
        "message": "Invalid format for {field}",
        "severity": "error",
        "suggestions": [
            "Check the required format",
            "Review the documentation",
            "Use the provided examples",
        ],
    },
    "VALUE_OUT_OF_RANGE": {
        "message": "{field} must be between {min} and {max}",
        "severity": "error",
        "suggestions": [
            "Adjust the value to be within range",
            "Check the requirements",
            "Use the default value",
        ],
    },
}


def create_validation_error(
    code: str, field: str, context: Optional[Dict] = None
) -> ValidationError:
    """Create a ValidationError from a standard error code"""
    if context is None:
        context = {}

    error_info = STANDARD_ERROR_CODES.get(
        code,
        {
            "message": f"Unknown error: {code}",
            "severity": "error",
            "suggestions": ["Contact support for assistance"],
        },
    )

    message = error_info["message"].format(**context)
    suggestions = [s.format(**context) for s in error_info["suggestions"]]

    return ValidationError(
        field=field,
        message=message,
        severity=error_info["severity"],
        suggestions=suggestions,
        code=code,
    )

    message = error_info["message"].format(**context)
    suggestions = [s.format(**context) for s in error_info["suggestions"]]

    return ValidationError(
        field=field,
        message=message,
        severity=error_info["severity"],
        suggestions=suggestions,
        code=code,
    )
