"""Regression: a TV pack with a combined episode file plus the same episode
as a single file used to crash build_plans (AttributeError on a None plan)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from orchestrator.models import StackConfig
from orchestrator.pipeline.remux import StreamInfo
from orchestrator.pipeline.worker import PipelineWorker, TorrentInfo


def test_build_plans_prefers_single_over_combined_without_crashing(
    stack_config_with_temp_paths: StackConfig, temp_dir: Path
) -> None:
    pack = temp_dir / "Phineas And Ferb [Complete]"
    pack.mkdir()
    names = [
        "Phineas and Ferb - S01E01-E02.mkv",  # combined first
        "Phineas and Ferb - S01E01.mkv",      # single replaces the combined plan
        "Phineas and Ferb - S01E03.mkv",      # next file used to crash on the None plan
    ]
    files = []
    for name in names:
        f = pack / name
        f.write_bytes(b"\0" * 1024 * 1024)
        files.append(f)

    worker = PipelineWorker(stack_config_with_temp_paths)
    torrent = TorrentInfo(
        hash="poison", name=pack.name, category="tv", download_path=pack, files=files
    )

    with patch("orchestrator.pipeline.remux.probe_streams") as mock_probe:
        mock_probe.return_value = StreamInfo(
            audio_languages={"eng"}, subtitle_languages={"eng"},
            has_video=True, audio_count=1, subtitle_count=1,
        )
        plans = worker.build_plans(torrent)

    sources = {p.source.name for p in plans}
    assert all(p is not None for p in plans)
    assert "Phineas and Ferb - S01E01-E02.mkv" not in sources
    assert {"Phineas and Ferb - S01E01.mkv", "Phineas and Ferb - S01E03.mkv"} <= sources
