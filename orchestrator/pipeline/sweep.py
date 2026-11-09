"""Library sweep engine — in-place remux for existing library files.

Walks the media library directories and strips unwanted audio/subtitle
tracks from files that don't match the user's media policy.  Reuses the
same ffprobe/ffmpeg pipeline used for new downloads.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, List, Optional, Set

import httpx

from ..models import StackConfig
from ..storage import ConfigRepository
from .languages import arr_language_to_iso
from .remux import (
    StreamInfo,
    SubtitlePolicy,
    TrackSelection,
    _parse_subtitle_track,
    build_remux,
    container_for_path,
    detect_stream_policy_changes,
    probe_raw_streams,
    probe_streams,
    select_subtitles,
    subtitle_identity_from_srt_filename,
)
from .worker import VIDEO_EXTENSIONS, PipelineWorker


def _safe_replace(src: Path, dst: Path) -> None:
    """Replace dst with src, handling cross-device moves.

    Tries atomic ``os.replace`` first (same filesystem).  Falls back to
    ``shutil.copy2`` + ``unlink`` when source and destination are on
    different mount points (EXDEV error).
    """
    try:
        os.replace(src, dst)
    except OSError:
        shutil.copy2(src, dst)
        src.unlink()


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SweepAction:
    """A single file that needs remuxing."""

    path: Path
    size: int
    category: str  # "movies" or "tv"
    unwanted_audio: List[str]
    unwanted_subtitles: List[str]
    selection: TrackSelection


@dataclass
class SweepPlan:
    """Result of a dry-run scan."""

    total_files_scanned: int = 0
    files_already_clean: int = 0
    files_to_process: int = 0
    total_bytes_to_process: int = 0
    estimated_time_seconds: float = 0.0
    actions: List[SweepAction] = field(default_factory=list)


@dataclass
class SweepResult:
    """Result of executing a sweep."""

    total: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    errors: List[str] = field(default_factory=list)


def unwanted_subtitle_streams(
    streams: List[dict],
    selection: TrackSelection,
    *,
    container: str,
) -> List[str]:
    """Describe the subtitle streams a policy remux would change.

    Uses the same selection as the remux command builder.
    """
    _audio, subtitles = detect_stream_policy_changes(
        streams, replace(selection, container=container),
    )
    return subtitles


# ---------------------------------------------------------------------------
# LibrarySweeper
# ---------------------------------------------------------------------------

class LibrarySweeper:
    """Scans and remuxes existing library files to match the media policy."""

    _TRACK_POLICY_VERSION = 2

    # Rough estimate: ~60 MB/s remux throughput (copy codec)
    _REMUX_THROUGHPUT = 60 * 1024 * 1024  # bytes per second

    def __init__(self, config: StackConfig, repo: ConfigRepository) -> None:
        self.config = config
        self.repo = repo
        self._worker = PipelineWorker(config)
        self.pool_root = self._worker.pool_root
        # Cache of original languages from Radarr/Sonarr API
        self._original_languages: Dict[str, Optional[str]] = {}
        self._arr_data_loaded = False

    # ------------------------------------------------------------------
    # Scan (dry-run)
    # ------------------------------------------------------------------

    def scan(self) -> SweepPlan:
        """Walk the library and identify files that need track stripping.

        Returns a SweepPlan with counts and a list of SweepActions.
        This does NOT modify any files.
        """
        plan = SweepPlan()
        swept_state = self._load_swept_state()

        # Pre-load original language data from Radarr/Sonarr
        self._load_arr_original_languages()

        categories = self.config.download_policy.categories
        library_dirs: Dict[str, Path] = {
            categories.radarr: self.pool_root / "movies",
            categories.sonarr: self.pool_root / "tv",
        }

        for category, lib_dir in library_dirs.items():
            if not lib_dir.exists():
                continue
            for file_path in self._walk_video_files(lib_dir):
                plan.total_files_scanned += 1

                # Skip files already swept at current mtime + policy
                policy_version = self._policy_version(
                    self._worker._policy_for_category(category)
                )
                if self._is_already_swept(file_path, swept_state, policy_version):
                    plan.files_already_clean += 1
                    continue

                action = self._build_action_for_file(file_path, category)
                if action is None:
                    plan.files_already_clean += 1
                    continue

                plan.files_to_process += 1
                plan.total_bytes_to_process += action.size
                plan.actions.append(action)

        # Estimate time based on throughput
        if plan.total_bytes_to_process > 0:
            plan.estimated_time_seconds = (
                plan.total_bytes_to_process / self._REMUX_THROUGHPUT
            )

        return plan

    def scan_targets(
        self,
        target_paths: List[str | Path],
        *,
        skip_swept: bool = False,
    ) -> SweepPlan:
        """Scan an explicit target list instead of the whole library.

        This is used by staged remediation batches generated from a media
        audit.  The check still probes current files, so stale audit entries
        do not cause unnecessary remuxes.
        """
        plan = SweepPlan()
        swept_state = self._load_swept_state()
        self._load_arr_original_languages()

        seen: Set[Path] = set()
        for raw_path in target_paths:
            file_path = self._resolve_target_path(Path(raw_path))
            if file_path in seen:
                continue
            seen.add(file_path)
            plan.total_files_scanned += 1

            if not file_path.exists() or file_path.suffix.lower() not in VIDEO_EXTENSIONS:
                plan.files_already_clean += 1
                continue

            category = self._category_for_library_path(file_path)
            if category is None:
                plan.files_already_clean += 1
                continue

            policy_version = self._policy_version(
                self._worker._policy_for_category(category)
            )
            if skip_swept and self._is_already_swept(file_path, swept_state, policy_version):
                plan.files_already_clean += 1
                continue

            action = self._build_action_for_file(file_path, category)
            if action is None:
                plan.files_already_clean += 1
                continue

            plan.files_to_process += 1
            plan.total_bytes_to_process += action.size
            plan.actions.append(action)

        if plan.total_bytes_to_process > 0:
            plan.estimated_time_seconds = (
                plan.total_bytes_to_process / self._REMUX_THROUGHPUT
            )

        return plan

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    def execute(
        self,
        plan: SweepPlan,
        *,
        progress_callback=None,
        should_cancel=None,
    ) -> SweepResult:
        """Remux each file in the plan, replacing it in-place.

        Args:
            plan: A SweepPlan from scan().
            progress_callback: Optional callable(current: int, total: int, path: str)
                called after each file for UI progress updates.

        Returns a SweepResult with counts and error details.
        """
        result = SweepResult(total=len(plan.actions))
        scratch_root = self._resolve_scratch()
        use_scratch = scratch_root is not None and scratch_root.exists()

        for i, action in enumerate(plan.actions):
            if should_cancel and should_cancel():
                result.skipped += result.total - i
                result.errors.append("cancelled")
                break

            if progress_callback:
                progress_callback(i, result.total, str(action.path))

            staging_path = self._staging_path(action.path, use_scratch, scratch_root)

            try:
                # Ensure staging directory exists
                staging_path.parent.mkdir(parents=True, exist_ok=True)

                # Look up original language for this file
                original_language = self._get_original_language_for_file(
                    action.path, action.category
                )

                # Build and run ffmpeg.  If the file cannot be probed the
                # builder would map no subtitles; for an in-place rewrite of
                # a library file that is destructive, so abort instead.
                build = build_remux(
                    action.path, staging_path, action.selection,
                    original_language=original_language,
                )
                if build.notes.probe_failed:
                    result.failed += 1
                    result.errors.append(f"probe failed, not rewriting: {action.path}")
                    continue
                success = self._run_ffmpeg(build.command)

                if not success:
                    result.failed += 1
                    result.errors.append(f"ffmpeg failed: {action.path}")
                    self._cleanup(staging_path)
                    continue

                # Verify output exists and has reasonable size
                if not staging_path.exists():
                    result.failed += 1
                    result.errors.append(f"Output missing: {action.path}")
                    continue

                staged_size = staging_path.stat().st_size
                if staged_size < 1024:
                    result.failed += 1
                    result.errors.append(
                        f"Output too small ({staged_size} bytes): {action.path}"
                    )
                    self._cleanup(staging_path)
                    continue

                # Atomic replace: staging -> original
                _safe_replace(Path(str(staging_path)), action.path)
                result.succeeded += 1

                # Mark as swept under the current policy
                self._mark_swept(
                    action.path, "ok", self._policy_version(action.selection),
                )

            except Exception as exc:
                result.failed += 1
                result.errors.append(f"{action.path}: {exc}")
                self._cleanup(staging_path)

        # Clean up scratch sweep directory
        if use_scratch and scratch_root is not None:
            sweep_dir = scratch_root / "postproc"
            self._cleanup_empty_dirs(sweep_dir)

        # Trigger Sonarr/Radarr rescan if any files were processed
        if result.succeeded > 0:
            self._trigger_arr_rescans()

        if progress_callback:
            progress_callback(result.total, result.total, "")

        return result

    # ------------------------------------------------------------------
    # Detection logic
    # ------------------------------------------------------------------

    def _detect_unwanted(
        self,
        info: StreamInfo,
        selection: TrackSelection,
        *,
        original_language: Optional[str] = None,
    ) -> tuple[List[str], List[str]]:
        """Determine which audio/subtitle languages should be stripped.

        Args:
            info: Stream metadata from ffprobe.
            selection: User's language preferences.
            original_language: ISO 639 code from Radarr/Sonarr API.
                When provided, overrides the unreliable first-audio-track
                heuristic from probe_streams().

        Returns (unwanted_audio, unwanted_subtitles).
        """
        keep_audio: Set[str] = set(lang.lower() for lang in selection.audio)
        keep_audio.add("und")  # Always keep undetermined

        # Use API-provided original language (reliable) if available,
        # fall back to probe_streams heuristic (unreliable) otherwise
        orig = original_language or info.original_language
        if orig:
            keep_audio.add(orig.lower())

        keep_subs: Set[str] = set(lang.lower() for lang in selection.subtitles)
        # "forced" is a disposition flag, not a language — remove from set
        keep_subs.discard("forced")

        unwanted_audio = [
            lang for lang in sorted(info.audio_languages)
            if lang.lower() not in keep_audio
        ]
        unwanted_subs = [
            lang for lang in sorted(info.subtitle_languages)
            if lang.lower() not in keep_subs
        ]

        return unwanted_audio, unwanted_subs

    def _build_action_for_file(
        self,
        file_path: Path,
        category: str,
    ) -> Optional[SweepAction]:
        """Return a SweepAction when the current file violates track policy."""
        stream_info = probe_streams(file_path)
        if stream_info is None or not stream_info.has_video:
            return None

        # An existing file keeps its own container (no mkv <-> mp4 rewrite).
        selection = replace(
            self._worker._policy_for_category(category),
            container=container_for_path(file_path),
        )
        original_language = self._get_original_language_for_file(file_path, category)

        # The raw-stream policy catches duplicate same-language tracks,
        # subtitle limits and stale subtitle default flags, not only
        # unwanted languages.
        raw_streams = probe_raw_streams(file_path)
        if raw_streams:
            unwanted_audio, unwanted_subs = detect_stream_policy_changes(
                raw_streams,
                selection,
                original_language=original_language,
            )
        else:
            unwanted_audio, unwanted_subs = self._detect_unwanted(
                stream_info,
                selection,
                original_language=original_language,
            )

        if not unwanted_audio and not unwanted_subs:
            return None

        return SweepAction(
            path=file_path,
            size=file_path.stat().st_size,
            category=category,
            unwanted_audio=unwanted_audio,
            unwanted_subtitles=unwanted_subs,
            selection=selection,
        )

    # ------------------------------------------------------------------
    # Original language lookup from Radarr/Sonarr API
    # ------------------------------------------------------------------

    def _load_arr_original_languages(self) -> None:
        """Pre-load original language data from Radarr and Sonarr APIs.

        Builds a cache mapping file paths to ISO 639 codes so we don't
        need per-file API calls during the scan.
        """
        if self._arr_data_loaded:
            return
        self._arr_data_loaded = True

        secrets = self.repo.load_secrets()
        categories = self.config.download_policy.categories

        # Load from Radarr (movies)
        radarr_key = secrets.get("radarr", {}).get("api_key")
        if radarr_key:
            self._load_radarr_languages(radarr_key, categories.radarr)

        # Load from Sonarr (TV)
        sonarr_key = secrets.get("sonarr", {}).get("api_key")
        if sonarr_key:
            self._load_sonarr_languages(sonarr_key, categories.sonarr)

        print(
            f"[sweep] loaded original languages for "
            f"{len(self._original_languages)} titles"
        )

    def _load_radarr_languages(self, api_key: str, _category: str) -> None:
        """Fetch all movies from Radarr and cache their original languages."""
        from ..models import VPN_ROUTED_SERVICES
        host = "gluetun" if (self.config.services.gluetun.enabled and "radarr" in VPN_ROUTED_SERVICES) else "radarr"
        try:
            response = httpx.get(
                f"http://{host}:7878/api/v3/movie",
                headers={"X-Api-Key": api_key},
                timeout=httpx.Timeout(30.0, connect=5.0),
            )
            response.raise_for_status()
            movies = response.json()
        except Exception as exc:
            print(f"[sweep] failed to load Radarr movies: {exc}")
            return

        for movie in movies:
            orig_lang = movie.get("originalLanguage", {})
            lang_name = orig_lang.get("name", "")
            iso_code = arr_language_to_iso(lang_name) if lang_name else None
            if iso_code and iso_code != "und":
                # Map by multiple keys for robust matching
                folder = movie.get("folderName") or movie.get("path", "")
                title = movie.get("title", "")
                if folder:
                    folder_path = Path(folder)
                    # Store the full Docker-internal path
                    self._original_languages[str(folder_path)] = iso_code
                    # Also store just the folder name (matches across mount points)
                    self._original_languages[folder_path.name.lower()] = iso_code
                if title:
                    self._original_languages[title.lower()] = iso_code
                # Also store alternate titles if available
                for alt in movie.get("alternateTitles", []):
                    alt_title = alt.get("title", "")
                    if alt_title:
                        self._original_languages[alt_title.lower()] = iso_code

    def _load_sonarr_languages(self, api_key: str, _category: str) -> None:
        """Fetch all series from Sonarr and cache their original languages."""
        from ..models import VPN_ROUTED_SERVICES
        host = "gluetun" if (self.config.services.gluetun.enabled and "sonarr" in VPN_ROUTED_SERVICES) else "sonarr"
        try:
            response = httpx.get(
                f"http://{host}:8989/api/v3/series",
                headers={"X-Api-Key": api_key},
                timeout=httpx.Timeout(30.0, connect=5.0),
            )
            response.raise_for_status()
            series_list = response.json()
        except Exception as exc:
            print(f"[sweep] failed to load Sonarr series: {exc}")
            return

        for series in series_list:
            orig_lang = series.get("originalLanguage", {})
            lang_name = orig_lang.get("name", "")
            iso_code = arr_language_to_iso(lang_name) if lang_name else None
            if iso_code and iso_code != "und":
                folder = series.get("path", "")
                title = series.get("title", "")
                if folder:
                    folder_path = Path(folder)
                    self._original_languages[str(folder_path)] = iso_code
                    self._original_languages[folder_path.name.lower()] = iso_code
                if title:
                    self._original_languages[title.lower()] = iso_code
                for alt in series.get("alternateTitles", []):
                    alt_title = alt.get("title", "")
                    if alt_title:
                        self._original_languages[alt_title.lower()] = iso_code

    def _get_original_language_for_file(
        self, file_path: Path, _category: str
    ) -> Optional[str]:
        """Look up the original language for a library file.

        Tries multiple matching strategies in order of specificity:
        1. Full path match (Docker-internal path)
        2. Folder name match (cross-mount-point)
        3. Title-only match (stripped of year, case-insensitive)
        4. Fuzzy title match (check if any cached title is contained in folder name)
        """
        # Strategy 1: Direct path match (file's parent or grandparent)
        for ancestor in (file_path.parent, file_path.parent.parent):
            key = str(ancestor)
            if key in self._original_languages:
                return self._original_languages[key]

        parent_name = file_path.parent.name.lower()

        # Strategy 2: Folder name match (case-insensitive)
        if parent_name in self._original_languages:
            return self._original_languages[parent_name]

        # Strategy 3: Strip year suffix — "Parasite (2019)" -> "parasite"
        title_part = re.sub(r'\s*\(\d{4}\)\s*$', '', parent_name).strip()
        if title_part in self._original_languages:
            return self._original_languages[title_part]

        # Strategy 4: Strip common suffixes (REMASTERED, REMUX, etc.)
        cleaned = re.sub(
            r'\s*(remastered|remux|bdremux|extended|directors\s*cut|unrated)\s*',
            '', title_part, flags=re.IGNORECASE,
        ).strip()
        if cleaned and cleaned in self._original_languages:
            return self._original_languages[cleaned]

        # Strategy 5: Check if any cached title is a substring of the folder name
        # This catches cases like "Drunken Master REMASTERED" matching "drunken master"
        for cached_title, lang in self._original_languages.items():
            if len(cached_title) >= 4 and cached_title in parent_name:
                return lang

        return None

    # ------------------------------------------------------------------
    # File walking
    # ------------------------------------------------------------------

    def _walk_video_files(self, directory: Path):
        """Yield all video files under directory, recursively."""
        for root, _dirs, files in os.walk(directory):
            for name in sorted(files):
                # Skip hidden/temp files
                if name.startswith("."):
                    continue
                if Path(name).suffix.lower() in VIDEO_EXTENSIONS:
                    yield Path(root) / name

    def _resolve_target_path(self, path: Path) -> Path:
        """Map audit paths to the current runtime library mount when needed."""
        if path.exists():
            return path

        configured_pool = Path(self.config.paths.pool)
        try:
            relative = path.relative_to(configured_pool)
        except ValueError:
            return path

        candidate = self.pool_root / relative
        if candidate.exists():
            return candidate
        return path

    def _category_for_library_path(self, path: Path) -> Optional[str]:
        """Infer the configured media category for a library file path."""
        categories = self.config.download_policy.categories
        roots: Dict[str, List[Path]] = {
            categories.radarr: [
                self.pool_root / "movies",
                Path(self.config.paths.pool) / "movies",
            ],
            categories.sonarr: [
                self.pool_root / "tv",
                Path(self.config.paths.pool) / "tv",
            ],
            categories.anime: [
                self.pool_root / "anime",
                Path(self.config.paths.pool) / "anime",
            ],
        }

        for category, candidates in roots.items():
            for root in candidates:
                try:
                    path.relative_to(root)
                    return category
                except ValueError:
                    continue
        return None

    # ------------------------------------------------------------------
    # Swept state tracking
    # ------------------------------------------------------------------

    def _load_swept_state(self) -> dict:
        """Load the 'swept' key from pipeline state."""
        pipeline = self.repo.load_pipeline_state()
        return pipeline.get("swept", {})

    def _policy_version(self, selection: TrackSelection) -> str | int:
        """Swept-state version: changes when the subtitle policy changes.

        The default policy keeps the plain version number, so records
        written before configurable policies stay valid.
        """
        policy = selection.subtitle_policy
        if policy == SubtitlePolicy():
            return self._TRACK_POLICY_VERSION
        key = repr((
            policy.max_per_language,
            [p.lower() for p in policy.prefer],
            policy.keep_forced,
            policy.keep_sdh,
        ))
        digest = hashlib.sha256(key.encode()).hexdigest()[:12]
        return f"{self._TRACK_POLICY_VERSION}:{digest}"

    def _is_already_swept(
        self, path: Path, swept_state: dict, policy_version: str | int,
    ) -> bool:
        """Check if a file was already swept at its current mtime and policy."""
        key = str(path)
        record = swept_state.get(key)
        if record is None:
            return False
        try:
            current_mtime = path.stat().st_mtime
            return (
                record.get("mtime") == current_mtime
                and record.get("status") == "ok"
                and record.get("policy_version") == policy_version
            )
        except OSError:
            return False

    def _mark_swept(
        self, path: Path, status: str, policy_version: str | int,
    ) -> None:
        """Record a file as swept in pipeline state."""
        pipeline = self.repo.load_pipeline_state()
        swept = pipeline.setdefault("swept", {})
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        swept[str(path)] = {
            "status": status,
            "mtime": mtime,
            "policy_version": policy_version,
            "timestamp": int(time.time()),
        }
        self.repo.save_pipeline_state(pipeline)

    # ------------------------------------------------------------------
    # Staging paths
    # ------------------------------------------------------------------

    def _staging_path(
        self,
        source: Path,
        use_scratch: bool,
        scratch_root: Optional[Path],
    ) -> Path:
        """Compute a staging path for the remuxed output.

        With scratch disk: /scratch/postproc/sweep-{hash12}/filename.ext
        Without scratch:   source.parent/.filename.tmp.ext (same container)
        """
        if use_scratch and scratch_root is not None:
            hash_input = str(source).encode()
            hash12 = hashlib.sha256(hash_input).hexdigest()[:12]
            return scratch_root / "postproc" / f"sweep-{hash12}" / source.name
        else:
            # Dot-prefix hides from media scanners
            return source.parent / f".{source.stem}.tmp{source.suffix}"

    def _resolve_scratch(self) -> Optional[Path]:
        """Resolve the scratch root, preferring container mounts."""
        container_candidates = [Path("/scratch")]
        for candidate in container_candidates:
            if candidate.exists():
                return candidate
        scratch = self.config.paths.scratch
        if scratch is not None:
            p = Path(scratch)
            if p.exists():
                return p
        return None

    # ------------------------------------------------------------------
    # FFmpeg execution
    # ------------------------------------------------------------------

    def _run_ffmpeg(self, command: List[str]) -> bool:
        """Run an ffmpeg command with timeout. Returns True on success."""
        try:
            result = subprocess.run(
                command, check=False, capture_output=True, text=True,
                timeout=3600,  # 1 hour max per file
            )
        except subprocess.TimeoutExpired:
            print("[sweep] ffmpeg timed out after 1 hour")
            return False
        except OSError as exc:
            print(f"[sweep] ffmpeg failed to start: {exc}")
            return False

        if result.returncode != 0:
            stderr = result.stderr.strip()
            lines = stderr.split("\n")
            tail = "\n".join(lines[-5:]) if len(lines) > 5 else stderr
            print(f"[sweep] ffmpeg error (exit {result.returncode}): {tail}")
            return False
        return True

    # ------------------------------------------------------------------
    # Cleanup helpers
    # ------------------------------------------------------------------

    def _cleanup(self, path: Path) -> None:
        """Remove a staging file if it exists."""
        try:
            if path.exists():
                path.unlink()
        except OSError as exc:
            print(f"[sweep] cleanup failed for {path}: {exc}")

    def _cleanup_empty_dirs(self, directory: Path) -> None:
        """Remove empty sweep-* subdirectories."""
        if not directory.exists():
            return
        try:
            for child in directory.iterdir():
                if child.is_dir() and child.name.startswith("sweep-"):
                    try:
                        child.rmdir()  # Only removes if empty
                    except OSError:
                        pass
        except OSError:
            pass

    # ------------------------------------------------------------------
    # Arr service refresh
    # ------------------------------------------------------------------

    def _trigger_arr_rescans(self) -> None:
        """Tell Sonarr and Radarr to rescan their libraries."""
        secrets = self.repo.load_secrets()

        services = [
            ("sonarr", 8989, "/api/v3", "RescanSeries"),
            ("radarr", 7878, "/api/v3", "RescanMovie"),
        ]

        from ..models import VPN_ROUTED_SERVICES
        vpn_active = self.config.services.gluetun.enabled

        for name, port, prefix, command in services:
            api_key = secrets.get(name, {}).get("api_key")
            if not api_key:
                print(f"[sweep] no API key for {name}, skipping rescan")
                continue
            host = "gluetun" if (vpn_active and name in VPN_ROUTED_SERVICES) else name
            try:
                response = httpx.post(
                    f"http://{host}:{port}{prefix}/command",
                    json={"name": command},
                    headers={"X-Api-Key": api_key},
                    timeout=httpx.Timeout(10.0, connect=5.0),
                )
                response.raise_for_status()
                print(f"[sweep] triggered {command} on {name}")
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                print(f"[sweep] {name} rescan failed: {exc}")

    # ------------------------------------------------------------------
    # Subtitle sweep — embed orphaned .srt files into MKV containers
    # ------------------------------------------------------------------

    def scan_orphaned_srt(self) -> List[dict]:
        """Scan the library for external .srt files that can be embedded.

        Returns a list of dicts describing each SRT and its parent video:
            [{
                "video": Path,
                "srt": Path,
                "language": str,
                "entries": int,
            }, ...]
        """
        from .subtitle_align import parse_srt, probe_video_duration, validate_timing

        results: List[dict] = []
        # pool_root is /data inside container, media is at /data/tv and /data/movies
        # (no /data/media/ subdirectory in the container mount)
        media_root = self.pool_root / "media"
        if not media_root.exists():
            media_root = self.pool_root  # Fallback: scan pool_root directly

        for video_path in self._walk_video_files(media_root):
            video_base = video_path.stem
            srt_files = list(video_path.parent.glob(f"{video_base}*.srt"))
            if not srt_files:
                continue  # No SRTs — skip without probing video

            # Probe video duration once per video (only if SRTs exist)
            duration: Optional[float] = None
            _MIN_CUES_PER_MINUTE = 3

            for srt_path in srt_files:
                if srt_path.stat().st_size == 0:
                    continue

                entries = parse_srt(srt_path)
                if not entries:
                    continue

                identity = subtitle_identity_from_srt_filename(srt_path, video_base)

                # Probe video lazily — only on first valid SRT
                if duration is None:
                    duration = probe_video_duration(video_path)
                    if duration is None:
                        break  # Can't validate any SRTs for this video

                validation = validate_timing(entries, duration)
                if not validation.is_valid:
                    continue

                # Reject signs-only / sparse subtitles.
                cues_per_min = len(entries) / max(duration / 60, 1)
                if cues_per_min < _MIN_CUES_PER_MINUTE:
                    print(
                        f"[sweep/srt] skipping {srt_path.name} — too sparse "
                        f"({len(entries)} cues in {duration/60:.0f}min = "
                        f"{cues_per_min:.1f}/min, need {_MIN_CUES_PER_MINUTE})"
                    )
                    continue

                results.append({
                    "video": video_path,
                    "srt": srt_path,
                    "language": identity.lang,
                    "kind": identity.kind,
                    "entries": len(entries),
                })

        print(f"[sweep] found {len(results)} orphaned SRT files to embed")
        return results

    def _srts_allowed_by_policy(
        self,
        video_path: Path,
        srt_items: List[dict],
        rejected: Optional[List[dict]] = None,
    ) -> List[dict]:
        """Return the SRTs the subtitle policy would keep once embedded.

        An SRT is skipped (and appended to ``rejected``) when the file
        already has a track filling its policy slot, in any codec, including
        an identical duplicate.  If the video cannot be probed nothing is
        embedded and nothing is rejected (fail closed), so a slow probe can
        no longer let duplicates through.
        """
        existing = probe_raw_streams(video_path)
        if existing is None:
            print(f"[sweep/srt] cannot probe {video_path.name}; not embedding")
            return []
        category = (
            self._category_for_library_path(video_path)
            or self.config.download_policy.categories.radarr
        )
        selection = replace(self._worker._policy_for_category(category), container="mkv")
        tracks = [
            t for t in (_parse_subtitle_track(s) for s in existing) if t is not None
        ]
        accepted: List[dict] = []
        for n, item in enumerate(srt_items):
            kind = item.get("kind") or subtitle_identity_from_srt_filename(
                item["srt"], video_path.stem,
            ).kind
            candidate = _parse_subtitle_track(
                {
                    "index": f"srt{n}",
                    "codec_type": "subtitle",
                    "codec_name": "subrip",
                    "tags": {"language": item["language"]},
                    "disposition": {
                        "forced": int(kind == "forced"),
                        "hearing_impaired": int(kind == "hearing_impaired"),
                    },
                },
                input_index=1,
                origin=1,
            )
            assert candidate is not None
            if candidate in select_subtitles(tracks + [candidate], selection).selected:
                tracks.append(candidate)
                accepted.append({**item, "kind": kind})
            else:
                print(
                    f"[sweep/srt] skipping {item['srt'].name}: policy slot for "
                    f"{item['language']} {kind} already filled"
                )
                if rejected is not None:
                    rejected.append(item)
        return accepted

    def _embed_srt_for_video(
        self,
        video_path: Path,
        srt_items: List[dict],
        scratch: Optional[Path] = None,
    ) -> int:
        """Embed all SRT files for a single video using mkvmerge.

        Uses mkvmerge instead of ffmpeg because ffmpeg's matroska muxer
        re-clusters streams during ``-c copy``, which can corrupt HEVC files
        carrying Dolby Vision RPU side-data or TrueHD/Atmos substreams (the
        Bug's Life mid-playback freeze was caused by exactly this path).
        mkvmerge passes through video/audio bitstreams byte-for-byte and
        only muxes the SRT track in.
        """
        # Idempotency guard: embed only SRTs the subtitle policy would keep.
        # SRTs whose slot is already filled (any codec) are deleted so Bazarr
        # re-downloads cannot re-add the same subtitle every cycle.  If the
        # video cannot be probed nothing is embedded or deleted.
        # mkvmerge writes MKV; never replace an MP4 library file with it.
        if video_path.suffix.lower() != ".mkv":
            print(f"[sweep/srt] skipped {video_path.name} — not MKV")
            return 0

        rejected: List[dict] = []
        srt_items = self._srts_allowed_by_policy(video_path, srt_items, rejected)
        for item in rejected:
            try:
                item["srt"].unlink()
            except OSError:
                pass
        if not srt_items:
            print(f"[sweep/srt] skipped {video_path.name} — nothing to embed under policy")
            return 0

        # Dot-prefix hides from media scanners (Radarr/Sonarr) so they
        # don't index the temp file and mark the movie as deleted when it
        # gets cleaned up.
        output_path = video_path.parent / f".{video_path.stem}.srtremux.tmp.mkv"

        # mkvmerge syntax: each --language flag applies to the *next* input file's
        # track 0 (SRT files are always single-track).  Track indexing is handled
        # automatically — no need to probe existing subtitle count.
        args: List[str] = ["mkvmerge", "--quiet", "-o", str(output_path), str(video_path)]
        for item in srt_items:
            args.extend([
                "--language", f"0:{item['language']}",
                "--default-track-flag", "0:no",
            ])
            if item.get("kind") == "forced":
                args.extend(["--forced-display-flag", "0:yes"])
            elif item.get("kind") == "hearing_impaired":
                args.extend(["--hearing-impaired-flag", "0:yes"])
            args.append(str(item["srt"]))

        # Scale timeout by file size.  mkvmerge is I/O bound — it copies
        # tracks raw without decoding — but still rewrites the whole MKV.
        file_size = video_path.stat().st_size if video_path.exists() else 0
        timeout_s = max(1800, int(file_size / (50 * 1024 * 1024)))

        try:
            result = subprocess.run(
                args, capture_output=True, text=True, timeout=timeout_s,
            )
            # mkvmerge: 0=success, 1=success-with-warnings, 2=hard error.
            # Negative or other non-zero values indicate signal-killed (SIGKILL,
            # SIGSEGV) — the partial output is unreliable and must not replace
            # the original.
            if result.returncode not in (0, 1):
                err = (result.stderr.strip() or result.stdout.strip())[-200:]
                print(f"[sweep/srt] FAILED to embed into {video_path.name} (rc={result.returncode}): {err}")
                self._cleanup(output_path)
                return 0

            if not output_path.exists() or output_path.stat().st_size < 1024:
                print(f"[sweep/srt] FAILED: output too small for {video_path.name}")
                self._cleanup(output_path)
                return 0

            _safe_replace(output_path, video_path)
            print(f"[sweep/srt] embedded {len(srt_items)} SRT(s) into {video_path.name}")

            for item in srt_items:
                try:
                    item["srt"].unlink()
                except OSError:
                    pass
            return len(srt_items)

        except subprocess.TimeoutExpired:
            print(f"[sweep/srt] TIMEOUT embedding into {video_path.name}")
            self._cleanup(output_path)
            return 0
        except OSError as exc:
            print(f"[sweep/srt] ERROR embedding {video_path.name}: {exc}")
            self._cleanup(output_path)
            return 0

    def embed_orphaned_srt(self, dry_run: bool = False, max_workers: int = 1) -> int:
        """Scan and embed all orphaned .srt files into their parent MKVs.

        For each video with external .srt files:
        1. Validates the SRT timing against the video
        2. Remuxes the SRT into the MKV via mkvmerge (bitstream-safe)
        3. Replaces the original MKV atomically
        4. Deletes the source .srt file

        Uses a thread pool to process multiple videos in parallel since the
        bottleneck is I/O wait per mkvmerge process.

        Args:
            dry_run: If True, scan only — don't modify any files.
            max_workers: Number of parallel mkvmerge processes (default 4).

        Returns:
            Number of SRT files embedded.
        """
        srt_list = self.scan_orphaned_srt()
        if not srt_list:
            return 0

        if dry_run:
            for item in srt_list:
                print(
                    f"[sweep/srt] would embed {item['srt'].name} "
                    f"({item['language']}, {item.get('kind', 'normal')}, "
                    f"{item['entries']} cues) into {item['video'].name}"
                )
            return 0

        scratch = self._resolve_scratch()

        # Group SRTs by video file (embed all at once)
        from collections import defaultdict
        by_video: Dict[Path, List[dict]] = defaultdict(list)
        for item in srt_list:
            by_video[item["video"]].append(item)

        print(f"[sweep/srt] embedding {len(srt_list)} SRT(s) across {len(by_video)} videos ({max_workers} workers)")

        # Process videos in parallel
        from concurrent.futures import ThreadPoolExecutor, as_completed
        embedded_count = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(self._embed_srt_for_video, video_path, srt_items, scratch): video_path
                for video_path, srt_items in by_video.items()
            }
            for future in as_completed(futures):
                try:
                    embedded_count += future.result()
                except Exception as exc:
                    video_path = futures[future]
                    print(f"[sweep/srt] error processing {video_path.name}: {exc}")

        if embedded_count > 0:
            print(f"[sweep/srt] embedded {embedded_count} subtitle file(s) total")
            self._trigger_arr_rescans()

        return embedded_count

    def _detect_srt_lang(self, srt_path: Path, video_stem: str) -> str:
        """Detect subtitle language from SRT filename suffix."""
        return subtitle_identity_from_srt_filename(srt_path, video_stem).lang

    # ------------------------------------------------------------------
    # Embedded subtitle quality scan — detect and strip bad subtitle tracks
    # ------------------------------------------------------------------

    _SIGNS_PATTERNS: Set[str] = {"signs", "songs", "signs/songs", "signs & songs", "s&s", "sign/song"}
    _MIN_CUES_PER_MINUTE = 3  # Same threshold as SRT embed guard

    def scan_bad_embedded_subs(self) -> List[dict]:
        """Scan library for MKV files with sparse/signs-only embedded subtitles.

        For each text-based subtitle track (SRT, ASS), extracts and checks
        cue density.  Tracks below 3 cues/min are flagged.

        Returns a list of dicts:
            [{
                "video": Path,
                "bad_tracks": [{
                    "index": str,
                    "lang": str,
                    "codec": str,
                    "title": str,
                    "reason": str,
                    "cues_per_min": float | None,
                }],
            }, ...]
        """
        results: List[dict] = []
        media_root = self.pool_root / "media"
        if not media_root.exists():
            media_root = self.pool_root

        for video_path in self._walk_video_files(media_root):
            bad_tracks = self._check_embedded_subs(video_path)
            if bad_tracks:
                results.append({"video": video_path, "bad_tracks": bad_tracks})

        print(f"[sweep/sub-quality] found {sum(len(r['bad_tracks']) for r in results)} bad subtitle tracks across {len(results)} files")
        return results

    def _check_embedded_subs(self, video_path: Path) -> List[dict]:
        """Check all embedded subtitle tracks in a video for quality issues."""
        from .subtitle_align import probe_video_duration

        raw_streams = probe_raw_streams(video_path)
        if not raw_streams:
            return []

        duration = probe_video_duration(video_path)
        if not duration or duration < 60:
            return []

        duration_min = duration / 60

        bad: List[dict] = []
        for stream in raw_streams:
            if stream.get("codec_type") != "subtitle":
                continue

            idx = str(stream.get("index", ""))
            lang = stream.get("tags", {}).get("language", "und")
            codec = stream.get("codec_name", "").lower()
            title = stream.get("tags", {}).get("title", "")

            # Quick reject: signs/songs by title
            title_lower = title.lower()
            if any(p in title_lower for p in self._SIGNS_PATTERNS):
                # Signs tracks are intentional — skip, don't flag
                continue

            # Only check text-based subs (can extract and count cues)
            if codec not in ("subrip", "srt", "ass", "ssa"):
                continue

            # Extract and count cues
            cue_count = self._count_embedded_cues(video_path, idx)
            if cue_count is None:
                continue

            cues_per_min = cue_count / max(duration_min, 1)
            if cues_per_min < self._MIN_CUES_PER_MINUTE:
                bad.append({
                    "index": idx,
                    "lang": lang,
                    "codec": codec,
                    "title": title,
                    "reason": f"sparse ({cue_count} cues in {duration_min:.0f}min = {cues_per_min:.1f}/min)",
                    "cues_per_min": cues_per_min,
                })

        return bad

    def _count_embedded_cues(self, video_path: Path, stream_index: str) -> Optional[int]:
        """Extract a subtitle track and count its cues."""
        import tempfile

        # Extract to SRT format (ffmpeg converts ASS→SRT automatically)
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as tmp:
            tmp_path = Path(tmp.name)

        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                    "-i", str(video_path),
                    "-map", f"0:{stream_index}",
                    "-c:s", "srt",
                    str(tmp_path),
                ],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                return None

            if not tmp_path.exists():
                return None

            from .subtitle_align import parse_srt
            entries = parse_srt(tmp_path)
            return len(entries)
        except (subprocess.TimeoutExpired, OSError):
            return None
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def strip_bad_embedded_subs(self, max_workers: int = 4) -> int:
        """Scan and strip bad embedded subtitle tracks from library MKVs.

        Returns the number of files fixed.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        scan_results = self.scan_bad_embedded_subs()
        if not scan_results:
            return 0

        scratch = self._resolve_scratch()
        fixed = 0

        def _strip_one(item: dict) -> bool:
            video_path: Path = item["video"]
            bad_indices = {t["index"] for t in item["bad_tracks"]}
            return self._strip_subtitle_tracks(video_path, bad_indices, scratch)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_strip_one, item): item for item in scan_results}
            for future in as_completed(futures):
                item = futures[future]
                try:
                    if future.result():
                        fixed += 1
                        reasons = ", ".join(t["reason"] for t in item["bad_tracks"])
                        print(f"[sweep/sub-quality] stripped {len(item['bad_tracks'])} bad track(s) from {item['video'].name} ({reasons})")
                except Exception as exc:
                    print(f"[sweep/sub-quality] error on {item['video'].name}: {exc}")

        if fixed > 0:
            print(f"[sweep/sub-quality] fixed {fixed} file(s)")
            self._trigger_arr_rescans()
        return fixed

    def _strip_subtitle_tracks(self, video_path: Path, bad_indices: Set[str], scratch: Optional[Path]) -> bool:
        """Remove specific subtitle tracks from a video via copy-mode remux."""
        if scratch:
            staging = scratch / f"{video_path.stem}.subfix.tmp.mkv"
        else:
            # Dot-prefix hides from media scanners (Radarr/Sonarr)
            staging = video_path.parent / f".{video_path.stem}.subfix.tmp.mkv"

        args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-i", str(video_path)]

        # Map everything
        args.extend(["-map", "0"])

        # Negative-map the bad subtitle tracks
        for idx in bad_indices:
            args.extend(["-map", f"-0:{idx}"])

        args.extend(["-c", "copy", str(staging)])

        try:
            file_size = video_path.stat().st_size if video_path.exists() else 0
            timeout_s = max(3600, int(file_size / (25 * 1024 * 1024)))
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
            if result.returncode != 0:
                self._cleanup(staging)
                return False
            if not staging.exists() or staging.stat().st_size < 1024:
                self._cleanup(staging)
                return False
            _safe_replace(staging, video_path)
            return True
        except (subprocess.TimeoutExpired, OSError):
            self._cleanup(staging)
            return False
