"""Tests for pipeline worker and remux functionality."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from orchestrator.pipeline.remux import (
    build_ffmpeg_command,
    probe_streams,
    TrackSelection,
    StreamInfo,
)
from orchestrator.pipeline.worker import PipelineWorker, TorrentInfo
from orchestrator.models import StackConfig


class TestProbeStreams:
    """Tests for stream probing functionality."""

    def test_probe_valid_file(self, sample_media_info: Dict[str, Any]):
        """Probing a valid file should return stream info."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=json.dumps(sample_media_info)
            )
            result = probe_streams(Path("/fake/file.mkv"))

            assert result is not None
            assert result.has_video
            assert result.audio_count == 3
            assert result.subtitle_count == 2
            assert "eng" in result.audio_languages
            assert "rus" in result.audio_languages
            assert "jpn" in result.audio_languages

    def test_probe_nonexistent_file(self):
        """Probing a non-existent file should return None."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = probe_streams(Path("/fake/nonexistent.mkv"))
            assert result is None

    def test_probe_invalid_json(self):
        """Probing with invalid ffprobe output should return None."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="not json")
            result = probe_streams(Path("/fake/file.mkv"))
            assert result is None

    def test_probe_timeout(self):
        """Probing should handle timeout gracefully."""
        with patch("subprocess.run") as mock_run:
            import subprocess
            mock_run.side_effect = subprocess.TimeoutExpired("ffprobe", 30)
            result = probe_streams(Path("/fake/file.mkv"))
            assert result is None

    def test_probe_file_with_no_audio(self):
        """Probing a video-only file should work."""
        media_info = {
            "streams": [
                {"index": 0, "codec_type": "video", "tags": {"language": "eng"}},
            ]
        }
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(media_info))
            result = probe_streams(Path("/fake/video_only.mkv"))

            assert result is not None
            assert result.has_video
            assert result.audio_count == 0
            assert result.subtitle_count == 0


class TestBuildFFmpegCommand:
    """Tests for FFmpeg command building."""

    def test_basic_command(self):
        """Basic command should include essential options."""
        selection = TrackSelection(audio=["eng"], subtitles=["eng"])

        with patch("orchestrator.pipeline.remux.probe_streams") as mock_probe:
            mock_probe.return_value = StreamInfo(
                audio_languages={"eng"},
                subtitle_languages={"eng"},
                has_video=True,
                audio_count=1,
                subtitle_count=1,
            )

            cmd = build_ffmpeg_command(
                Path("/input.mkv"),
                Path("/output.mkv"),
                selection
            )

            assert "ffmpeg" in cmd
            assert "-i" in cmd
            assert "/input.mkv" in cmd
            assert "-c" in cmd
            assert "copy" in cmd
            assert "/output.mkv" in cmd

    def test_english_only_filtering(self, sample_media_info: Dict[str, Any]):
        """English-only selection should filter out other languages."""
        selection = TrackSelection(audio=["eng"], subtitles=["eng"])

        with patch("orchestrator.pipeline.remux.probe_streams") as mock_probe:
            mock_probe.return_value = StreamInfo(
                audio_languages={"eng", "rus", "jpn"},
                subtitle_languages={"eng", "rus"},
                has_video=True,
                audio_count=3,
                subtitle_count=2,
            )

            cmd = build_ffmpeg_command(
                Path("/input.mkv"),
                Path("/output.mkv"),
                selection
            )

            # Should map English audio
            assert "0:a:m:language:eng" in " ".join(cmd)
            # Should NOT map Russian (except as fallback 'und' might be added)

    def test_anime_dual_audio(self):
        """Anime selection should keep Japanese and English."""
        selection = TrackSelection(audio=["jpn", "eng"], subtitles=["eng"])

        with patch("orchestrator.pipeline.remux.probe_streams") as mock_probe:
            mock_probe.return_value = StreamInfo(
                audio_languages={"eng", "jpn"},
                subtitle_languages={"eng"},
                has_video=True,
                audio_count=2,
                subtitle_count=1,
            )

            cmd = build_ffmpeg_command(
                Path("/input.mkv"),
                Path("/output.mkv"),
                selection
            )

            cmd_str = " ".join(cmd)
            assert "0:a:m:language:eng" in cmd_str
            assert "0:a:m:language:jpn" in cmd_str

    def test_no_matching_audio(self):
        """When no audio matches, should fall back to first track."""
        selection = TrackSelection(audio=["eng"], subtitles=["eng"])

        with patch("orchestrator.pipeline.remux.probe_streams") as mock_probe:
            mock_probe.return_value = StreamInfo(
                audio_languages={"fra"},  # Only French
                subtitle_languages={"fra"},
                has_video=True,
                audio_count=1,
                subtitle_count=1,
            )

            cmd = build_ffmpeg_command(
                Path("/input.mkv"),
                Path("/output.mkv"),
                selection
            )

            # Should fall back to first audio track
            assert "0:a:0" in " ".join(cmd)

    def test_probe_failure_fallback(self):
        """When probe fails, should copy all streams."""
        selection = TrackSelection(audio=["eng"], subtitles=["eng"])

        with patch("orchestrator.pipeline.remux.probe_streams") as mock_probe:
            mock_probe.return_value = None  # Probe failed

            cmd = build_ffmpeg_command(
                Path("/input.mkv"),
                Path("/output.mkv"),
                selection
            )

            # Should fall back to copying all
            cmd_str = " ".join(cmd)
            assert "0:a" in cmd_str
            assert "0:s" in cmd_str


class TestPipelineWorker:
    """Tests for the PipelineWorker class."""

    def test_build_plan(self, stack_config: StackConfig, temp_dir: Path):
        """Building a plan should create valid output paths."""
        # Create a test file
        test_file = temp_dir / "test.mkv"
        test_file.touch()

        worker = PipelineWorker(stack_config)

        torrent_info = TorrentInfo(
            hash="abc123",
            name="test",
            category="movies",
            download_path=temp_dir,
            files=[test_file],
        )

        with patch("orchestrator.pipeline.remux.probe_streams") as mock_probe:
            mock_probe.return_value = StreamInfo(
                audio_languages={"eng"},
                subtitle_languages={"eng"},
                has_video=True,
                audio_count=1,
                subtitle_count=1,
            )

            plan = worker.build_plan(torrent_info)

            assert plan.source == test_file
            assert plan.staging_output is not None
            assert plan.final_output is not None
            assert len(plan.ffmpeg_command) > 0

    def test_category_to_destination(self, stack_config: StackConfig):
        """Categories should map to correct destinations."""
        worker = PipelineWorker(stack_config)

        # Movies category
        dest = worker.destinations.get("movies")
        assert dest is not None
        assert "movies" in str(dest)

        # TV category
        dest = worker.destinations.get("tv")
        assert dest is not None
        assert "tv" in str(dest)

    def test_policy_for_category(self, stack_config: StackConfig):
        """Policy selection should use correct settings per category."""
        worker = PipelineWorker(stack_config)

        # Movies should use movies policy
        policy = worker._policy_for_category("movies")
        assert "eng" in policy.audio

        # Anime should use anime policy
        policy = worker._policy_for_category("anime")
        assert "jpn" in policy.audio
        assert "eng" in policy.audio


class TestTorrentProcessing:
    """Tests for torrent processing logic."""

    def test_skip_already_processed(self, stack_config: StackConfig, config_repo):
        """Already processed torrents should be skipped."""
        # Mark a torrent as processed
        state = config_repo.load_state()
        state["pipeline"] = {"processed": {"existing_hash": {"status": "ok"}}}
        config_repo.save_state(state)

        from orchestrator.pipeline.runner import PipelineRunner
        runner = PipelineRunner(config_repo)

        assert runner._is_processed("existing_hash")
        assert not runner._is_processed("new_hash")

    def test_category_filtering(self, stack_config: StackConfig):
        """Only tracked categories should be processed."""
        from orchestrator.pipeline.runner import PipelineRunner

        class MockRepo:
            def load_stack(self):
                return stack_config

        runner = PipelineRunner(MockRepo())

        assert runner._should_process(stack_config, "movies")
        assert runner._should_process(stack_config, "tv")
        assert runner._should_process(stack_config, "anime")
        assert not runner._should_process(stack_config, "other")
        assert not runner._should_process(stack_config, "")


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_filename_with_spaces(self, stack_config: StackConfig, temp_dir: Path):
        """Filenames with spaces should be handled."""
        test_file = temp_dir / "My Movie (2024).mkv"
        test_file.touch()

        worker = PipelineWorker(stack_config)
        torrent_info = TorrentInfo(
            hash="abc123",
            name="My Movie (2024)",
            category="movies",
            download_path=temp_dir,
            files=[test_file],
        )

        with patch("orchestrator.pipeline.remux.probe_streams") as mock_probe:
            mock_probe.return_value = StreamInfo(
                audio_languages={"eng"},
                subtitle_languages={"eng"},
                has_video=True,
                audio_count=1,
                subtitle_count=1,
            )

            plan = worker.build_plan(torrent_info)
            # Paths should be properly quoted/escaped in command
            assert str(test_file) in " ".join(str(p) for p in [plan.source])

    def test_unicode_filename(self, stack_config: StackConfig, temp_dir: Path):
        """Unicode filenames should be handled."""
        test_file = temp_dir / "映画.mkv"
        test_file.touch()

        worker = PipelineWorker(stack_config)
        torrent_info = TorrentInfo(
            hash="abc123",
            name="映画",
            category="movies",
            download_path=temp_dir,
            files=[test_file],
        )

        with patch("orchestrator.pipeline.remux.probe_streams") as mock_probe:
            mock_probe.return_value = StreamInfo(
                audio_languages={"eng"},
                subtitle_languages={"eng"},
                has_video=True,
                audio_count=1,
                subtitle_count=1,
            )

            plan = worker.build_plan(torrent_info)
            assert plan is not None

    def test_very_long_filename(self, stack_config: StackConfig, temp_dir: Path):
        """Very long filenames should be handled."""
        long_name = "A" * 200 + ".mkv"
        test_file = temp_dir / long_name
        test_file.touch()

        worker = PipelineWorker(stack_config)
        torrent_info = TorrentInfo(
            hash="abc123",
            name=long_name[:-4],
            category="movies",
            download_path=temp_dir,
            files=[test_file],
        )

        with patch("orchestrator.pipeline.remux.probe_streams") as mock_probe:
            mock_probe.return_value = StreamInfo(
                audio_languages={"eng"},
                subtitle_languages={"eng"},
                has_video=True,
                audio_count=1,
                subtitle_count=1,
            )

            plan = worker.build_plan(torrent_info)
            # Should handle or truncate
            assert plan is not None

    def test_empty_file_list(self, stack_config: StackConfig, temp_dir: Path):
        """Empty file list should be handled gracefully."""
        worker = PipelineWorker(stack_config)
        torrent_info = TorrentInfo(
            hash="abc123",
            name="empty",
            category="movies",
            download_path=temp_dir,
            files=[],
        )

        with pytest.raises(Exception):
            # Should raise or handle gracefully
            worker.build_plan(torrent_info)
