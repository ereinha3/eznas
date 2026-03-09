"""Validation helpers for NAS orchestrator configuration."""
from __future__ import annotations

import re
import socket
import shutil
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from .models import StackConfig, ValidationResult

# Container names that belong to our managed stack.
_STACK_CONTAINER_NAMES = {
    "gluetun",
    "qbittorrent", "radarr", "sonarr", "prowlarr",
    "jellyseerr", "jellyfin", "bazarr", "flaresolverr", "pipeline-worker", "traefik",
    "orchestrator", "orchestrator-dev",
    # Dev-compose variants
    "qbittorrent-dev", "radarr-dev", "sonarr-dev", "prowlarr-dev",
    "jellyseerr-dev", "jellyfin-dev", "bazarr-dev", "flaresolverr-dev", "pipeline-worker-dev",
    "frontend-dev",
}


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
    elif _port_owned_by_stack(config.ui.port):
        checks["ui.port"] = "ok"  # In use by our container
    else:
        user = _identify_port_user(config.ui.port)
        checks["ui.port"] = f"in_use (by {user})"
        overall_ok = False

    if config.proxy.enabled:
        proxy_http_key = "proxy.http_port"
        if _port_available(config.proxy.http_port):
            checks[proxy_http_key] = "ok"
        elif _port_owned_by_stack(config.proxy.http_port):
            checks[proxy_http_key] = "in_use_by_stack"
        else:
            user = _identify_port_user(config.proxy.http_port)
            checks[proxy_http_key] = f"in_use (by {user})"
            overall_ok = False

        https_port = config.proxy.https_port
        proxy_https_key = "proxy.https_port"
        if https_port is None:
            checks[proxy_https_key] = "skipped"
        else:
            if _port_available(int(https_port)):
                checks[proxy_https_key] = "ok"
            elif _port_owned_by_stack(int(https_port)):
                checks[proxy_https_key] = "in_use_by_stack"
            else:
                user = _identify_port_user(int(https_port))
                checks[proxy_https_key] = f"in_use (by {user})"
                overall_ok = False

    port_optional = {"pipeline", "gluetun"}
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
        if _port_owned_by_stack(int(port)):
            checks[key] = "in_use_by_stack"
            continue
        user = _identify_port_user(int(port))
        checks[key] = f"in_use (by {user})"
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


def _port_owned_by_stack(port: int) -> bool:
    """Check if a port is published by any container belonging to our stack.

    Uses ``docker ps --filter publish=PORT`` which catches containers regardless
    of naming convention (compose-prefixed, -dev suffix, etc.).  Falls back to
    direct ``docker inspect`` for a handful of known names when the filter
    approach is unavailable.
    """
    containers = _containers_publishing_port(port)
    if containers:
        for name in containers:
            # Exact match
            if name in _STACK_CONTAINER_NAMES:
                return True
            # Substring match for compose-prefixed names like
            # "nas_orchestrator-qbittorrent-1"
            for stack_name in _STACK_CONTAINER_NAMES:
                if stack_name in name:
                    return True
        return False

    # Fallback: try direct inspect for known container names (handles
    # cases where `docker ps --filter` is unreliable or unavailable).
    known_names = [
        "orchestrator", "orchestrator-dev",
        "traefik",
    ]
    for cname in known_names:
        if _inspect_container_port(cname, port):
            return True
    return False


def _containers_publishing_port(port: int) -> List[str]:
    """Find running container names that publish a given host port."""
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", f"publish={port}", "--format", "{{.Names}}"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return []
    if result.returncode != 0:
        return []
    return [name.strip() for name in result.stdout.strip().splitlines() if name.strip()]


def _inspect_container_port(container_name: str, port: int) -> bool:
    """Check a specific container's published ports via docker inspect."""
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

    for bindings in (ports or {}).values():
        if not bindings:
            continue
        for binding in bindings:
            host_port = binding.get("HostPort")
            if host_port and int(host_port) == port:
                return True
    return False


def _identify_port_user(port: int) -> str:
    """Best-effort identification of what is using a port.

    Returns a human-readable string like ``"process 'filebrowser'"``,
    ``"container 'qbittorrent'"``, or ``"unknown process"`` as a fallback.
    """
    # Check Docker containers first
    containers = _containers_publishing_port(port)
    if containers:
        return f"container '{containers[0]}'"

    # Try host process identification via ss (works if we own the process or are root)
    for cmd in [
        ["ss", "-tlnp", f"sport = {port}"],
        ["sudo", "-n", "ss", "-tlnp", f"sport = {port}"],
    ]:
        try:
            result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=3)
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.splitlines():
                    if f":{port}" in line:
                        match = re.search(r'users:\(\("([^"]+)"', line)
                        if match:
                            return f"process '{match.group(1)}'"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue

    # Try lsof as last resort
    try:
        result = subprocess.run(
            ["lsof", "-i", f":{port}", "-sTCP:LISTEN", "-t"],
            check=False, capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = result.stdout.strip().splitlines()[0]
            # Read process name from /proc
            try:
                comm = Path(f"/proc/{pid}/comm").read_text().strip()
                return f"process '{comm}' (pid {pid})"
            except OSError:
                return f"pid {pid}"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return "another process (run: sudo ss -tlnp 'sport = " + str(port) + "')"


def find_available_port(preferred: int, exclude: set[int] | None = None, max_tries: int = 100) -> int:
    """Find an available host port starting from *preferred*.

    Increments by 1 until it finds a port that is:
      - Not in use by any process
      - OR in use by our own stack (safe to re-bind on redeploy)
      - AND not in the *exclude* set (prevents assigning the same port to
        two services within the same config).

    Returns the first available port, or raises ``RuntimeError`` if none
    found within *max_tries*.
    """
    exclude = exclude or set()
    port = preferred
    for _ in range(max_tries):
        if port not in exclude and (_port_available(port) or _port_owned_by_stack(port)):
            return port
        port += 1
        # Wrap around valid range
        if port > 65535:
            port = 1024
    raise RuntimeError(
        f"Could not find an available port starting from {preferred} "
        f"after {max_tries} attempts"
    )


def resolve_port_conflicts(config: StackConfig) -> tuple[StackConfig, list[str]]:
    """Auto-resolve port conflicts in *config* by bumping to available ports.

    Checks every enabled service port (and the UI/proxy ports).  When a
    conflict with a non-stack process is detected the port is incremented
    until a free one is found.  The *exclude* set ensures no two services
    end up on the same port.

    Returns ``(updated_config, changes)`` where *changes* is a list of
    human-readable strings describing each reassignment (empty if nothing
    changed).
    """
    changes: list[str] = []
    claimed: set[int] = set()

    # Reserve the UI port first (don't auto-change it — it's the orchestrator's own port)
    claimed.add(config.ui.port)

    # Reserve proxy ports if enabled
    if config.proxy.enabled:
        claimed.add(config.proxy.http_port)
        if config.proxy.https_port is not None:
            claimed.add(config.proxy.https_port)

    service_map = {
        "qbittorrent": config.services.qbittorrent,
        "radarr": config.services.radarr,
        "sonarr": config.services.sonarr,
        "prowlarr": config.services.prowlarr,
        "jellyseerr": config.services.jellyseerr,
        "jellyfin": config.services.jellyfin,
        "bazarr": config.services.bazarr,
        "flaresolverr": config.services.flaresolverr,
    }

    for name, svc in service_map.items():
        if not svc.enabled or not svc.port:
            continue

        original = svc.port

        if original in claimed:
            # Internal conflict: two services trying to use the same port
            new_port = find_available_port(original + 1, exclude=claimed)
            svc.port = new_port
            changes.append(f"{name}: {original} → {new_port} (internal conflict)")
            claimed.add(new_port)
        elif not _port_available(original) and not _port_owned_by_stack(original):
            # External conflict: some other process is using this port
            user = _identify_port_user(original)
            new_port = find_available_port(original + 1, exclude=claimed)
            svc.port = new_port
            changes.append(f"{name}: {original} → {new_port} (was {user})")
            claimed.add(new_port)
        else:
            claimed.add(original)

    return config, changes


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








