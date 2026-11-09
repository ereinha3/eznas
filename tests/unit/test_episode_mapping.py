"""Absolute->season remapping must only apply to absolute-numbered names.

Regression: a Phineas and Ferb pack named "S01e15 I Scream, You Scream" was
filed as S01E16 because every season-1 file was pushed through Sonarr's
absolute map, whose order differs from the aired S01 order for that show.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.models import StackConfig
from orchestrator.pipeline.worker import PipelineWorker, TorrentInfo

# Sonarr numbering for a show whose absolute order differs from aired order
# (taken from Sonarr series 44): S01E15 is absolute 22, S01E16 is absolute 15.
ABSOLUTE_MAP = {
    15: {"season": 1, "episode": 16},
    16: {"season": 1, "episode": 17},
    22: {"season": 1, "episode": 15},
    50: {"season": 2, "episode": 10},
    51: {"season": 2, "episode": 11},
}


def _final_path(config: StackConfig, temp_dir: Path, filename: str) -> Path:
    worker = PipelineWorker(config)
    categories = config.download_policy.categories
    library = temp_dir / "pool" / "media" / "tv" / "Show"
    source = temp_dir / "pack" / filename
    torrent = TorrentInfo(
        hash="h", name="Show [Complete]", category=categories.sonarr,
        download_path=source.parent, files=[source],
    )
    return worker._compute_final_path(
        source, torrent, categories.sonarr, categories, library.parent,
        library_path=library, absolute_episode_map=ABSOLUTE_MAP,
    )


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        # Explicit SxxEyy that exists in Sonarr: keep it as-is.
        ("Phineas And Ferb S01e15 I Scream, You Scream (720p x265 BMG).mkv", "Show - S01E15.mkv"),
        ("Show.S01E16.1080p.WEB.mkv", "Show - S01E16.mkv"),
        ("Show 1x15 Title.mkv", "Show - S01E15.mkv"),
        # Bare absolute numbering: map through Sonarr's absolute order.
        ("[Eclipse] Show - 15 (720p).mkv", "Show - S01E16.mkv"),
        ("[Group] Show - 50 [1080p].mkv", "Show - S02E10.mkv"),
        # Explicit numbering that does not exist in Sonarr: still remapped.
        ("Show S01E50 1080p.mkv", "Show - S02E10.mkv"),
        ("Show S03E51 1080p.mkv", "Show - S02E11.mkv"),
    ],
)
def test_absolute_remap_only_for_absolute_names(
    stack_config_with_temp_paths: StackConfig, temp_dir: Path,
    filename: str, expected: str,
) -> None:
    assert _final_path(stack_config_with_temp_paths, temp_dir, filename).name == expected
