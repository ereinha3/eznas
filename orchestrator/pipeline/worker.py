"""Media pipeline worker skeleton for post-processing downloads."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from .remux import TrackSelection, build_ffmpeg_command
from ..models import StackConfig


VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".ts"}


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

    def build_plan(self, torrent: TorrentInfo) -> PipelinePlan:
        """Produce a remux + move plan for a completed torrent."""
        source = self._select_primary_file(torrent.files)
        selection = self._policy_for_category(torrent.category)
        staging_dir = self.scratch_root / "postproc" / torrent.hash
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging_output = staging_dir / f"{source.stem}.mkv"

        final_dir = self.destinations.get(
            torrent.category, self.pool_root / "media" / torrent.category
        )
        final_dir.mkdir(parents=True, exist_ok=True)
        final_output = final_dir / staging_output.name

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











