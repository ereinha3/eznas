"""Converge runner orchestrating validation, deployment, and configuration."""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from copy import deepcopy
from typing import List, Optional, Tuple

from ..models import StackConfig, StageEvent
from ..rendering import ComposeRenderer
from ..runtime.docker import DockerComposeRunner
from ..storage import ConfigRepository
from ..validators import run_validation
from .services import ServiceConfigurator


@dataclass
class ApplyRunner:
    repo: ConfigRepository
    renderer: ComposeRenderer
    services: ServiceConfigurator

    def run(self, run_id: str, config: StackConfig) -> Tuple[bool, List[StageEvent]]:
        events: List[StageEvent] = []
        self.repo.start_run(run_id)

        self._record(run_id, events, "validate", "started")
        validation = run_validation(config)
        checks_summary = ", ".join(f"{key}={value}" for key, value in validation.checks.items())
        status = "ok" if validation.ok else "failed"
        self._record(run_id, events, "validate", status, checks_summary)
        if not validation.ok:
            self.repo.finalize_run(run_id, ok=False, summary="Validation failed")
            return False, events

        fs_changes = self.repo.ensure_directories(config)
        fs_detail = ", ".join(fs_changes) if fs_changes else "directories ready"
        self._record(run_id, events, "prepare.paths", "ok", fs_detail)

        secrets_detail, secret_values = self._ensure_secrets(config)
        self._record(run_id, events, "prepare.secrets", "ok", secrets_detail)

        self._record(run_id, events, "render", "started")
        result = self.renderer.render(
            config,
            self.repo.generated_dir,
            secrets=secret_values,
        )
        render_details = [result.compose_path.name, result.env_path.name]
        if result.secret_files:
            render_details.append(f"{len(result.secret_files)} secrets")
        self._record(
            run_id,
            events,
            "render",
            "ok",
            ",".join(render_details),
        )

        self.repo.save_stack(config)
        self._record(run_id, events, "persist", "ok", str(self.repo.stack_path))

        compose_runner = DockerComposeRunner(result.compose_path)
        self._record(run_id, events, "deploy.compose", "started")
        compose_ok, compose_detail = compose_runner.up()
        self._record(
            run_id,
            events,
            "deploy.compose",
            "ok" if compose_ok else "failed",
            compose_detail,
        )
        if not compose_ok:
            self.repo.finalize_run(run_id, ok=False, summary="Compose up failed")
            return False, events

        if not self._wait_for_services(run_id, events, config):
            self.repo.finalize_run(run_id, ok=False, summary="Service readiness failed")
            return False, events

        configured_events = self.services.ensure(config)
        configured_summary = []
        for event in configured_events:
            self._record(run_id, events, event.stage, event.status, event.detail)
            if event.status == "ok" and (event.detail or "").startswith("skipped"):
                continue
            if event.status == "ok":
                configured_summary.append(event.stage.replace("configure.", ""))

        latest_secrets = deepcopy(self.repo.load_state().get("secrets", {}))
        if latest_secrets != secret_values:
            _, secret_files = self.renderer.render_secrets(
                config,
                self.repo.generated_dir,
                secrets=latest_secrets,
            )
            self._record(
                run_id,
                events,
                "render.secrets",
                "ok",
                f"{len(secret_files)} secrets refreshed",
            )
            secret_values = latest_secrets

        summary = "Rendered compose bundle"
        if configured_summary:
            summary += f"; configured {', '.join(configured_summary)}"

        self.repo.finalize_run(run_id, ok=True, summary=summary)
        return True, events

    # ------------------------------------------------------------------ helpers

    def _ensure_secrets(self, config: StackConfig) -> tuple[str, dict[str, dict[str, str]]]:
        state = self.repo.load_state()
        secrets = state.setdefault("secrets", {})
        details: List[str] = []
        state_dirty = False

        def ensure_secret(service: str, key: str, value: Optional[str], message: str) -> None:
            nonlocal state_dirty
            if value is None:
                return
            service_secrets = secrets.setdefault(service, {})
            if service_secrets.get(key) != value:
                service_secrets[key] = value
                details.append(message)
                state_dirty = True

        qb_cfg = config.services.qbittorrent
        ensure_secret("qbittorrent", "username", qb_cfg.username, "qbittorrent username set")
        ensure_secret("qbittorrent", "password", qb_cfg.password, "qbittorrent password set")

        admin_username = "admin"
        admin_password = "adminadmin"

        ensure_secret("jellyseerr", "admin_username", admin_username, "jellyseerr admin username set")
        ensure_secret("jellyseerr", "admin_password", admin_password, "jellyseerr admin password set")

        ensure_secret("jellyfin", "admin_username", admin_username, "jellyfin admin username set")
        ensure_secret("jellyfin", "admin_password", admin_password, "jellyfin admin password set")

        if state_dirty:
            self.repo.save_state(state)

        return (
            ", ".join(details) if details else "secrets unchanged",
            deepcopy(state.get("secrets", {})),
        )

    def _wait_for_services(self, run_id: str, events: List[StageEvent], config: StackConfig) -> bool:
        service_configs = {
            "qbittorrent": config.services.qbittorrent,
            "radarr": config.services.radarr,
            "sonarr": config.services.sonarr,
            "prowlarr": config.services.prowlarr,
            "jellyseerr": config.services.jellyseerr,
            "jellyfin": config.services.jellyfin,
        }
        for name, svc in service_configs.items():
            if not svc.enabled or not svc.port:
                continue
            stage = f"wait.{name}"
            self._record(run_id, events, stage, "started", f"port={svc.port}")
            ok, detail = self._wait_for_port("127.0.0.1", svc.port, timeout=180)
            self._record(run_id, events, stage, "ok" if ok else "failed", detail)
            if not ok:
                return False
        return True

    @staticmethod
    def _wait_for_port(host: str, port: int, timeout: int = 120) -> Tuple[bool, str]:
        deadline = time.monotonic() + timeout
        last_error: Optional[str] = None
        while time.monotonic() < deadline:
            try:
                with socket.create_connection((host, port), timeout=5):
                    return True, "ready"
            except OSError as exc:
                last_error = str(exc)
            time.sleep(3)
        return False, f"timeout waiting for {host}:{port} ({last_error or 'no response'})"

    def _record(
        self,
        run_id: str,
        events: List[StageEvent],
        stage: str,
        status: str,
        detail: str | None = None,
    ) -> None:
        event = StageEvent(stage=stage, status=status, detail=detail)
        events.append(event)
        self.repo.append_run_event(run_id, event)
