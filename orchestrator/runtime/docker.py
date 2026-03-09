"""Utilities for invoking docker compose commands."""
from __future__ import annotations

import logging
import os
import socket
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)


class DockerComposeRunner:
    """Wrapper around docker compose for bringing the stack up or down."""

    def __init__(self, compose_path: Path, project_name: str = "nas_media_stack") -> None:
        self.compose_path = compose_path
        self.project_name = project_name
        self.workdir = compose_path.parent

    def up(self, force_recreate: bool = False) -> Tuple[bool, str]:
        """Run `docker compose up -d --remove-orphans` and return success + detail.

        When *force_recreate* is True, all containers are recreated even if
        their config hasn't changed.  This is needed when VPN (gluetun) is
        enabled because services sharing gluetun's network namespace must be
        recreated whenever gluetun is to avoid stale namespace references.
        """
        command = [
            "docker",
            "compose",
            "-f",
            str(self.compose_path),
            "--project-name",
            self.project_name,
            "up",
            "-d",
            "--remove-orphans",
        ]
        if force_recreate:
            command.append("--force-recreate")
        return self._run(command)

    def join_stack_network(self) -> Tuple[bool, str]:
        """Connect this container to the media stack's Docker network.

        After ``docker compose up`` creates the media stack on its own network
        (e.g. ``nas_media_stack_nas_net``), the orchestrator — which runs in a
        separate dev compose — cannot resolve service hostnames.  This method
        connects the orchestrator container to the stack network so Docker DNS
        works for service-to-service calls.

        Returns (success, detail) — safe to call when already connected or
        when running outside Docker (returns immediately).
        """
        container_name = self._detect_own_container()
        if not container_name:
            return True, "not in container, skipping network join"

        network = f"{self.project_name}_nas_net"

        # Check if already connected
        result = subprocess.run(
            ["docker", "network", "inspect", network, "--format",
             "{{range .Containers}}{{.Name}} {{end}}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and container_name in result.stdout:
            return True, f"already on {network}"

        # Connect
        result = subprocess.run(
            ["docker", "network", "connect", network, container_name],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            log.info("Joined media stack network %s as %s", network, container_name)
            return True, f"joined {network}"

        err = result.stderr.strip()
        if "already exists" in err:
            return True, f"already on {network}"

        log.warning("Failed to join %s: %s", network, err)
        return False, f"failed to join {network}: {err}"

    @staticmethod
    def _detect_own_container() -> Optional[str]:
        """Return this container's name, or None if not running in Docker."""
        # Fast check: /.dockerenv exists in containers
        if not Path("/.dockerenv").exists():
            return None

        # Try hostname (Docker sets it to the short container ID by default)
        hostname = socket.gethostname()

        # Try well-known names first, verify they match our hostname/ID
        for candidate in ("orchestrator-dev", "nas-orchestrator"):
            result = subprocess.run(
                ["docker", "inspect", candidate, "--format", "{{.Config.Hostname}}"],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and result.stdout.strip() == hostname:
                return candidate

        # Fallback: use the hostname (usually the container ID)
        return hostname

    def down(self) -> Tuple[bool, str]:
        command = [
            "docker",
            "compose",
            "-f",
            str(self.compose_path),
            "--project-name",
            self.project_name,
            "down",
        ]
        return self._run(command)

    def _run(self, command: list[str]) -> Tuple[bool, str]:
        env = os.environ.copy()
        env.setdefault("COMPOSE_PROJECT_NAME", self.project_name)
        process = subprocess.run(
            command,
            cwd=str(self.workdir),
            env=env,
            capture_output=True,
            text=True,
        )
        success = process.returncode == 0
        detail = process.stdout.strip() if success else process.stderr.strip()
        if not detail:
            detail = "ok" if success else "failed"
        return success, detail

    @staticmethod
    def stop_conflicting_dev_services(
        enabled_services: List[str], project_root: Path | None = None
    ) -> Tuple[bool, str, List[str]]:
        """
        Stop dev compose services that conflict with enabled services.

        This works both when orchestrator runs locally and in a container.
        When in a container, it stops dev containers directly by name since
        the compose file may not be accessible.

        Args:
            enabled_services: List of service names that will be started (e.g., ['jellyfin', 'qbittorrent'])
            project_root: Optional project root path (for finding docker-compose.dev.yml when running locally).

        Returns:
            Tuple of (success, detail_message, stopped_services)
        """
        if not enabled_services:
            return True, "no services to check", []

        # Map service names to their dev container names
        service_to_dev_container = {
            "qbittorrent": "qbittorrent-dev",
            "radarr": "radarr-dev",
            "sonarr": "sonarr-dev",
            "prowlarr": "prowlarr-dev",
            "jellyseerr": "jellyseerr-dev",
            "jellyfin": "jellyfin-dev",
            "pipeline": "pipeline-worker",  # Old pipeline-worker container
        }
        
        # Find which dev containers exist (running or stopped).
        # A stopped container still blocks the name from being reused.
        running_containers: List[tuple[str, str]] = []  # (service_name, container_name)
        stopped_only: List[tuple[str, str]] = []  # exist but not running
        for service in enabled_services:
            dev_container = service_to_dev_container.get(service)
            if not dev_container:
                continue

            # Check if the dev container is running
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}", "--filter", f"name=^{dev_container}$"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                running_containers.append((service, dev_container))
                continue

            # Check if it exists but is stopped (still blocks the name)
            result = subprocess.run(
                ["docker", "ps", "-a", "--format", "{{.Names}}", "--filter", f"name=^{dev_container}$"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and result.stdout.strip():
                stopped_only.append((service, dev_container))

        # Remove any stopped containers that would block the name
        removed_stopped: List[str] = []
        for service, container in stopped_only:
            result = subprocess.run(
                ["docker", "rm", container],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                removed_stopped.append(container)

        if not running_containers:
            if removed_stopped:
                return True, f"removed {len(removed_stopped)} stopped container(s): {', '.join(removed_stopped)}", []
            return True, "no conflicting dev services running", []
        
        # Try to use docker compose stop if we can find the compose file (local dev)
        # Otherwise, stop containers directly (containerized orchestrator)
        stopped_services: List[str] = []
        
        if project_root is not None:
            dev_compose_path = project_root / "docker-compose.dev.yml"
            if dev_compose_path.exists():
                # Try docker compose stop first (cleaner, removes networks properly)
                service_names = [svc for svc, _ in running_containers]
                command = [
                    "docker",
                    "compose",
                    "-f",
                    str(dev_compose_path),
                    "stop",
                ] + service_names
                
                process = subprocess.run(
                    command,
                    cwd=str(project_root),
                    capture_output=True,
                    text=True,
                )
                
                if process.returncode == 0:
                    stopped_services = service_names
                    # Also remove stopped containers so names are freed
                    for _, container in running_containers:
                        subprocess.run(
                            ["docker", "rm", container],
                            capture_output=True,
                            text=True,
                        )
                # If compose stop fails, fall through to direct container stop
        
        # Stop containers directly by name (works in both local and containerized scenarios)
        # Use docker stop with a timeout to ensure containers stop even if they're hanging
        if not stopped_services:
            for service, container in running_containers:
                # Stop with a 10 second timeout
                result = subprocess.run(
                    ["docker", "stop", "--time", "10", container],
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    # Remove the container so the name is freed for the new
                    # compose project.  Without this, `docker compose up` fails
                    # with "container name already in use".
                    subprocess.run(
                        ["docker", "rm", container],
                        capture_output=True,
                        text=True,
                    )
                    stopped_services.append(service)
                else:
                    # If stop fails, try kill + rm as a last resort
                    kill_result = subprocess.run(
                        ["docker", "kill", container],
                        capture_output=True,
                        text=True,
                    )
                    if kill_result.returncode == 0:
                        subprocess.run(
                            ["docker", "rm", container],
                            capture_output=True,
                            text=True,
                        )
                        stopped_services.append(service)
                    else:
                        # Log but continue with other containers
                        error = result.stderr.strip() or "unknown error"
                        print(f"Warning: failed to stop {container}: {error}")
        
        # Verify containers are actually stopped (wait a moment for ports to be released)
        if stopped_services:
            import time
            time.sleep(1)  # Brief pause to ensure ports are released
            
            # Double-check that containers are stopped
            still_running: List[str] = []
            for service, container in running_containers:
                if service in stopped_services:
                    # Verify it's actually stopped
                    result = subprocess.run(
                        ["docker", "ps", "--format", "{{.Names}}", "--filter", f"name=^{container}$"],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        still_running.append(service)
            
            if still_running:
                detail = f"stopped {len(stopped_services)} dev service(s): {', '.join(stopped_services)} (warning: {', '.join(still_running)} may still be running)"
            else:
                detail = f"stopped {len(stopped_services)} dev service(s): {', '.join(stopped_services)}"
            return True, detail, stopped_services
        else:
            return False, "failed to stop any dev services", []









