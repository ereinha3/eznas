"""Media pipeline worker skeleton for post-processing downloads."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .bdmv import (
    detect_bdmv,
    find_main_feature,
    find_main_feature_extended,
    get_bdmv_stream_languages,
    map_clpi_to_ffprobe_indices,
)
from .remux import TrackSelection, build_ffmpeg_command
from ..models import StackConfig


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts"}


def parse_movie_name(torrent_name: str) -> Tuple[str, Optional[str]]:
    """Extract movie title and year from torrent name.

    Returns: (title, year)
    Examples:
        "Good.Will.Hunting.1997.1080p.BluRay" -> ("Good Will Hunting", "1997")
        "Kung Fu Panda 2008 UHD" -> ("Kung Fu Panda", "2008")
        "ICE_AGE_3_FU" -> ("ICE AGE 3", None)
        "Drunken.Master.1978.REMASTERED.1080p" -> ("Drunken Master", "1978")
    """
    # Clean up common patterns
    name = torrent_name

    # Step 1: Remove bracketed content first (e.g. [JAPANESE] [YTS.MX])
    name = re.sub(r'\[.*?\]', ' ', name)

    # Step 2: Replace dots/underscores with spaces early so patterns work on words
    name = re.sub(r'[._]', ' ', name)

    # Step 3: Remove quality/source info (everything after resolution or keywords).
    # These patterns MUST match as complete words to avoid false positives
    # (e.g. "MA" inside "Master").
    truncate_patterns = [
        r'\b(1080p|720p|2160p|4K|UHD)\b.*$',
        r'\b(BluRay|Blu-ray|WEBRip|WEB-DL|REMUX|HDTV)\b.*$',
        r'\b(BD-?DISK|BD-?REMUX|BDRip|BDRemux|DVDRip)\b.*$',
        r'\b(x264|x265|HEVC|H 264|H 265)\b.*$',
        # Codec patterns: require dash-prefix or standalone to avoid "Master"
        r'\bDTS-HD\b.*$',
        r'\bDTS\b[\s\-].*$',  # DTS followed by space/dash (not "DTSomeword")
        r'\b(AAC|FLAC|TrueHD|Atmos)\b.*$',
    ]
    for pattern in truncate_patterns:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)

    # Step 4: Extract year (4 digits between 1900-2099)
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', name)
    year = year_match.group(1) if year_match else None

    # Remove year from title
    if year:
        name = name.replace(year, '')

    # Step 5: Remove scene/release tags
    scene_tags = re.compile(
        r'\b(REMASTERED|COMPLETE|REPACK|EXTENDED|UNRATED|UNCUT|'
        r'DIRECTORS\s*CUT|JAPANESE|RUSSIAN|MULTi|DUAL|PROPER|INTERNAL|LIMITED|'
        r'CEE|EUR|HDR|BD|CRITERION|SAMPLE|'
        # Common scene group names that appear as suffixes
        r'FU|HDCLUB|NAHOM|GUHZER|FGT|EATDIK|SharpHD|MassModz|'
        r'FraMeSToR|MkvCage|YTS|YIFY|RARBG|SPARKS|AMIABLE|EVO|FLAME)\b',
        re.IGNORECASE,
    )
    name = scene_tags.sub('', name)

    # Step 6: Collapse spaces, strip trailing punctuation
    title = re.sub(r'\s+', ' ', name).strip()
    title = re.sub(r'[\-\(\)\s]+$', '', title).strip()

    # Step 7: Remove trailing scene group names (short ALL-CAPS at end of title)
    # e.g. "Ice Age 3 FU" -> "Ice Age 3", "Leon BD" -> "Leon"
    # But don't strip if the ENTIRE title is all-caps (e.g. "THE ROYAL TENENBAUMS")
    if not title.isupper():
        title = re.sub(r'\s+[A-Z]{1,8}$', '', title).strip()

    # Step 8: Final cleanup
    title = re.sub(r'[\-\(\)\s]+$', '', title).strip()
    title = re.sub(r'\s+', ' ', title).strip()

    return (title, year)


def parse_tv_episode(name: str) -> Optional[Tuple[str, int, int]]:
    """Extract show name, season, and episode from a torrent or file name.

    Returns: (show_name, season, episode) or None if not a TV episode
    Examples:
        "The Office US S09E22" -> ("The Office US", 9, 22)
        "The.Legend.of.Korra.S04E01.After.All.These.Years" -> ("The Legend of Korra", 4, 1)
    """
    # Common TV episode patterns
    patterns = [
        r'^(.+?)\s*[.\-_ ]+S(\d{1,2})E(\d{1,2})',  # S01E01
        r'^(.+?)\s+(\d{1,2})x(\d{1,2})',   # 1x01
        r'^(.+?)\s+Season\s*(\d+).*?Episode\s*(\d+)',  # Season 1 Episode 1
    ]

    for pattern in patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            show_name = match.group(1).strip()
            season = int(match.group(2))
            episode = int(match.group(3))

            # Clean show name: replace dots/underscores with spaces
            show_name = re.sub(r'[._]', ' ', show_name)
            show_name = re.sub(r'\s+', ' ', show_name).strip()
            # Remove trailing dashes
            show_name = re.sub(r'[\-]+$', '', show_name).strip()

            return (show_name, season, episode)

    return None


def parse_tv_season(name: str) -> Optional[Tuple[str, int]]:
    """Extract show name and season number from a season pack name.

    Returns: (show_name, season) or None
    Examples:
        "The.Legend.of.Korra.S04.1080p.BluRay" -> ("The Legend of Korra", 4)
        "Breaking Bad Season 2" -> ("Breaking Bad", 2)
    """
    patterns = [
        r'^(.+?)\s*[.\-_ ]+S(\d{1,2})(?!\d|E)',  # S04 (not followed by E)
        r'^(.+?)\s+Season\s*(\d+)',                 # Season 4
    ]
    for pattern in patterns:
        match = re.search(pattern, name, re.IGNORECASE)
        if match:
            show_name = match.group(1).strip()
            season = int(match.group(2))
            show_name = re.sub(r'[._]', ' ', show_name)
            show_name = re.sub(r'\s+', ' ', show_name).strip()
            show_name = re.sub(r'[\-\(\)]+$', '', show_name).strip()
            return (show_name, season)
    return None


@dataclass
class TorrentInfo:
    """Minimal representation of a completed torrent payload."""

    hash: str
    name: str
    category: str
    download_path: Path
    files: Sequence[Path]


@dataclass
class PipelinePlan:
    """Computed plan for processing a single video file from a torrent."""

    torrent: TorrentInfo
    source: Path
    staging_output: Path
    final_output: Path
    ffmpeg_command: List[str]
    selection: TrackSelection
    original_language: Optional[str] = None
    is_bdmv: bool = False


class PipelineWorker:
    """Derives remux/move plans for completed torrents."""

    def __init__(self, config: StackConfig) -> None:
        self.config = config
        # The stack config generally stores *host* paths (e.g. /mnt/pool/data),
        # but this worker typically runs inside a container where those paths
        # are mounted at conventional locations. Prefer the container mounts
        # if present; fall back to config values for non-container execution.
        self.pool_root = self._resolve_pool_root(config)
        categories = config.download_policy.categories
        # Paths must match Sonarr/Radarr root folders:
        #   Sonarr root = /data/tv,  Radarr root = /data/movies
        self.destinations = {
            categories.radarr: self.pool_root / "movies",
            categories.sonarr: self.pool_root / "tv",
        }

    def _normalize_category(self, category: str) -> str:
        """Normalize category names to handle *arr service suffixes.

        Sonarr/Radarr may append service names to categories (e.g., 'tv-sonarr').
        This strips common suffixes to match against configured categories.
        """
        for suffix in ["-sonarr", "-radarr"]:
            if category.endswith(suffix):
                return category[: -len(suffix)]
        return category

    def build_plans(
        self,
        torrent: TorrentInfo,
        *,
        original_language: Optional[str] = None,
        library_path: Optional[Path] = None,
        iso_mount_dir: Optional[Path] = None,
    ) -> List[PipelinePlan]:
        """Produce remux + move plans for ALL video files in a torrent.

        For a single-file movie torrent this returns one plan.
        For a season pack this returns one plan per episode file.
        For a BDMV torrent this returns one plan for the main feature.
        For an ISO image this returns one plan from the mounted BDMV.

        Args:
            torrent: Torrent metadata and file list.
            original_language: ISO 639 code for the content's original
                language (from Radarr/Sonarr API).  Passed through to
                ``build_ffmpeg_command()`` for keep-audio logic.
            library_path: Canonical library path from Radarr/Sonarr
                (e.g. ``/data/movies/Hereditary (2018)``).  Used for
                clean output naming when available.
            iso_mount_dir: Path to a mounted ISO image.  When provided,
                the mount point is scanned for BDMV structure instead
                of using the torrent's file list.
        """
        selection = self._policy_for_category(torrent.category)
        normalized_category = self._normalize_category(torrent.category)
        base_dir = self.destinations.get(
            normalized_category, self.pool_root / normalized_category
        )
        categories = self.config.download_policy.categories
        # --- ISO: treat mounted ISO as BDMV ---
        if iso_mount_dir:
            bdmv_root = detect_bdmv(iso_mount_dir)
            if bdmv_root is not None:
                return self._build_bdmv_plan(
                    torrent, bdmv_root, selection, normalized_category,
                    categories, base_dir, original_language,
                    library_path=library_path,
                )
            raise ValueError(
                f"ISO mounted at {iso_mount_dir} but no BDMV structure found"
            )

        # --- BDMV detection ---
        bdmv_root = self._detect_bdmv_in_torrent(torrent)
        if bdmv_root is not None:
            return self._build_bdmv_plan(
                torrent, bdmv_root, selection, normalized_category,
                categories, base_dir, original_language,
                library_path=library_path,
            )

        # --- Standard video files ---
        video_files = self._select_video_files(torrent.files)
        if not video_files:
            raise ValueError("No video files found in torrent payload.")

        # SAFETY GUARD: Detect unrecognised BD-DISK structures.
        # If BDMV detection above failed (e.g. Docker path mismatch) but the
        # torrent is actually a Blu-ray disc, we'll see many .m2ts files that
        # all resolve to the same output path.  Processing every small .m2ts
        # clip would overwrite the main feature with a tiny menu fragment.
        #
        # Heuristic: if ≥5 .m2ts files exist and the largest is ≥10× the
        # median, this is almost certainly a BD-DISK.  Only process the
        # largest file (the main feature).
        m2ts_files = [f for f in video_files if f.suffix.lower() == ".m2ts"]
        if len(m2ts_files) >= 5:
            sizes = sorted(
                (f.stat().st_size for f in m2ts_files if f.exists()),
                reverse=True,
            )
            if sizes and sizes[0] > 10 * sizes[len(sizes) // 2]:
                print(
                    f"[pipeline] WARNING: {len(m2ts_files)} .m2ts files "
                    f"detected (likely unrecognised BD-DISK). "
                    f"Processing only the largest file "
                    f"({sizes[0] / (1024**3):.1f} GB)."
                )
                video_files = [m2ts_files[0]]  # already sorted largest-first

        plans: List[PipelinePlan] = []
        for source in video_files:
            final_output = self._compute_final_path(
                source, torrent, normalized_category, categories, base_dir,
                library_path=library_path,
            )

            # SAFETY: Skip if this output path is already claimed by another
            # plan in this batch — prevents the overwrite-chain bug.
            existing_outputs = {p.final_output for p in plans}
            if final_output in existing_outputs:
                print(
                    f"[pipeline] skipping {source.name}: output path "
                    f"{final_output.name} already used by another plan"
                )
                continue

            final_output.parent.mkdir(parents=True, exist_ok=True)
            # Stage as a temp file next to the final output (same filesystem)
            # so the final move is an atomic rename, not a cross-device copy.
            staging_output = final_output.parent / f".tmp_{final_output.name}"

            command = build_ffmpeg_command(
                source, staging_output, selection,
                original_language=original_language,
            )
            plans.append(PipelinePlan(
                torrent=torrent,
                source=source,
                staging_output=staging_output,
                final_output=final_output,
                ffmpeg_command=command,
                selection=selection,
                original_language=original_language,
            ))

        return plans

    def _detect_bdmv_in_torrent(self, torrent: TorrentInfo) -> Optional[Path]:
        """Check if any path in the torrent contains a BDMV structure.

        Tries multiple strategies to handle both host and Docker paths:
        1. Detection on content_path (torrent.download_path / torrent.name)
        2. File-path reconstruction from torrent file list

        IMPORTANT: We intentionally do NOT call detect_bdmv() on the bare
        download_path (e.g. /downloads/complete/).  That directory is shared
        across ALL torrents, so detect_bdmv()'s child-directory search would
        find BDMV structures belonging to OTHER torrents and return them —
        causing every subsequent torrent to be misidentified as a Blu-ray
        and processed from the wrong source file.
        """
        # Strategy 1: Check download_path / torrent_name (the content directory)
        # qBittorrent's save_path is often just /downloads/complete, and the
        # actual torrent content is at save_path/torrent_name/
        content_dir = torrent.download_path / torrent.name
        if content_dir.is_dir():
            bdmv = detect_bdmv(content_dir)
            if bdmv is not None:
                print(
                    f"[pipeline] BDMV found via content dir: "
                    f"{bdmv} (torrent: {torrent.name})"
                )
                return bdmv

        # Strategy 2: Reconstruct BDMV root from file paths in the torrent.
        # File paths from qBittorrent may be relative (TorrentName/BDMV/STREAM/...)
        # or absolute (/downloads/complete/TorrentName/BDMV/STREAM/...).
        for f in torrent.files:
            parts = f.parts
            for i, part in enumerate(parts):
                if part == "BDMV" and i + 1 < len(parts) and parts[i + 1] == "STREAM":
                    bdmv_root = Path(*parts[:i + 1]) if i > 0 else Path(parts[0])
                    # Try absolute path first
                    if bdmv_root.is_absolute() and bdmv_root.is_dir():
                        return bdmv_root
                    # Try relative to download_path (common for qBittorrent)
                    abs_root = torrent.download_path / bdmv_root
                    if abs_root.is_dir():
                        return abs_root

        return None

    def _build_bdmv_plan(
        self,
        torrent: TorrentInfo,
        bdmv_root: Path,
        selection: TrackSelection,
        normalized_category: str,
        categories,
        base_dir: Path,
        original_language: Optional[str],
        *,
        library_path: Optional[Path] = None,
    ) -> List[PipelinePlan]:
        """Build a plan to extract the main feature from a BDMV structure.

        Handles both single-file and seamless branching (multi-clip playlist)
        BDMVs.  For playlists, builds a concat + remux command.
        """
        feature = find_main_feature_extended(bdmv_root)
        if feature is None:
            raise ValueError(
                f"BDMV detected at {bdmv_root} but no .m2ts files found"
            )

        main_m2ts = feature.primary_clip
        clip_id = feature.primary_clip_id

        if feature.is_playlist:
            print(
                f"[pipeline] BDMV detected: seamless branching playlist "
                f"{feature.playlist_name} with {len(feature.clips)} clips "
                f"({feature.total_size / (1024**3):.1f} GB total)"
            )
        else:
            print(
                f"[pipeline] BDMV detected: main feature is {clip_id}.m2ts "
                f"({main_m2ts.stat().st_size / (1024**3):.1f} GB)"
            )

        # Parse CLPI for language metadata (use first clip's CLPI)
        stream_languages: Optional[List[Dict[str, str]]] = None
        clpi_raw = get_bdmv_stream_languages(bdmv_root, clip_id)
        if clpi_raw:
            stream_languages = map_clpi_to_ffprobe_indices(main_m2ts, clpi_raw)
            if stream_languages:
                audio_langs = [
                    s["lang"] for s in stream_languages if s["type"] == "audio"
                ]
                sub_langs = [
                    s["lang"] for s in stream_languages if s["type"] == "subtitle"
                ]
                print(
                    f"[pipeline] CLPI languages — audio: {audio_langs}, "
                    f"subs: {sub_langs}"
                )
            else:
                print("[pipeline] CLPI parsing found entries but ffprobe mapping failed")
        else:
            print("[pipeline] no CLPI language data found, will keep all tracks")

        # Compute final output path — prefer API library path when available
        final_output = self._compute_final_path(
            main_m2ts, torrent, normalized_category, categories, base_dir,
            library_path=library_path,
        )
        final_output.parent.mkdir(parents=True, exist_ok=True)
        # Stage as a temp file next to the final output (same filesystem)
        staging_output = final_output.parent / f".tmp_{final_output.name}"

        if feature.is_playlist and len(feature.clips) > 1:
            # Multi-clip: create a concat file list and use concat demuxer
            concat_list = final_output.parent / f".tmp_{torrent.name}_concat.txt"
            with open(concat_list, "w") as f:
                for clip_path in feature.clips:
                    # Paths need to be escaped for the concat demuxer
                    escaped = str(clip_path).replace("'", "'\\''")
                    f.write(f"file '{escaped}'\n")

            command = self._build_concat_command(
                concat_list, staging_output, selection,
                original_language=original_language,
                stream_languages=stream_languages,
                source_suffix=main_m2ts.suffix,
            )
        else:
            command = build_ffmpeg_command(
                main_m2ts, staging_output, selection,
                original_language=original_language,
                stream_languages=stream_languages,
            )

        return [PipelinePlan(
            torrent=torrent,
            source=main_m2ts,
            staging_output=staging_output,
            final_output=final_output,
            ffmpeg_command=command,
            selection=selection,
            original_language=original_language,
            is_bdmv=True,
        )]

    def _build_concat_command(
        self,
        concat_list: Path,
        destination: Path,
        selection: TrackSelection,
        *,
        original_language: Optional[str] = None,
        stream_languages: Optional[List[Dict[str, str]]] = None,
        source_suffix: str = ".m2ts",
    ) -> List[str]:
        """Build an ffmpeg concat + remux command for multi-clip BDMVs.

        Uses the concat demuxer to join clips, then applies the same
        stream selection logic as build_ffmpeg_command.
        """
        args: List[str] = [
            "ffmpeg",
            "-hide_banner",
            "-y",
        ]

        # Transport streams need extra analysis
        if source_suffix.lower() in (".m2ts", ".ts"):
            args.extend(["-analyzeduration", "10M", "-probesize", "10M"])

        args.extend(["-f", "concat", "-safe", "0", "-i", str(concat_list)])

        # Map video
        args.extend(["-map", "0:v:0?"])

        # Build keep-lists (normalise ISO 639-2 B/T variants)
        from .remux import _normalize_lang
        keep_audio = {_normalize_lang(lang) for lang in selection.audio}
        keep_subs = {_normalize_lang(lang) for lang in selection.subtitles}
        if original_language:
            keep_audio.add(_normalize_lang(original_language))

        # For concat, we can't easily probe individual streams, so use
        # CLPI metadata if available to select tracks by index
        if stream_languages:
            audio_input_idx = 0   # index into input audio streams
            audio_output_idx = 0  # index into output (mapped) audio streams
            for entry in stream_languages:
                if entry.get("type") == "audio":
                    lang = _normalize_lang(entry.get("lang", "und"))
                    if lang in keep_audio or lang == "und":
                        args.extend(["-map", f"0:a:{audio_input_idx}"])
                        if lang != "und":
                            args.extend([
                                f"-metadata:s:a:{audio_output_idx}",
                                f"language={lang}",
                            ])
                        audio_output_idx += 1
                    audio_input_idx += 1

            sub_input_idx = 0
            sub_output_idx = 0
            for entry in stream_languages:
                if entry.get("type") == "subtitle":
                    lang = _normalize_lang(entry.get("lang", "und"))
                    if lang in keep_subs:
                        args.extend(["-map", f"0:s:{sub_input_idx}"])
                        if lang != "und":
                            args.extend([
                                f"-metadata:s:s:{sub_output_idx}",
                                f"language={lang}",
                            ])
                        sub_output_idx += 1
                    sub_input_idx += 1

            # SAFETY: If filtering removed all audio, keep all as fallback
            if audio_output_idx == 0 and audio_input_idx > 0:
                print(
                    "[pipeline] WARNING: concat track selection removed all "
                    "audio — keeping all tracks as fallback"
                )
                args.extend(["-map", "0:a?"])
        else:
            # No CLPI data — keep all audio and subtitle streams as fallback
            args.extend(["-map", "0:a?", "-map", "0:s?"])

        args.extend(["-c", "copy", str(destination)])
        return args

    # Keep backward compat — returns plan for the largest file only
    def build_plan(self, torrent: TorrentInfo) -> PipelinePlan:
        """Produce a remux + move plan for the primary video file."""
        plans = self.build_plans(torrent)
        # Return the plan for the largest source file
        return max(plans, key=lambda p: p.source.stat().st_size if p.source.exists() else 0)

    def _compute_final_path(
        self,
        source: Path,
        torrent: TorrentInfo,
        normalized_category: str,
        categories,
        base_dir: Path,
        *,
        library_path: Optional[Path] = None,
    ) -> Path:
        """Compute the final library path for a single video file.

        When ``library_path`` is provided (from Radarr/Sonarr API), it is
        used for clean, canonical naming.  Falls back to regex parsing of
        the torrent/file name when the API path is unavailable.
        """
        if normalized_category == categories.radarr:
            # Movies: /data/movies/Movie Name (Year)/Movie Name (Year).mkv
            if library_path:
                # Use the canonical folder name from Radarr
                # e.g. library_path = /data/movies/Hereditary (2018)
                folder_name = library_path.name  # "Hereditary (2018)"
                file_name = f"{folder_name}.mkv"
                return base_dir / folder_name / file_name

            # Fallback: regex-parse from file stem, then torrent name
            title, year = parse_movie_name(source.stem)
            if not year:
                title, year = parse_movie_name(torrent.name)
            if year:
                folder_name = f"{title} ({year})"
                file_name = f"{title} ({year}).mkv"
            else:
                folder_name = title
                file_name = f"{title}.mkv"
            return base_dir / folder_name / file_name

        elif normalized_category == categories.sonarr:
            # TV: try parsing episode info from the FILE name first
            # (season packs have episode info in files, not torrent name)
            episode_info = parse_tv_episode(source.stem)
            if not episode_info:
                # Fall back to torrent name (single-episode downloads)
                episode_info = parse_tv_episode(torrent.name)

            if episode_info:
                _, season, episode = episode_info
                # Use the canonical show name from Sonarr when available
                # e.g. library_path = /data/tv/The Legend of Korra
                show_name = library_path.name if library_path else episode_info[0]
                season_dir = base_dir / show_name / f"Season {season}"
                file_name = f"{show_name} - S{season:02d}E{episode:02d}.mkv"
                return season_dir / file_name

            # Couldn't parse episode — try season-level info for directory,
            # keep original filename
            season_info = parse_tv_season(torrent.name)
            if season_info:
                parsed_show_name, season = season_info
                show_name = library_path.name if library_path else parsed_show_name
                season_dir = base_dir / show_name / f"Season {season}"
                return season_dir / f"{source.stem}.mkv"

            # Total fallback — use API show name if available
            if library_path:
                return base_dir / library_path.name / f"{source.stem}.mkv"
            return base_dir / f"{source.stem}.mkv"

        # Unknown category
        return base_dir / f"{source.stem}.mkv"

    def _policy_for_category(self, _category: str) -> TrackSelection:
        # Use the same media policy for all categories
        # Original language detection ensures foreign content keeps native audio
        policy = self.config.media_policy.movies
        return TrackSelection(
            audio=list(policy.keep_audio),
            subtitles=list(policy.keep_subs),
        )

    def _select_video_files(self, files: Iterable[Path]) -> List[Path]:
        """Return all video files from the torrent, sorted largest first."""
        candidates = [
            Path(f) for f in files if Path(f).suffix.lower() in VIDEO_EXTENSIONS
        ]
        # Sort largest first so the caller can prioritize if needed
        candidates.sort(
            key=lambda p: p.stat().st_size if p.exists() else 0,
            reverse=True,
        )
        return candidates

    # Keep backward compat
    def _select_primary_file(self, files: Iterable[Path]) -> Path:
        candidates = self._select_video_files(files)
        if not candidates:
            raise ValueError("No video files found in torrent payload.")
        return candidates[0]

    def _resolve_pool_root(self, config: StackConfig) -> Path:
        for candidate in (Path("/data"), Path(config.paths.pool)):
            if candidate.exists():
                return candidate
        return Path(config.paths.pool)

