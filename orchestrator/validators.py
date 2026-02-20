"""Validation helpers for NAS orchestrator configuration."""
from __future__ import annotations

import socket
import shutil
import json
import subprocess
from pathlib import Path
from typing import Dict

from .models import StackConfig, ValidationResult


def run_validation(config: StackConfig) -> ValidationResult:
    """Validate that required paths and ports are usable."""
    checks: Dict[str, str] = {}
    overall_ok = True

    uid = config.runtime.user_id
    gid = config.runtime.group_id

    # Path mapping: host paths -> container paths (for containerized validation)
    # These match the mounts in docker-compose.dev.yml
    path_mappings = _get_path_mappings()

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

        # Try mapped container path first, then original path
        check_path = _resolve_path(path, path_mappings)

        if not check_path.exists():
            checks[label] = f"missing (run: sudo mkdir -p {path})"
            overall_ok = False
        elif not check_path.is_dir():
            checks[label] = "not_directory"
            overall_ok = False
        elif not _is_writable(check_path):
            fix_cmd = f"sudo chown -R {uid}:{gid} {path} && sudo chmod -R 775 {path}"
            checks[label] = f"not_writable (run: {fix_cmd})"
            overall_ok = False
        else:
            checks[label] = "ok"

    # Check UI port - but recognize if it's in use by ourselves (the orchestrator)
    # The orchestrator is always running when validation happens, so its port will be "in use"
    if _port_available(config.ui.port):
        checks["ui.port"] = "ok"
    elif _is_our_port(config.ui.port):
        checks["ui.port"] = "ok"  # In use by us, that's fine
    elif _port_owned_by_container("orchestrator-dev", config.ui.port) or \
         _port_owned_by_container("orchestrator", config.ui.port):
        checks["ui.port"] = "ok"  # In use by our container
    else:
        checks["ui.port"] = "in_use"
        overall_ok = False

    if config.proxy.enabled:
        proxy_http_key = "proxy.http_port"
        if _port_available(config.proxy.http_port):
            checks[proxy_http_key] = "ok"
        elif _port_owned_by_container("traefik", config.proxy.http_port):
            checks[proxy_http_key] = "in_use_by_stack"
        else:
            checks[proxy_http_key] = "in_use"
            overall_ok = False

        https_port = config.proxy.https_port
        proxy_https_key = "proxy.https_port"
        if https_port is None:
            checks[proxy_https_key] = "skipped"
        else:
            if _port_available(int(https_port)):
                checks[proxy_https_key] = "ok"
            elif _port_owned_by_container("traefik", int(https_port)):
                checks[proxy_https_key] = "in_use_by_stack"
            else:
                checks[proxy_https_key] = "in_use"
                overall_ok = False

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

    # Docker availability check - CLI or socket
    docker_cli = shutil.which("docker")
    docker_socket = Path("/var/run/docker.sock").exists()

    if docker_cli:
        checks["docker.cli"] = "present"
    elif docker_socket:
        # Socket available but no CLI - can still work via API
        checks["docker.cli"] = "socket_only"
    else:
        checks["docker.cli"] = "missing"
        # Don't fail validation for missing docker - it's informational
        # The actual docker operations will fail with clear errors if needed

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


def _is_our_port(port: int) -> bool:
    """Check if this port is the one the orchestrator itself is running on.

    This handles the case where validation runs while the orchestrator is serving requests.
    """
    import os

    # Check common environment variables that uvicorn/gunicorn use
    server_port = os.environ.get("PORT") or os.environ.get("UVICORN_PORT")
    if server_port and int(server_port) == port:
        return True

    # Default orchestrator ports
    if port in (8443, 8000):
        # Check if we're inside the orchestrator process by looking for telltale signs
        # The orchestrator will have loaded FastAPI
        try:
            import sys
            if any("orchestrator" in str(m) for m in sys.modules.keys()):
                return True
        except Exception:
            pass

    return False


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


def _get_path_mappings() -> Dict[str, str]:
    """Get path mappings from environment or use defaults for container detection.

    Returns a dict mapping host path patterns to container paths.
    """
    import os
    from pathlib import Path

    mappings = {}

    # Check for explicit mapping via environment variables
    pool_map = os.environ.get("ORCH_PATH_POOL")
    scratch_map = os.environ.get("ORCH_PATH_SCRATCH")
    appdata_map = os.environ.get("ORCH_PATH_APPDATA")

    if pool_map:
        mappings["pool"] = pool_map
    if scratch_map:
        mappings["scratch"] = scratch_map
    if appdata_map:
        mappings["appdata"] = appdata_map

    # Auto-detect common container paths if they exist
    container_paths = {
        "pool": Path("/data"),
        "scratch": Path("/scratch"),
        "appdata": Path("/appdata"),
    }

    for key, cpath in container_paths.items():
        if key not in mappings and cpath.exists():
            mappings[key] = str(cpath)

    return mappings


def _resolve_path(path, mappings: Dict[str, str]):
    """Resolve a config path to a checkable path, using mappings if in container."""
    from pathlib import Path

    path_str = str(path)

    # Check if any mapping key appears in the path name (e.g., "pool" in "/home/.../test_pool")
    for key, mapped_path in mappings.items():
        # Match by path component containing the key
        if key in path_str.lower() or path_str.endswith(key):
            return Path(mapped_path)

    # Also check for exact container paths that might already be set
    if Path(path_str).exists():
        return Path(path_str)

    # Check if any standard container path exists as fallback
    for key, mapped_path in mappings.items():
        mapped = Path(mapped_path)
        if mapped.exists():
            # Heuristic: if config path contains 'pool', 'scratch', or 'appdata'
            if key in path_str.lower():
                return mapped

    return path












