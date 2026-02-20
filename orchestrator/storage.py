"""Helpers for reading and writing orchestrator configuration and state.

State is split into separate section files for isolation and safety:
  - auth.json:     users, sessions, auth config
  - secrets.json:  per-service API keys and credentials
  - services.json: per-service runtime state (download client IDs, etc.)
  - runs.json:     converge run history
  - pipeline.json: media processing tracker

The legacy monolithic state.json is auto-migrated on first access.
load_state() / save_state() still work by composing all section files
into a single dict for backward compatibility.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

from .models import RunRecord, StageEvent, StackConfig, UserRole

logger = logging.getLogger(__name__)

# Maximum number of run records to keep
MAX_RUN_HISTORY = 20

# State section names → filenames
_STATE_SECTIONS = ("auth", "secrets", "services", "runs", "pipeline")


class ConfigRepository:
    """File-backed persistence for stack configuration and runtime state.

    State is stored in individual section files (auth.json, secrets.json, etc.)
    for isolation: writing secrets never risks corrupting auth, and vice versa.
    Each file uses atomic writes (tmp + fsync + rename) for crash safety.

    Legacy support: if only state.json exists, it's auto-migrated to section
    files on first access. load_state()/save_state() still compose the full
    dict for backward compatibility with existing consumers.
    """

    def __init__(self, root: Path, read_only: bool = False) -> None:
        self.root = root
        self.stack_path = root / "stack.yaml"
        self.generated_dir = root / "generated"
        self.read_only = read_only

        # Legacy monolithic state file (for migration)
        self._legacy_state_path = root / "state.json"

        # Section file paths
        self._section_paths: dict[str, Path] = {
            section: root / f"{section}.json" for section in _STATE_SECTIONS
        }

        self._migrated = False

        if not read_only:
            try:
                self.generated_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------------ Migration

    def _ensure_migrated(self) -> None:
        """Migrate legacy state.json to section files if needed.

        Only runs once per process. If section files already exist,
        this is a no-op. If state.json exists but sections don't,
        it splits the monolithic file into per-section files.
        """
        if self._migrated:
            return
        self._migrated = True

        # Check if any section files exist — if so, migration already done
        has_section_files = any(p.exists() for p in self._section_paths.values())
        if has_section_files:
            return

        # Check for legacy state.json
        if not self._legacy_state_path.exists():
            return

        # Read and migrate
        try:
            legacy_data = json.loads(self._legacy_state_path.read_text())
        except json.JSONDecodeError as e:
            logger.warning(f"Corrupted legacy state.json during migration: {e}")
            content = self._legacy_state_path.read_text()
            legacy_data = self._try_recover_json(content)
            if legacy_data is None:
                logger.error("Cannot recover legacy state.json — starting fresh")
                return

        logger.info("Migrating legacy state.json to section files...")

        for section in _STATE_SECTIONS:
            section_data = legacy_data.get(section)
            if section_data is not None:
                self._save_section(section, section_data)
                logger.info(f"  migrated section: {section}")

        # Rename legacy file to prevent re-migration
        backup = self._legacy_state_path.with_suffix(".json.migrated")
        try:
            os.replace(str(self._legacy_state_path), str(backup))
            logger.info(f"  legacy state.json renamed to {backup.name}")
        except OSError as exc:
            logger.warning(f"Could not rename legacy state.json: {exc}")

    # ------------------------------------------------------------------ Stack config

    def load_stack(self) -> StackConfig:
        if not self.stack_path.exists():
            raise FileNotFoundError(f"Missing stack configuration at {self.stack_path}")
        data = yaml.safe_load(self.stack_path.read_text())
        return StackConfig.model_validate(data)

    def save_stack(self, config: StackConfig) -> None:
        payload = config.model_dump(mode="json")
        self._atomic_write(self.stack_path, yaml.safe_dump(payload, sort_keys=False))

    # ------------------------------------------------------------------ Unified state (backward compat)

    @property
    def state_path(self) -> Path:
        """Legacy accessor — returns the old path for code that references it."""
        return self._legacy_state_path

    def load_state(self) -> dict[str, Any]:
        """Load all sections into a single dict (backward-compatible).

        Composes all section files into one dict. Callers that modify
        the returned dict and call save_state() will write changes back
        to the individual section files.
        """
        self._ensure_migrated()
        state: dict[str, Any] = {}
        for section in _STATE_SECTIONS:
            data = self._load_section(section)
            if data is not None:
                state[section] = data
        return state

    def save_state(self, state: dict[str, Any]) -> None:
        """Decompose state dict and write each section to its own file.

        This is the backward-compatible entry point. Each section is
        written atomically to its own file, so a crash while writing
        one section cannot corrupt another.
        """
        self._ensure_migrated()
        for section in _STATE_SECTIONS:
            section_data = state.get(section)
            if section_data is not None:
                self._save_section(section, section_data)

    # ------------------------------------------------------------------ Section-level I/O

    def _load_section(self, section: str) -> Any | None:
        """Load a single section file, returning None if it doesn't exist."""
        path = self._section_paths[section]
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as e:
            logger.warning(f"Corrupted {path.name}: {e}. Attempting recovery...")
            content = path.read_text()
            recovered = self._try_recover_json(content)
            if recovered is not None:
                backup = path.with_suffix(".json.corrupted")
                path.rename(backup)
                self._save_section(section, recovered)
                logger.info(f"Recovered {path.name}")
                return recovered
            logger.warning(f"Could not recover {path.name}. Starting with empty section.")
            return None

    def _save_section(self, section: str, data: Any) -> None:
        """Write a single section file atomically."""
        path = self._section_paths[section]
        self._atomic_write(path, json.dumps(data, indent=2))

    # ------------------------------------------------------------------ Section accessors
    # Direct access to individual sections — more efficient than load_state()
    # because they only read/write the section they need.

    def load_secrets(self) -> dict[str, dict[str, str]]:
        """Load just the secrets section."""
        self._ensure_migrated()
        return self._load_section("secrets") or {}

    def save_secrets(self, secrets: dict[str, dict[str, str]]) -> None:
        """Save the secrets section."""
        self._ensure_migrated()
        self._save_section("secrets", secrets)

    def get_auth_state(self) -> dict[str, Any]:
        """Get the authentication section."""
        self._ensure_migrated()
        return self._load_section("auth") or {}

    def save_auth_state(self, auth_state: dict[str, Any]) -> None:
        """Save the authentication section."""
        self._ensure_migrated()
        self._save_section("auth", auth_state)

    def load_services_state(self) -> dict[str, Any]:
        """Get the per-service runtime state section."""
        self._ensure_migrated()
        return self._load_section("services") or {}

    def save_services_state(self, services_state: dict[str, Any]) -> None:
        """Save the per-service runtime state section."""
        self._ensure_migrated()
        self._save_section("services", services_state)

    def load_pipeline_state(self) -> dict[str, Any]:
        """Get the pipeline processing state."""
        self._ensure_migrated()
        return self._load_section("pipeline") or {}

    def save_pipeline_state(self, pipeline_state: dict[str, Any]) -> None:
        """Save the pipeline processing state."""
        self._ensure_migrated()
        self._save_section("pipeline", pipeline_state)

    def has_users(self) -> bool:
        """Check if any users exist in auth state."""
        auth = self.get_auth_state()
        return len(auth.get("users", [])) > 0

    # ------------------------------------------------------------------ Atomic I/O

    def _atomic_write(self, path: Path, content: str) -> None:
        """Write content to path atomically using tmp + rename."""
        fd, tmp_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.stem}_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def _try_recover_json(self, content: str) -> dict[str, Any] | None:
        """Try to extract valid JSON from potentially corrupted content."""
        depth = 0
        end_pos = 0
        for i, char in enumerate(content):
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    end_pos = i + 1
                    break

        if end_pos > 0:
            try:
                return json.loads(content[:end_pos])
            except json.JSONDecodeError:
                pass
        return None

    # ------------------------------------------------------------------ Filesystem helpers

    def ensure_directories(self, config: StackConfig) -> list[str]:
        """Ensure required directories exist with correct permissions.

        Attempts to create each directory and make it writable. If any
        directory cannot be created or written to, raises PermissionError
        with the exact commands needed to fix it.

        Returns a list of directories that were created.
        """
        created: list[str] = []
        uid = config.runtime.user_id
        gid = config.runtime.group_id

        def _ensure(path: Path) -> None:
            """Create a directory and verify it's writable."""
            if path.exists():
                if not os.access(path, os.W_OK | os.X_OK):
                    # Try to fix permissions
                    try:
                        os.chmod(str(path), 0o775)
                        if os.getuid() == 0:
                            os.chown(str(path), uid, gid)
                    except OSError:
                        pass  # Will be caught by the re-check below

                    if not os.access(path, os.W_OK | os.X_OK):
                        _raise_permission_error(path)
            else:
                try:
                    path.mkdir(parents=True, exist_ok=True)
                    path.chmod(0o775)
                    if os.getuid() == 0:
                        os.chown(str(path), uid, gid)
                    created.append(str(path))
                except PermissionError:
                    _raise_permission_error(path)

        def _raise_permission_error(path: Path) -> None:
            """Build an actionable error message and raise."""
            import pwd, grp
            try:
                st = path.stat() if path.exists() else path.parent.stat()
                try:
                    owner = pwd.getpwuid(st.st_uid).pw_name
                except KeyError:
                    owner = str(st.st_uid)
                try:
                    group = grp.getgrgid(st.st_gid).gr_name
                except KeyError:
                    group = str(st.st_gid)
                info = f"{owner}:{group} ({oct(st.st_mode)[-3:]})"
            except Exception:
                info = "unknown"

            target = path if path.exists() else path.parent
            fix_cmd = (
                f"sudo chown -R {uid}:{gid} {target} && "
                f"sudo chmod -R 775 {target}"
            )
            raise PermissionError(
                f"Cannot write to {path} (owned by {info}). "
                f"Fix with:\n  {fix_cmd}"
            )

        pool = Path(config.paths.pool)
        appdata = Path(config.paths.appdata)
        scratch_config = config.paths.scratch
        scratch_root = (
            Path(scratch_config) if scratch_config is not None else pool / "downloads"
        )

        # Base directories first — these must succeed
        base_dirs = [pool, appdata]
        if scratch_config is not None:
            base_dirs.append(Path(scratch_config))
        for base in base_dirs:
            _ensure(base)

        # Per-service appdata
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
            if target:
                _ensure(target)

        # Traefik
        if config.proxy.enabled:
            traefik_dir = appdata / "traefik"
            _ensure(traefik_dir)
            _ensure(traefik_dir / "certs")

        # Download & processing directories
        download_root = (
            scratch_root / "downloads" if scratch_config is not None else scratch_root
        )
        for directory in (
            scratch_root,
            download_root,
            download_root / "complete",
            download_root / "incomplete",
            scratch_root / "postproc",
            scratch_root / "transcode",
        ):
            _ensure(directory)

        # Category sub-dirs
        categories = config.download_policy.categories
        complete = download_root / "complete"
        for suffix in (categories.radarr, categories.sonarr):
            _ensure(complete / suffix)

        # Media library
        media_root = pool / "media"
        for section in ("movies", "tv"):
            _ensure(media_root / section)

        return created

    # ------------------------------------------------------------------ Secrets helpers

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

    # ------------------------------------------------------------------ Run history helpers

    def _trim_runs(self, state: dict[str, Any]) -> None:
        """Keep only the most recent MAX_RUN_HISTORY completed runs."""
        runs = state.get("runs", [])
        if len(runs) > MAX_RUN_HISTORY:
            # Keep only the last MAX_RUN_HISTORY entries
            state["runs"] = runs[-MAX_RUN_HISTORY:]

    def start_run(self, run_id: str) -> None:
        state = self.load_state()
        runs = state.setdefault("runs", [])
        runs.append({"run_id": run_id, "ok": None, "events": []})
        self._trim_runs(state)
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
                {
                    "run_id": run_id,
                    "ok": None,
                    "events": [event.model_dump(mode="json")],
                }
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

    def list_runs(self, limit: int = 10) -> list[RunRecord]:
        """Return the most recent runs, newest first."""
        state = self.load_state()
        raw_runs = state.get("runs", [])
        # Runs are stored oldest-first; reverse for newest-first
        recent = raw_runs[-limit:] if limit else raw_runs
        result: list[RunRecord] = []
        for record in reversed(recent):
            events = [
                StageEvent.model_validate(event)
                for event in record.get("events", [])
            ]
            result.append(
                RunRecord(
                    run_id=record.get("run_id", ""),
                    ok=record.get("ok"),
                    events=events,
                    summary=record.get("summary"),
                )
            )
        return result

    # ------------------------------------------------------------------ Admin bootstrap

    def create_default_admin(self, password: Optional[str] = None) -> tuple[str, str]:
        """Create a default admin user if none exist.

        Returns:
            Tuple of (username, password) for the created user.
        """
        import secrets
        from .auth import AuthManager

        state = self.load_state()
        auth_manager = AuthManager(state)

        if auth_manager.has_users():
            raise ValueError("Users already exist")

        username = "admin"
        password = password or secrets.token_urlsafe(12)

        auth_manager.create_user(username, password, role=UserRole.ADMIN)
        self.save_state(state)

        return username, password
