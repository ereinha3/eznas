"""Unmatched specials/extras in a TV pack go to Specials/ or extras/, not the series root.

Regression: Black Clover SP01-SP14, NCOP/NCED and a "Jump Special" landed in
/data/tv/Black Clover/ under their release names.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.models import StackConfig
from orchestrator.pipeline.worker import PipelineWorker, TorrentInfo


def _final_path(config: StackConfig, temp_dir: Path, filename: str, torrent_name: str) -> Path:
    worker = PipelineWorker(config)
    categories = config.download_policy.categories
    library = temp_dir / "pool" / "media" / "tv" / "Black Clover"
    source = temp_dir / "pack" / filename
    torrent = TorrentInfo(
        hash="h", name=torrent_name, category=categories.sonarr,
        download_path=source.parent, files=[source],
    )
    return worker._compute_final_path(
        source, torrent, categories.sonarr, categories, library.parent,
        library_path=library,
    )


@pytest.mark.parametrize(
    ("filename", "folder"),
    [
        ("[Judas] Black Clover - SP08 - Clover Clips, Supersized! Langsis.mkv", "Specials"),
        ("[Group] Show - OVA 2 [1080p].mkv", "Specials"),
        ("[Judas] Black Clover - NCED 10a - New Page.mkv", "extras"),
        ("[Judas] Black Clover - NCOP 02b - PAiNT it BLACK.mkv", "extras"),
        ("[Judas] Black Clover - Jump Special Anime Festa 2018.mkv", "extras"),
    ],
)
@pytest.mark.parametrize("torrent_name", ["[Judas] Black Clover (Seasons 1-4)", "Black Clover S02 1080p"])
def test_unmatched_extras_are_routed(
    stack_config_with_temp_paths: StackConfig, temp_dir: Path,
    filename: str, folder: str, torrent_name: str,
) -> None:
    out = _final_path(stack_config_with_temp_paths, temp_dir, filename, torrent_name)
    assert out.parent.name == folder
    assert out.parent.parent.name == "Black Clover"
    assert out.name == Path(filename).with_suffix(".mkv").name


def test_regular_episode_unaffected(stack_config_with_temp_paths: StackConfig, temp_dir: Path) -> None:
    out = _final_path(stack_config_with_temp_paths, temp_dir,
                      "[Judas] Black Clover - S02E05.mkv", "[Judas] Black Clover (Seasons 1-4)")
    assert out.parent.name == "Season 2"


def test_unparsed_non_extra_keeps_old_fallback(stack_config_with_temp_paths: StackConfig, temp_dir: Path) -> None:
    out = _final_path(stack_config_with_temp_paths, temp_dir, "random name.mkv", "[Judas] Black Clover (Seasons 1-4)")
    assert out.parent.name == "Black Clover"
