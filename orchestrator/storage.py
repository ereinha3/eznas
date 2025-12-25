"""Helpers for reading and writing orchestrator configuration and state."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import yaml

from .models import RunRecord, StageEvent, StackConfig


class ConfigRepository:
    """File-backed persistence for stack configuration and runtime state."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.stack_path = root / "stack.yaml"
        self.state_path = root / "state.json"
        self.generated_dir = root / "generated"
        self.generated_dir.mkdir(parents=True, exist_ok=True)

    def load_stack(self) -> StackConfig:
        if not self.stack_path.exists():
            raise FileNotFoundError(f"Missing stack configuration at {self.stack_path}")
        data = yaml.safe_load(self.stack_path.read_text())
        return StackConfig.model_validate(data)

    def save_stack(self, config: StackConfig) -> None:
        payload = config.model_dump(mode="json")
        yaml.safe_dump(payload, self.stack_path.open("w"), sort_keys=False)

    def load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text())

    def save_state(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state, indent=2))

    # Filesystem helpers ----------------------------------------------------

    def ensure_directories(self, config: StackConfig) -> list[str]:
        """Ensure required directories exist for configured services."""
        changes: list[str] = []
        pool = Path(config.paths.pool)
        appdata = Path(config.paths.appdata)
        scratch_config = config.paths.scratch
        scratch_root = (
            Path(scratch_config) if scratch_config is not None else pool / "downloads"
        )

        base_dirs = [pool, appdata]
        if scratch_config is not None:
            base_dirs.append(Path(scratch_config))

        for base in base_dirs:
            if not base.exists():
                base.mkdir(parents=True, exist_ok=True)
                changes.append(f"created {base}")

        service_dirs = {
            "qbittorrent": appdata / "qbittorrent",
            "radarr": appdata / "radarr",
            "sonarr": appdata / "sonarr",
            "prowlarr": appdata / "prowlarr",
            "jellyseerr": appdata / "jellyseerr",
            "jellyfin": appdata / "jellyfin",
            "pipeline": appdata / "pipeline",
        }

        for name, settings in config.services.model_dump(mode="python").items():
            if not settings.get("enabled", True):
                continue
            target = service_dirs.get(name)
            if target and not target.exists():
                target.mkdir(parents=True, exist_ok=True)
                changes.append(f"created {target}")

        download_root = (
            scratch_root / "downloads" if scratch_config is not None else scratch_root
        )
        complete = download_root / "complete"
        incomplete = download_root / "incomplete"
        postproc = scratch_root / "postproc"
        transcode = scratch_root / "transcode"

        for directory in (scratch_root, download_root, complete, incomplete, postproc, transcode):
            if not directory.exists():
                directory.mkdir(parents=True, exist_ok=True)
                changes.append(f"created {directory}")

        categories = config.download_policy.categories
        for suffix in (categories.radarr, categories.sonarr, categories.anime):
            dest = complete / suffix
            if not dest.exists():
                dest.mkdir(parents=True, exist_ok=True)
                changes.append(f"created {dest}")

        media_root = pool / "media"
        for section in ("movies", "tv", "anime"):
            target = media_root / section
            if not target.exists():
                target.mkdir(parents=True, exist_ok=True)
                changes.append(f"created {target}")

        return changes

    # Secrets helpers --------------------------------------------------------

    def ensure_secret(
        self,
        state: dict[str, Any],
        service: str,
        key: str,
        generator: Callable[[], str],
    ) -> str:
        secrets = state.setdefault("secrets", {})
        service_secrets = secrets.setdefault(service, {})
        value = service_secrets.get(key)
        if not value:
            value = generator()
            service_secrets[key] = value
        return value

    def set_secret(
        self,
        state: dict[str, Any],
        service: str,
        key: str,
        value: str,
    ) -> None:
        secrets = state.setdefault("secrets", {})
        service_secrets = secrets.setdefault(service, {})
        service_secrets[key] = value

    # Run history helpers -------------------------------------------------

    def start_run(self, run_id: str) -> None:
        state = self.load_state()
        runs = state.setdefault("runs", [])
        runs.append({"run_id": run_id, "ok": None, "events": []})
        self.save_state(state)

    def append_run_event(self, run_id: str, event: StageEvent) -> None:
        state = self.load_state()
        runs = state.setdefault("runs", [])
        for record in runs:
            if record["run_id"] == run_id:
                record.setdefault("events", []).append(event.model_dump(mode="json"))
                break
        else:
            runs.append(
                {"run_id": run_id, "ok": None, "events": [event.model_dump(mode="json")]}
            )
        self.save_state(state)

    def finalize_run(self, run_id: str, ok: bool, summary: str | None = None) -> None:
        state = self.load_state()
        runs = state.setdefault("runs", [])
        for record in runs:
            if record["run_id"] == run_id:
                record["ok"] = ok
                if summary:
                    record["summary"] = summary
                break
        else:
            runs.append({"run_id": run_id, "ok": ok, "events": [], "summary": summary})
        self.save_state(state)

    def get_run(self, run_id: str) -> RunRecord | None:
        state = self.load_state()
        for record in state.get("runs", []):
            if record.get("run_id") == run_id:
                events = [
                    StageEvent.model_validate(event)
                    for event in record.get("events", [])
                ]
                return RunRecord(
                    run_id=run_id,
                    ok=record.get("ok"),
                    events=events,
                    summary=record.get("summary"),
                )
        return None

