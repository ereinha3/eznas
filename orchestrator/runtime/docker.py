"""Utilities for invoking docker compose commands."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Tuple


class DockerComposeRunner:
    """Wrapper around docker compose for bringing the stack up or down."""

    def __init__(self, compose_path: Path, project_name: str = "nas_media_stack") -> None:
        self.compose_path = compose_path
        self.project_name = project_name
        self.workdir = compose_path.parent

    def up(self) -> Tuple[bool, str]:
        """Run `docker compose up -d --remove-orphans` and return success + detail."""
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
        return self._run(command)

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
        }
        
        # Find which dev containers are actually running
        running_containers: List[tuple[str, str]] = []  # (service_name, container_name)
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
        
        if not running_containers:
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
                    stopped_services.append(service)
                else:
                    # If stop fails, try kill as a last resort
                    kill_result = subprocess.run(
                        ["docker", "kill", container],
                        capture_output=True,
                        text=True,
                    )
                    if kill_result.returncode == 0:
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









