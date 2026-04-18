"""Library sweep engine — in-place remux for existing library files.

Walks the media library directories and strips unwanted audio/subtitle
tracks from files that don't match the user's media policy.  Reuses the
same ffprobe/ffmpeg pipeline used for new downloads.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

import httpx

from ..models import StackConfig
from ..storage import ConfigRepository
from .languages import arr_language_to_iso
from .remux import TrackSelection, StreamInfo, build_ffmpeg_command, probe_streams
from .worker import VIDEO_EXTENSIONS, PipelineWorker


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


# ---------------------------------------------------------------------------
# LibrarySweeper
# ---------------------------------------------------------------------------

class LibrarySweeper:
    """Scans and remuxes existing library files to match the media policy."""

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

                # Skip files already swept at current mtime
                if self._is_already_swept(file_path, swept_state):
                    plan.files_already_clean += 1
                    continue

                # Probe the file
                stream_info = probe_streams(file_path)
                if stream_info is None or not stream_info.has_video:
                    plan.files_already_clean += 1
                    continue

                # Get the policy for this category
                selection = self._worker._policy_for_category(category)

                # Look up the original language from API data
                original_language = self._get_original_language_for_file(
                    file_path, category
                )

                # Check if stripping is needed
                unwanted_audio, unwanted_subs = self._detect_unwanted(
                    stream_info, selection, original_language=original_language,
                )

                if not unwanted_audio and not unwanted_subs:
                    plan.files_already_clean += 1
                    continue

                file_size = file_path.stat().st_size
                plan.files_to_process += 1
                plan.total_bytes_to_process += file_size
                plan.actions.append(SweepAction(
                    path=file_path,
                    size=file_size,
                    category=category,
                    unwanted_audio=unwanted_audio,
                    unwanted_subtitles=unwanted_subs,
                    selection=selection,
                ))

        # Estimate time based on throughput
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

                # Build and run ffmpeg
                cmd = build_ffmpeg_command(
                    action.path, staging_path, action.selection,
                    original_language=original_language,
                )
                success = self._run_ffmpeg(cmd)

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
                os.replace(str(staging_path), str(action.path))
                result.succeeded += 1

                # Mark as swept
                self._mark_swept(action.path, "ok")

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

    # ------------------------------------------------------------------
    # Swept state tracking
    # ------------------------------------------------------------------

    def _load_swept_state(self) -> dict:
        """Load the 'swept' key from pipeline state."""
        pipeline = self.repo.load_pipeline_state()
        return pipeline.get("swept", {})

    def _is_already_swept(self, path: Path, swept_state: dict) -> bool:
        """Check if a file was already swept at its current mtime."""
        key = str(path)
        record = swept_state.get(key)
        if record is None:
            return False
        try:
            current_mtime = path.stat().st_mtime
            return record.get("mtime") == current_mtime and record.get("status") == "ok"
        except OSError:
            return False

    def _mark_swept(self, path: Path, status: str) -> None:
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

        With scratch disk: /scratch/postproc/sweep-{hash12}/filename.mkv
        Without scratch:   source.parent/.filename.tmp.mkv
        """
        if use_scratch and scratch_root is not None:
            hash_input = str(source).encode()
            hash12 = hashlib.sha256(hash_input).hexdigest()[:12]
            return scratch_root / "postproc" / f"sweep-{hash12}" / source.name
        else:
            # Dot-prefix hides from media scanners
            return source.parent / f".{source.stem}.tmp.mkv"

    def _resolve_scratch(self) -> Optional[Path]:
        """Resolve the scratch root, preferring container mounts."""
        container_candidates = [Path("/scratch")]
        for candidate in container_candidates:
            if candidate.exists():
                return candidate
        scratch = self.config.paths.scratch
        if scratch is not None:
            return Path(scratch)
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

    # Known subtitle language codes for filename detection
    _KNOWN_LANG_CODES: Set[str] = {
        "en", "eng", "ja", "jpn", "es", "spa", "fr", "fra", "de", "deu",
        "it", "ita", "pt", "por", "ru", "rus", "ko", "kor", "zh", "zho",
        "ar", "ara", "hi", "hin", "pl", "pol", "nl", "nld", "sv", "swe",
        "da", "dan", "no", "nor", "fi", "fin", "cs", "ces", "hu", "hun",
        "ro", "ron", "tr", "tur", "he", "heb", "el", "ell", "th", "tha",
        "vi", "vie", "id", "ind", "uk", "ukr", "hr", "hrv", "bg", "bul",
        "chi", "ger", "fre", "dut", "rum",
    }
    _SUBTITLE_HINTS: Set[str] = {"hi", "cc", "sdh", "forced"}

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

            for srt_path in srt_files:
                if srt_path.stat().st_size == 0:
                    continue

                entries = parse_srt(srt_path)
                if not entries:
                    continue

                # Detect language from filename
                lang = self._detect_srt_lang(srt_path, video_base)

                # Quick validation (probe video only if needed)
                duration = probe_video_duration(video_path)
                if duration is None:
                    continue

                validation = validate_timing(entries, duration)
                if not validation.is_valid:
                    continue

                results.append({
                    "video": video_path,
                    "srt": srt_path,
                    "language": lang,
                    "entries": len(entries),
                })

        print(f"[sweep] found {len(results)} orphaned SRT files to embed")
        return results

    def embed_orphaned_srt(self, dry_run: bool = False) -> int:
        """Scan and embed all orphaned .srt files into their parent MKVs.

        For each video with external .srt files:
        1. Validates the SRT timing against the video
        2. Remuxes the SRT into the MKV (ffmpeg copy mode)
        3. Replaces the original MKV atomically
        4. Deletes the source .srt file

        Args:
            dry_run: If True, scan only — don't modify any files.

        Returns:
            Number of SRT files embedded.
        """
        from .subtitle_align import remux_subtitle_into_mkv

        srt_list = self.scan_orphaned_srt()
        if not srt_list:
            return 0

        if dry_run:
            for item in srt_list:
                print(
                    f"[sweep/srt] would embed {item['srt'].name} ({item['language']}, "
                    f"{item['entries']} cues) into {item['video'].name}"
                )
            return 0

        embedded_count = 0
        scratch = self._resolve_scratch()

        # Group SRTs by video file (embed all at once)
        from collections import defaultdict
        by_video: Dict[Path, List[dict]] = defaultdict(list)
        for item in srt_list:
            by_video[item["video"]].append(item)

        for video_path, srt_items in by_video.items():
            current_input = video_path

            for i, item in enumerate(srt_items):
                srt_path = item["srt"]
                lang = item["language"]

                # Stage in scratch or beside the video
                if scratch:
                    output_path = scratch / f"{video_path.stem}.srtremux{i}.tmp.mkv"
                else:
                    output_path = video_path.with_suffix(f".srtremux{i}.tmp.mkv")

                ok = remux_subtitle_into_mkv(
                    video_path=current_input,
                    srt_path=srt_path,
                    output_path=output_path,
                    language=lang,
                )

                if not ok or not output_path.exists():
                    print(f"[sweep/srt] FAILED to embed {srt_path.name}")
                    self._cleanup(output_path)
                    break

                # If we had an intermediate staging file, clean it up
                if current_input != video_path:
                    self._cleanup(current_input)

                current_input = output_path

            # Atomic replacement: final staging → original video
            if current_input != video_path and current_input.exists():
                try:
                    os.replace(current_input, video_path)
                    print(f"[sweep/srt] embedded {len(srt_items)} SRT(s) into {video_path.name}")

                    # Delete source SRT files
                    for item in srt_items:
                        try:
                            item["srt"].unlink()
                        except OSError:
                            pass
                    embedded_count += len(srt_items)

                except OSError as exc:
                    print(f"[sweep/srt] atomic replace failed: {exc}")
                    self._cleanup(current_input)

        if embedded_count > 0:
            print(f"[sweep/srt] embedded {embedded_count} subtitle file(s) total")
            self._trigger_arr_rescans()

        return embedded_count

    def _detect_srt_lang(self, srt_path: Path, video_stem: str) -> str:
        """Detect subtitle language from SRT filename suffix."""
        full_stem = srt_path.stem
        suffix = full_stem[len(video_stem):]
        parts = [p.lower() for p in suffix.split(".") if p]

        for part in reversed(parts):
            if part in self._SUBTITLE_HINTS:
                continue
            if part in self._KNOWN_LANG_CODES:
                return part

        return "und"
