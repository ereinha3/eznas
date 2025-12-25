"""Utilities for invoking docker compose commands."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Tuple


class DockerComposeRunner:
    """Wrapper around docker compose for bringing the stack up or down."""

    def __init__(self, compose_path: Path, project_name: str = "nas_orchestrator") -> None:
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









