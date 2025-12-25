"""Base client definitions for orchestrated services."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..models import StackConfig


@dataclass
class EnsureOutcome:
    """Result of running ensure for a service."""

    detail: str = ""
    changed: bool = False
    success: bool = True


class ServiceClient(Protocol):
    """Protocol for service clients with idempotent ensure operations."""

    name: str

    def ensure(self, config: StackConfig) -> EnsureOutcome:
        ...


