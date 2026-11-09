"""Tests for media audit batch planning and targeted remediation scans."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from orchestrator.pipeline.media_audit import (
    Issue,
    FileAudit,
    build_batch_summary,
    classify_batches,
)
from orchestrator.pipeline.remux import StreamInfo
from orchestrator.pipeline.sweep import LibrarySweeper


def test_audit_batches_classify_known_subtitle_and_codec_risks() -> None:
    audit = FileAudit(
        path="/mnt/pool/media/movies/Austin Powers International Man of Mystery/movie.mkv",
        category="movies",
        size_bytes=10_000,
        issues=[
            Issue("critical", "default_subtitle", "image subtitle default"),
            Issue("high", "video_codec_transcode_risk", "vc1"),
            Issue("medium", "audio_codec_transcode_risk", "dts"),
        ],
    )

    assert classify_batches(audit) == [
        "pilot_known",
        "critical_subtitle",
        "track_hygiene",
        "codec_replace",
        "streaming_pressure",
    ]


def test_batch_summary_counts_files_and_bytes() -> None:
    audits = [
        FileAudit(
            path="/media/movies/A.mkv",
            category="movies",
            size_bytes=100,
            issues=[Issue("critical", "default_subtitle", "default")],
        ),
        FileAudit(
            path="/media/movies/B.mkv",
            category="movies",
            size_bytes=200,
            issues=[Issue("medium", "audio_codec_transcode_risk", "truehd")],
        ),
    ]

    summary = build_batch_summary(audits)

    assert summary["critical_subtitle"]["file_count"] == 1
    assert summary["critical_subtitle"]["total_bytes"] == 100
    assert summary["streaming_pressure"]["file_count"] == 1
    assert summary["streaming_pressure"]["total_bytes"] == 200


def test_scan_targets_uses_current_file_policy(
    stack_config_with_temp_paths,
    config_repo,
) -> None:
    movie = Path(stack_config_with_temp_paths.paths.pool) / "movies" / "Movie" / "Movie.mkv"
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b"not real media")

    sweeper = LibrarySweeper(stack_config_with_temp_paths, config_repo)
    sweeper.pool_root = Path(stack_config_with_temp_paths.paths.pool)

    raw_streams = [
        {"index": 0, "codec_type": "video", "codec_name": "h264"},
        {"index": 1, "codec_type": "audio", "codec_name": "ac3", "tags": {"language": "eng"}},
        {"index": 2, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "eng"}},
    ]

    with (
        patch("orchestrator.pipeline.sweep.probe_streams") as probe_streams,
        patch("orchestrator.pipeline.sweep.probe_raw_streams", return_value=raw_streams),
        patch(
            "orchestrator.pipeline.sweep.detect_stream_policy_changes",
            return_value=([], ["stream 2 (eng, subrip, clear default)"]),
        ),
    ):
        probe_streams.return_value = StreamInfo(
            audio_languages={"eng"},
            subtitle_languages={"eng"},
            has_video=True,
            audio_count=1,
            subtitle_count=1,
        )
        plan = sweeper.scan_targets([movie])

    assert plan.total_files_scanned == 1
    assert plan.files_to_process == 1
    assert plan.actions[0].path == movie
    assert plan.actions[0].unwanted_subtitles == [
        "stream 2 (eng, subrip, clear default)",
    ]
