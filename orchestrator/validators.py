"""Validation helpers for NAS orchestrator configuration."""
from __future__ import annotations

import socket
import shutil
import json
import subprocess
from typing import Dict

from .models import StackConfig, ValidationResult


def run_validation(config: StackConfig) -> ValidationResult:
    """Validate that required paths and ports are usable."""
    checks: Dict[str, str] = {}
    overall_ok = True

    path_entries = [
        ("paths.pool", config.paths.pool, False),
        ("paths.scratch", config.paths.scratch, True),
        ("paths.appdata", config.paths.appdata, False),
    ]

    for label, path, optional in path_entries:
        if path is None:
            checks[label] = "not_configured" if optional else "missing"
            if not optional:
                overall_ok = False
            continue

        if not path.exists():
            checks[label] = "missing"
            overall_ok = False
        elif not path.is_dir():
            checks[label] = "not_directory"
            overall_ok = False
        elif not _is_writable(path):
            checks[label] = "not_writable"
            overall_ok = False
        else:
            checks[label] = "ok"

    port_status = "ok" if _port_available(config.ui.port) else "in_use"
    if port_status != "ok":
        overall_ok = False
    checks["ui.port"] = port_status

    port_optional = {"pipeline"}
    for name, service in config.services.model_dump(mode="python").items():
        port = service.get("port")
        enabled = service.get("enabled", True)
        key = f"services.{name}.port"
        if not enabled:
            checks[key] = "skipped"
            continue
        if not port:
            if name in port_optional:
                checks[key] = "optional"
                continue
            checks[key] = "not_set"
            overall_ok = False
            continue
        if _port_available(int(port)):
            checks[key] = "ok"
            continue
        if _port_owned_by_container(name, int(port)):
            checks[key] = "in_use_by_stack"
            continue
        checks[key] = "in_use"
        overall_ok = False

    docker_cli = shutil.which("docker")
    checks["docker.cli"] = "present" if docker_cli else "missing"

    return ValidationResult(ok=overall_ok, checks=checks)


def _is_writable(path) -> bool:
    return path.exists() and path.is_dir() and os_access(path)


def os_access(path) -> bool:
    import os

    return os.access(path, os.W_OK | os.X_OK)


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        result = sock.connect_ex(("0.0.0.0", port))
        if result == 0:
            return False
    return True


def _port_owned_by_container(container_name: str, port: int) -> bool:
    """Check whether the given port is published by the named Docker container."""
    try:
        result = subprocess.run(
            ["docker", "inspect", container_name, "--format", "{{json .NetworkSettings.Ports}}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False

    if result.returncode != 0 or not result.stdout.strip():
        return False

    try:
        ports = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return False

    for bindings in ports.values():
        if not bindings:
            continue
        for binding in bindings:
            host_port = binding.get("HostPort")
            if host_port and int(host_port) == port:
                return True
    return False












