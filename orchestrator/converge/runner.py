"""Converge runner orchestrating validation, deployment, and configuration."""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from copy import deepcopy
from typing import List, Optional, Tuple

from ..constants import INTERNAL_PORTS, SERVICE_DEPENDENCY_ORDER
from ..models import StackConfig, StageEvent
from ..rendering import ComposeRenderer
from ..runtime.docker import DockerComposeRunner
from ..storage import ConfigRepository
from ..validators import run_validation
from .diff import ConfigDiff, compute_diff
from .services import ServiceConfigurator
from ..proxy import ensure_traefik_assets


@dataclass
class ApplyRunner:
    repo: ConfigRepository
    renderer: ComposeRenderer
    services: ServiceConfigurator

    def preview(self, config: StackConfig) -> ConfigDiff:
        """Compute what would change without actually applying.

        Loads the current saved config and diffs it against the proposed
        config.  Returns a structured diff the frontend can display.
        """
        try:
            current = self.repo.load_stack()
        except FileNotFoundError:
            # No existing config — everything is new; return empty diff
            # since there's nothing to compare against.
            return ConfigDiff()
        return compute_diff(old=current, new=config)

    def run(self, run_id: str, config: StackConfig) -> Tuple[bool, List[StageEvent]]:
        events: List[StageEvent] = []
        self.repo.start_run(run_id)

        # Compute diff before applying so we can record what changed
        diff = self.preview(config)
        if diff.has_changes:
            change_lines = "; ".join(
                f"{c.path}: {c.old_value!r} → {c.new_value!r}" for c in diff.changes[:10]
            )
            if len(diff.changes) > 10:
                change_lines += f" (+{len(diff.changes) - 10} more)"
            self._record(run_id, events, "diff", "ok", change_lines)
        else:
            self._record(run_id, events, "diff", "ok", "no config changes detected")

        self._record(run_id, events, "validate", "started")
        validation = run_validation(config)
        checks_summary = ", ".join(f"{key}={value}" for key, value in validation.checks.items())
        status = "ok" if validation.ok else "failed"
        self._record(run_id, events, "validate", status, checks_summary)
        if not validation.ok:
            self.repo.finalize_run(run_id, ok=False, summary="Validation failed")
            return False, events

        try:
            fs_changes = self.repo.ensure_directories(config)
        except PermissionError as exc:
            self._record(run_id, events, "prepare.paths", "failed", str(exc))
            self.repo.finalize_run(run_id, ok=False, summary="Directory permissions error")
            return False, events
        fs_detail = ", ".join(fs_changes) if fs_changes else "directories ready"
        self._record(run_id, events, "prepare.paths", "ok", fs_detail)

        try:
            _, proxy_detail = ensure_traefik_assets(self.repo, config)
            self._record(run_id, events, "prepare.proxy", "ok", proxy_detail)
        except RuntimeError as exc:
            detail = str(exc)
            self._record(run_id, events, "prepare.proxy", "failed", detail)
            self.repo.finalize_run(run_id, ok=False, summary="Proxy preparation failed")
            return False, events

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

        # Stop conflicting dev compose services before deploying
        enabled_services = []
        if config.services.qbittorrent.enabled:
            enabled_services.append("qbittorrent")
        if config.services.radarr.enabled:
            enabled_services.append("radarr")
        if config.services.sonarr.enabled:
            enabled_services.append("sonarr")
        if config.services.prowlarr.enabled:
            enabled_services.append("prowlarr")
        if config.services.jellyseerr.enabled:
            enabled_services.append("jellyseerr")
        if config.services.jellyfin.enabled:
            enabled_services.append("jellyfin")
        
        if enabled_services:
            self._record(run_id, events, "prepare.conflicts", "started")
            # Use the generated compose path to find project root
            project_root = result.compose_path.parent.parent
            conflict_ok, conflict_detail, _ = DockerComposeRunner.stop_conflicting_dev_services(
                enabled_services, project_root=project_root
            )
            self._record(
                run_id,
                events,
                "prepare.conflicts",
                "ok" if conflict_ok else "warning",
                conflict_detail,
            )

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

        verify_events = self.services.verify(config)
        verify_ok = True
        verified_summary = []
        for event in verify_events:
            self._record(run_id, events, event.stage, event.status, event.detail)
            if event.status == "failed":
                verify_ok = False
            if event.status == "ok" and (event.detail or "").startswith("skipped"):
                continue
            if event.status == "ok":
                verified_summary.append(event.stage.replace("verify.", ""))

        if not verify_ok:
            self.repo.finalize_run(run_id, ok=False, summary="Verification failed")
            return False, events

        summary = "Rendered compose bundle"
        if configured_summary:
            summary += f"; configured {', '.join(configured_summary)}"
        if verified_summary:
            summary += f"; verified {', '.join(verified_summary)}"

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

        # Derive Jellyfin/Jellyseerr admin credentials from the orchestrator's
        # admin account (created during setup wizard), NOT hardcoded defaults.
        # Falls back to existing secrets if already set (idempotent).
        auth_state = state.get("auth", {})
        auth_users = auth_state.get("users", [])
        admin_user = next(
            (u for u in auth_users if u.get("role") == "admin"),
            None,
        )

        if admin_user:
            admin_username = admin_user["username"]
        else:
            # No admin user found — use existing secrets or warn
            admin_username = secrets.get("jellyfin", {}).get("admin_username")

        # For password: we only set it from secrets (never from auth hash).
        # The setup wizard stores the plaintext admin password in secrets
        # on first initialization. After that, it persists there.
        existing_jf_pass = secrets.get("jellyfin", {}).get("admin_password")
        existing_js_pass = secrets.get("jellyseerr", {}).get("admin_password")

        if admin_username:
            ensure_secret("jellyfin", "admin_username", admin_username, "jellyfin admin username set")
            ensure_secret("jellyseerr", "admin_username", admin_username, "jellyseerr admin username set")

        # Only set passwords if they exist in secrets (from setup wizard)
        # Never hardcode fallback passwords
        if existing_jf_pass:
            ensure_secret("jellyfin", "admin_password", existing_jf_pass, "jellyfin admin password set")
        if existing_js_pass:
            ensure_secret("jellyseerr", "admin_password", existing_js_pass, "jellyseerr admin password set")

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
            internal_port = INTERNAL_PORTS.get(name, svc.port)
            self._record(run_id, events, stage, "started", f"container={name}:{internal_port}")
            # Use Docker container name for internal networking
            ok, detail = self._wait_for_port(name, internal_port, timeout=180)
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
