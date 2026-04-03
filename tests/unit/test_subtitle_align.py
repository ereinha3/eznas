"""Tests for subtitle alignment utilities."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from orchestrator.pipeline.subtitle_align import (
    parse_srt,
    write_srt,
    validate_timing,
    apply_offset,
    probe_video_duration,
    remux_subtitle_into_mkv,
    align_and_remux,
    SubtitleEntry,
    ValidationResult,
    AlignResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_srt_content() -> str:
    """A well-formed SRT file with 5 entries spanning ~25 minutes."""
    return (
        "1\n"
        "00:00:30,000 --> 00:00:33,500\n"
        "Hello, welcome to the show.\n"
        "\n"
        "2\n"
        "00:05:00,000 --> 00:05:03,000\n"
        "This is five minutes in.\n"
        "\n"
        "3\n"
        "00:10:00,000 --> 00:10:05,000\n"
        "Ten minutes have passed.\n"
        "\n"
        "4\n"
        "00:20:00,000 --> 00:20:04,000\n"
        "We're getting close to the end.\n"
        "\n"
        "5\n"
        "00:24:30,000 --> 00:24:35,000\n"
        "That's all folks!\n"
    )


@pytest.fixture
def sample_srt_file(sample_srt_content: str, tmp_path: Path) -> Path:
    """Write sample SRT content to a temp file."""
    f = tmp_path / "test.en.srt"
    f.write_text(sample_srt_content, encoding="utf-8")
    return f


@pytest.fixture
def malformed_srt_file(tmp_path: Path) -> Path:
    """An SRT file with some malformed entries."""
    content = (
        "1\n"
        "00:00:01,000 --> 00:00:04,000\n"
        "Good entry.\n"
        "\n"
        "bad index\n"
        "00:00:05,000 --> 00:00:08,000\n"
        "This has a bad index line.\n"
        "\n"
        "3\n"
        "not a timestamp\n"
        "This has a bad timestamp.\n"
        "\n"
        "4\n"
        "00:00:10,000 --> 00:00:13,000\n"
        "Another good entry.\n"
    )
    f = tmp_path / "malformed.en.srt"
    f.write_text(content, encoding="utf-8")
    return f


@pytest.fixture
def empty_srt_file(tmp_path: Path) -> Path:
    """An empty SRT file."""
    f = tmp_path / "empty.en.srt"
    f.write_text("", encoding="utf-8")
    return f


@pytest.fixture
def video_duration() -> float:
    """25-minute video duration in seconds."""
    return 25 * 60  # 1500 seconds


# ---------------------------------------------------------------------------
# SRT Parsing Tests
# ---------------------------------------------------------------------------


class TestParseSrt:
    def test_parse_valid_srt(self, sample_srt_file: Path):
        """Should parse all 5 entries from a valid SRT file."""
        entries = parse_srt(sample_srt_file)
        assert len(entries) == 5
        assert entries[0].index == 1
        assert entries[0].start_ms == 30_000
        assert entries[0].end_ms == 33_500
        assert entries[0].text == "Hello, welcome to the show."
        assert entries[4].index == 5
        assert entries[4].start_ms == 24 * 60_000 + 30_000  # 00:24:30
        assert entries[4].end_ms == 24 * 60_000 + 35_000  # 00:24:35

    def test_parse_malformed_srt(self, malformed_srt_file: Path):
        """Should skip malformed entries and parse valid ones."""
        entries = parse_srt(malformed_srt_file)
        # Should get the 2 good entries + possibly the "bad index" one
        # (which gets a synthetic index)
        assert len(entries) >= 2
        # First good entry
        assert entries[0].text == "Good entry."
        # Last good entry
        assert entries[-1].text == "Another good entry."

    def test_parse_empty_srt(self, empty_srt_file: Path):
        """Should return empty list for empty file."""
        entries = parse_srt(empty_srt_file)
        assert len(entries) == 0

    def test_parse_nonexistent_file(self):
        """Should return empty list for non-existent file."""
        entries = parse_srt(Path("/nonexistent/file.srt"))
        assert len(entries) == 0

    def test_parse_multiline_text(self, tmp_path: Path):
        """Should handle multi-line subtitle text."""
        content = "1\n00:00:01,000 --> 00:00:04,000\nLine one\nLine two\nLine three\n"
        f = tmp_path / "multi.srt"
        f.write_text(content, encoding="utf-8")
        entries = parse_srt(f)
        assert len(entries) == 1
        assert "Line one\nLine two\nLine three" == entries[0].text

    def test_parse_windows_line_endings(self, tmp_path: Path):
        """Should handle \\r\\n line endings."""
        content = (
            "1\r\n"
            "00:00:01,000 --> 00:00:04,000\r\n"
            "Hello world\r\n"
            "\r\n"
            "2\r\n"
            "00:00:05,000 --> 00:00:08,000\r\n"
            "Second entry\r\n"
        )
        f = tmp_path / "windows.srt"
        f.write_text(content, encoding="utf-8")
        entries = parse_srt(f)
        assert len(entries) == 2
        assert entries[0].text == "Hello world"
        assert entries[1].text == "Second entry"

    def test_parse_utf8_bom(self, tmp_path: Path):
        """Should handle UTF-8 BOM."""
        content = "1\n00:00:01,000 --> 00:00:04,000\nHello\n"
        f = tmp_path / "bom.srt"
        f.write_text(content, encoding="utf-8-sig")
        entries = parse_srt(f)
        assert len(entries) == 1
        assert entries[0].text == "Hello"


# ---------------------------------------------------------------------------
# SRT Writing Tests
# ---------------------------------------------------------------------------


class TestWriteSrt:
    def test_write_and_roundtrip(self, sample_srt_file: Path, tmp_path: Path):
        """Writing and re-reading should preserve entries."""
        entries = parse_srt(sample_srt_file)
        output = tmp_path / "output.srt"
        assert write_srt(output, entries)
        assert output.exists()

        re_read = parse_srt(output)
        assert len(re_read) == len(entries)
        for a, b in zip(entries, re_read):
            assert a.start_ms == b.start_ms
            assert a.end_ms == b.end_ms
            assert a.text == b.text

    def test_write_empty_entries(self, tmp_path: Path):
        """Should handle empty entry list."""
        output = tmp_path / "empty.srt"
        assert write_srt(output, [])
        assert output.exists()
        assert output.read_text() == ""


# ---------------------------------------------------------------------------
# Timestamp Tests
# ---------------------------------------------------------------------------


class TestTimestamps:
    def test_parse_timestamp_comma(self):
        """Should parse comma-separated timestamps."""
        from orchestrator.pipeline.subtitle_align import _parse_timestamp

        assert _parse_timestamp("00:01:30,500") == 90_500
        assert _parse_timestamp("01:00:00,000") == 3_600_000
        assert _parse_timestamp("00:00:00,000") == 0

    def test_parse_timestamp_period(self):
        """Should parse period-separated timestamps (some SRT variants)."""
        from orchestrator.pipeline.subtitle_align import _parse_timestamp

        assert _parse_timestamp("00:01:30.500") == 90_500

    def test_format_timestamp(self):
        """Should format milliseconds as SRT timestamp."""
        from orchestrator.pipeline.subtitle_align import _format_timestamp

        assert _format_timestamp(90_500) == "00:01:30,500"
        assert _format_timestamp(3_600_000) == "01:00:00,000"
        assert _format_timestamp(0) == "00:00:00,000"

    def test_format_negative_timestamp(self):
        """Should clamp negative timestamps to 0."""
        from orchestrator.pipeline.subtitle_align import _format_timestamp

        assert _format_timestamp(-1000) == "00:00:00,000"


# ---------------------------------------------------------------------------
# Validation Tests
# ---------------------------------------------------------------------------


class TestValidateTiming:
    def test_valid_timing(self, sample_srt_file: Path, video_duration: float):
        """Subtitles spanning most of the video should be valid."""
        entries = parse_srt(sample_srt_file)
        result = validate_timing(entries, video_duration)
        assert result.is_valid
        assert result.total_entries == 5
        assert result.first_subtitle_ms == 30_000
        assert result.last_subtitle_ms == 24 * 60_000 + 35_000

    def test_subtitle_starts_too_late(self, tmp_path: Path):
        """First subtitle at 95% of video should be flagged."""
        content = "1\n00:23:45,000 --> 00:23:50,000\nVery late subtitle.\n"
        f = tmp_path / "late.srt"
        f.write_text(content, encoding="utf-8")
        entries = parse_srt(f)
        result = validate_timing(entries, 1500.0)  # 25 min video
        assert not result.is_valid
        assert any("First subtitle starts" in issue for issue in result.issues)

    def test_subtitle_ends_too_early(self, tmp_path: Path):
        """Subtitles covering only 2% of video should be flagged."""
        content = (
            "1\n"
            "00:00:01,000 --> 00:00:04,000\n"
            "Brief subtitle.\n"
            "\n"
            "2\n"
            "00:00:05,000 --> 00:00:08,000\n"
            "Another brief one.\n"
        )
        f = tmp_path / "early.srt"
        f.write_text(content, encoding="utf-8")
        entries = parse_srt(f)
        result = validate_timing(entries, 1500.0)  # 25 min video
        assert not result.is_valid
        assert any("cover only" in issue for issue in result.issues)

    def test_subtitle_extends_past_video(self, tmp_path: Path):
        """Subtitles ending 5 minutes past video should be flagged."""
        content = (
            "1\n"
            "00:00:01,000 --> 00:00:04,000\n"
            "Start.\n"
            "\n"
            "2\n"
            "00:20:00,000 --> 00:30:00,000\n"
            "Way past the end.\n"
        )
        f = tmp_path / "overflow.srt"
        f.write_text(content, encoding="utf-8")
        entries = parse_srt(f)
        result = validate_timing(entries, 1500.0)  # 25 min video
        assert not result.is_valid
        assert any("past video end" in issue for issue in result.issues)

    def test_too_few_entries(self, tmp_path: Path):
        """Single entry should be flagged."""
        content = "1\n00:00:01,000 --> 00:00:04,000\nOnly one.\n"
        f = tmp_path / "few.srt"
        f.write_text(content, encoding="utf-8")
        entries = parse_srt(f)
        result = validate_timing(entries, 1500.0)
        assert not result.is_valid
        assert any("Only 1 entries" in issue for issue in result.issues)

    def test_empty_entries(self):
        """Empty entry list should be invalid."""
        result = validate_timing([], 1500.0)
        assert not result.is_valid
        assert any("No subtitle entries" in issue for issue in result.issues)

    def test_credits_overflow_allowed(self, tmp_path: Path):
        """Subtitles extending 20s past video (credits) should be OK."""
        content = (
            "1\n"
            "00:00:01,000 --> 00:00:04,000\n"
            "Start.\n"
            "\n"
            "2\n"
            "00:24:50,000 --> 00:25:20,000\n"
            "Credits subtitle.\n"
        )
        f = tmp_path / "credits.srt"
        f.write_text(content, encoding="utf-8")
        entries = parse_srt(f)
        result = validate_timing(entries, 1500.0, min_entries=2)  # 25 min = 1500s
        assert result.is_valid


# ---------------------------------------------------------------------------
# Offset Application Tests
# ---------------------------------------------------------------------------


class TestApplyOffset:
    def test_positive_offset(self, sample_srt_file: Path):
        """Positive offset should delay all subtitles."""
        entries = parse_srt(sample_srt_file)
        shifted = apply_offset(entries, 5_000)  # +5 seconds

        assert shifted[0].start_ms == 35_000  # 30s + 5s
        assert shifted[0].end_ms == 38_500  # 33.5s + 5s
        # Original unchanged
        assert entries[0].start_ms == 30_000

    def test_negative_offset(self, sample_srt_file: Path):
        """Negative offset should advance all subtitles."""
        entries = parse_srt(sample_srt_file)
        shifted = apply_offset(entries, -10_000)  # -10 seconds

        assert shifted[0].start_ms == 20_000  # 30s - 10s
        assert shifted[0].end_ms == 23_500  # 33.5s - 10s

    def test_negative_offset_clamped(self, tmp_path: Path):
        """Negative offset that would produce negative times should clamp to 0."""
        content = "1\n00:00:05,000 --> 00:00:08,000\nEarly subtitle.\n"
        f = tmp_path / "early.srt"
        f.write_text(content, encoding="utf-8")
        entries = parse_srt(f)
        shifted = apply_offset(entries, -10_000)  # -10 seconds

        assert shifted[0].start_ms == 0  # Clamped from -5000
        assert shifted[0].end_ms == 0  # Clamped from -2000

    def test_zero_offset(self, sample_srt_file: Path):
        """Zero offset should return identical timestamps."""
        entries = parse_srt(sample_srt_file)
        shifted = apply_offset(entries, 0)

        for orig, new in zip(entries, shifted):
            assert orig.start_ms == new.start_ms
            assert orig.end_ms == new.end_ms


# ---------------------------------------------------------------------------
# Video Duration Probe Tests
# ---------------------------------------------------------------------------


class TestProbeVideoDuration:
    def test_successful_probe(self, tmp_path: Path):
        """Should return duration on successful ffprobe."""
        fake_file = tmp_path / "test.mkv"
        fake_file.write_bytes(b"fake video")

        probe_output = json.dumps({"format": {"duration": "1500.123"}})

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout=probe_output,
            )
            result = probe_video_duration(fake_file)
            assert result is not None
            assert abs(result - 1500.123) < 0.001

    def test_failed_probe(self, tmp_path: Path):
        """Should return None on ffprobe failure."""
        fake_file = tmp_path / "test.mkv"
        fake_file.write_bytes(b"fake video")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="")
            result = probe_video_duration(fake_file)
            assert result is None

    def test_nonexistent_file(self):
        """Should return None for non-existent file."""
        result = probe_video_duration(Path("/nonexistent/file.mkv"))
        assert result is None


# ---------------------------------------------------------------------------
# Remux Tests
# ---------------------------------------------------------------------------


class TestRemuxSubtitle:
    def test_successful_remux(self, tmp_path: Path):
        """Should return True on successful ffmpeg remux."""
        video = tmp_path / "test.mkv"
        srt = tmp_path / "test.en.srt"
        output = tmp_path / "test_out.mkv"

        video.write_bytes(b"fake video")
        srt.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello\n", encoding="utf-8")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            # Simulate output file creation
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "stat") as mock_stat:
                    mock_stat.return_value = MagicMock(st_size=100_000_000)
                    result = remux_subtitle_into_mkv(
                        video,
                        srt,
                        output,
                        language="eng",
                    )
                    assert result is True

    def test_failed_remux(self, tmp_path: Path):
        """Should return False on ffmpeg failure."""
        video = tmp_path / "test.mkv"
        srt = tmp_path / "test.en.srt"
        output = tmp_path / "test_out.mkv"

        video.write_bytes(b"fake video")
        srt.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello\n", encoding="utf-8")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="error")
            result = remux_subtitle_into_mkv(video, srt, output)
            assert result is False


# ---------------------------------------------------------------------------
# Full Pipeline Tests
# ---------------------------------------------------------------------------


class TestAlignAndRemux:
    def test_valid_subtitle_no_offset_needed(self, tmp_path: Path):
        """Valid subtitle should be remuxed without offset."""
        video = tmp_path / "test.mkv"
        srt = tmp_path / "test.en.srt"
        output = tmp_path / "test_out.mkv"

        video.write_bytes(b"fake video")
        srt.write_text(
            "1\n00:00:30,000 --> 00:00:33,000\nHello\n\n"
            "2\n00:05:00,000 --> 00:05:03,000\nFive min\n\n"
            "3\n00:10:00,000 --> 00:10:03,000\nTen min\n",
            encoding="utf-8",
        )

        with patch(
            "orchestrator.pipeline.subtitle_align.probe_video_duration"
        ) as mock_dur:
            mock_dur.return_value = 1500.0  # 25 min
            with patch(
                "orchestrator.pipeline.subtitle_align.remux_subtitle_into_mkv"
            ) as mock_remux:
                mock_remux.return_value = True
                result = align_and_remux(video, srt, output)

                assert result.success is True
                assert result.action == "remuxed"
                assert result.offset_ms == 0

    def test_invalid_subtitle_no_reference(self, tmp_path: Path):
        """Invalid subtitle without reference should be skipped."""
        video = tmp_path / "test.mkv"
        srt = tmp_path / "test.en.srt"
        output = tmp_path / "test_out.mkv"

        video.write_bytes(b"fake video")
        # Subtitle that starts at 95% of video
        srt.write_text(
            "1\n00:23:45,000 --> 00:23:50,000\nVery late\n",
            encoding="utf-8",
        )

        with patch(
            "orchestrator.pipeline.subtitle_align.probe_video_duration"
        ) as mock_dur:
            mock_dur.return_value = 1500.0
            result = align_and_remux(video, srt, output)

            assert result.success is False
            assert result.action == "skipped"
            assert "Timing issues" in result.details

    def test_empty_srt_file(self, tmp_path: Path):
        """Empty SRT should fail immediately."""
        video = tmp_path / "test.mkv"
        srt = tmp_path / "test.en.srt"
        output = tmp_path / "test_out.mkv"

        video.write_bytes(b"fake video")
        srt.write_text("", encoding="utf-8")

        result = align_and_remux(video, srt, output)
        assert result.success is False
        assert result.action == "failed"
        assert "No valid entries" in result.details

    def test_remux_failure(self, tmp_path: Path):
        """Failed remux should return failure."""
        video = tmp_path / "test.mkv"
        srt = tmp_path / "test.en.srt"
        output = tmp_path / "test_out.mkv"

        video.write_bytes(b"fake video")
        srt.write_text(
            "1\n00:00:30,000 --> 00:00:33,000\nHello\n\n"
            "2\n00:05:00,000 --> 00:05:03,000\nFive min\n\n"
            "3\n00:10:00,000 --> 00:10:03,000\nTen min\n",
            encoding="utf-8",
        )

        with patch(
            "orchestrator.pipeline.subtitle_align.probe_video_duration"
        ) as mock_dur:
            mock_dur.return_value = 1500.0
            with patch(
                "orchestrator.pipeline.subtitle_align.remux_subtitle_into_mkv"
            ) as mock_remux:
                mock_remux.return_value = False
                result = align_and_remux(video, srt, output)

                assert result.success is False
                assert result.action == "failed"
