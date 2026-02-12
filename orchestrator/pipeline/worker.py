"""Media pipeline worker skeleton for post-processing downloads."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .remux import TrackSelection, build_ffmpeg_command
from ..models import StackConfig


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts"}


def parse_movie_name(torrent_name: str) -> Tuple[str, Optional[str]]:
    """Extract movie title and year from torrent name.

    Returns: (title, year)
    Examples:
        "Good.Will.Hunting.1997.1080p.BluRay" -> ("Good Will Hunting", "1997")
        "Kung Fu Panda 2008 UHD" -> ("Kung Fu Panda", "2008")
    """
    # Clean up common patterns
    name = torrent_name

    # Remove quality/source info (everything after resolution or common keywords)
    patterns_to_remove = [
        r'\b(1080p|720p|2160p|4K|UHD|BluRay|WEBRip|WEB-DL|REMUX|HDTV).*$',
        r'\b(x264|x265|HEVC|H\.264|H\.265).*$',
        r'\[.*?\]',  # Remove brackets
    ]
    for pattern in patterns_to_remove:
        name = re.sub(pattern, '', name, flags=re.IGNORECASE)

    # Extract year (4 digits between 1900-2099)
    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', name)
    year = year_match.group(1) if year_match else None

    # Remove year from title
    if year:
        name = name.replace(year, '')

    # Clean up: replace dots/underscores with spaces, strip, collapse multiple spaces
    title = re.sub(r'[._]', ' ', name)
    title = re.sub(r'\s+', ' ', title).strip()

    # Remove trailing dashes/parentheses
    title = re.sub(r'[\-\(\)]+$', '', title).strip()

    return (title, year)


def parse_tv_episode(torrent_name: str) -> Optional[Tuple[str, int, int]]:
    """Extract show name, season, and episode from torrent name.

    Returns: (show_name, season, episode) or None if not a TV episode
    Examples:
        "The Office US S09E22" -> ("The Office US", 9, 22)
        "Jujutsu Kaisen S03E04" -> ("Jujutsu Kaisen", 3, 4)
    """
    # Common TV episode patterns
    patterns = [
        r'^(.+?)\s+S(\d{1,2})E(\d{1,2})',  # S01E01
        r'^(.+?)\s+(\d{1,2})x(\d{1,2})',   # 1x01
        r'^(.+?)\s+Season\s*(\d+).*?Episode\s*(\d+)',  # Season 1 Episode 1
    ]

    for pattern in patterns:
        match = re.search(pattern, torrent_name, re.IGNORECASE)
        if match:
            show_name = match.group(1).strip()
            season = int(match.group(2))
            episode = int(match.group(3))

            # Clean show name: replace dots/underscores with spaces
            show_name = re.sub(r'[._]', ' ', show_name)
            show_name = re.sub(r'\s+', ' ', show_name).strip()

            return (show_name, season, episode)

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
    """Computed plan for processing a torrent."""

    torrent: TorrentInfo
    source: Path
    staging_output: Path
    final_output: Path
    ffmpeg_command: List[str]
    selection: TrackSelection


class PipelineWorker:
    """Derives remux/move plans for completed torrents."""

    def __init__(self, config: StackConfig) -> None:
        self.config = config
        # The stack config generally stores *host* paths (e.g. /mnt/pool/data),
        # but this worker typically runs inside a container where those paths
        # are mounted at conventional locations. Prefer the container mounts
        # if present; fall back to config values for non-container execution.
        self.pool_root = self._resolve_pool_root(config)
        self.scratch_root = self._resolve_scratch_root(config)
        categories = config.download_policy.categories
        self.destinations = {
            categories.radarr: self.pool_root / "media" / "movies",
            categories.sonarr: self.pool_root / "media" / "tv",
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

    def build_plan(self, torrent: TorrentInfo) -> PipelinePlan:
        """Produce a remux + move plan for a completed torrent."""
        source = self._select_primary_file(torrent.files)
        selection = self._policy_for_category(torrent.category)
        staging_dir = self.scratch_root / "postproc" / torrent.hash
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging_output = staging_dir / f"{source.stem}.mkv"

        # Normalize category to handle *arr service suffixes
        normalized_category = self._normalize_category(torrent.category)

        # Determine final output path with proper directory structure
        base_dir = self.destinations.get(
            normalized_category, self.pool_root / "media" / normalized_category
        )

        categories = self.config.download_policy.categories
        if normalized_category == categories.radarr:
            # Movies: /data/media/movies/Movie Name (Year)/Movie Name (Year).mkv
            title, year = parse_movie_name(torrent.name)
            if year:
                folder_name = f"{title} ({year})"
                file_name = f"{title} ({year}).mkv"
            else:
                folder_name = title
                file_name = f"{title}.mkv"
            final_dir = base_dir / folder_name
            final_output = final_dir / file_name

        elif normalized_category == categories.sonarr:
            # TV Shows: /data/media/tv/Show Name/Season 1/Show Name - S01E01.mkv
            episode_info = parse_tv_episode(torrent.name)
            if episode_info:
                show_name, season, episode = episode_info
                show_dir = base_dir / show_name
                # Use non-padded season directory to match Sonarr/Radarr standard
                season_dir = show_dir / f"Season {season}"
                # But keep zero-padded format in filename (S01E01 is standard)
                file_name = f"{show_name} - S{season:02d}E{episode:02d}.mkv"
                final_dir = season_dir
                final_output = final_dir / file_name
            else:
                # Fallback: couldn't parse, use flat structure
                final_dir = base_dir
                final_output = final_dir / staging_output.name
        else:
            # Unknown category: use flat structure
            final_dir = base_dir
            final_output = final_dir / staging_output.name

        final_dir.mkdir(parents=True, exist_ok=True)

        command = build_ffmpeg_command(source, staging_output, selection)
        return PipelinePlan(
            torrent=torrent,
            source=source,
            staging_output=staging_output,
            final_output=final_output,
            ffmpeg_command=command,
            selection=selection,
        )

    def _policy_for_category(self, category: str) -> TrackSelection:
        # Use the same media policy for all categories
        # Original language detection ensures foreign content keeps native audio
        policy = self.config.media_policy.movies
        return TrackSelection(
            audio=list(policy.keep_audio),
            subtitles=list(policy.keep_subs),
        )

    def _select_primary_file(self, files: Iterable[Path]) -> Path:
        candidates: List[Path] = [
            Path(file) for file in files if Path(file).suffix.lower() in VIDEO_EXTENSIONS
        ]
        if not candidates:
            raise ValueError("No video files found in torrent payload.")
        return max(candidates, key=lambda path: path.stat().st_size if path.exists() else 0)

    def _resolve_pool_root(self, config: StackConfig) -> Path:
        for candidate in (Path("/data"), Path(config.paths.pool)):
            if candidate.exists():
                return candidate
        return Path(config.paths.pool)

    def _resolve_scratch_root(self, config: StackConfig) -> Path:
        # Prefer qBittorrent's save path mount (/downloads) so paths reported by
        # the qBittorrent API (e.g. /downloads/complete/...) are accessible.
        container_candidates = [Path("/downloads"), Path("/scratch")]
        for candidate in container_candidates:
            if candidate.exists():
                return candidate
        scratch = config.paths.scratch
        if scratch is not None:
            return Path(scratch)
        return self.pool_root / "downloads"











