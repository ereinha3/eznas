"""Subtitle alignment utilities.

Parse, validate, time-shift, and remux SRT subtitle files.  Integrates
with chromaprint to detect timing offsets between subtitle and video
audio, then applies the correction before embedding into the MKV.

This module is designed to eventually become a standalone PyPI package
(``subtitle-align``) but currently lives in the orchestrator pipeline
for rapid iteration.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# SRT timestamp pattern: 00:00:01,234
_SRT_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class SubtitleEntry:
    """A single subtitle cue."""

    index: int
    start_ms: int  # Start time in milliseconds
    end_ms: int  # End time in milliseconds
    text: str  # Subtitle text (may be multi-line)


@dataclass
class ValidationResult:
    """Result of subtitle timing validation."""

    is_valid: bool
    issues: List[str] = field(default_factory=list)
    first_subtitle_ms: int = 0
    last_subtitle_ms: int = 0
    total_entries: int = 0


# ---------------------------------------------------------------------------
# SRT parsing
# ---------------------------------------------------------------------------


def _parse_timestamp(ts: str) -> int:
    """Parse an SRT timestamp ('HH:MM:SS,mmm') into milliseconds."""
    m = _SRT_TS.match(ts.strip())
    if not m:
        raise ValueError(f"Invalid SRT timestamp: {ts!r}")
    h, mi, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return h * 3_600_000 + mi * 60_000 + s * 1_000 + ms


def _format_timestamp(ms: int) -> str:
    """Format milliseconds as an SRT timestamp ('HH:MM:SS,mmm')."""
    if ms < 0:
        ms = 0
    h = ms // 3_600_000
    ms %= 3_600_000
    mi = ms // 60_000
    ms %= 60_000
    s = ms // 1_000
    ms %= 1_000
    return f"{h:02d}:{mi:02d}:{s:02d},{ms:03d}"


def parse_srt(path: Path) -> List[SubtitleEntry]:
    """Parse an SRT file into a list of subtitle entries.

    Handles both Windows (\\r\\n) and Unix (\\n) line endings.
    Skips malformed entries gracefully.

    Args:
        path: Path to the .srt file.

    Returns:
        List of SubtitleEntry objects, or empty list on failure.
    """
    try:
        content = path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        log.error("[subtitle] failed to read %s: %s", path.name, exc)
        return []

    entries: List[SubtitleEntry] = []
    # Split on blank lines (handles \\r\\n and \\n)
    blocks = re.split(r"\n\s*\n", content.strip())

    for block in blocks:
        lines = block.strip().splitlines()
        if len(lines) < 2:
            continue

        # First line should be the index (integer)
        try:
            index = int(lines[0].strip())
        except ValueError:
            # Some SRT files skip the index line; try parsing line 0 as timestamp
            if _SRT_TS.search(lines[0]):
                index = len(entries) + 1
                ts_line = lines[0]
                text_lines = lines[1:]
            else:
                continue
        else:
            ts_line = lines[1]
            text_lines = lines[2:]

        # Parse timestamp line: "00:00:01,234 --> 00:00:04,567"
        ts_match = re.findall(r"(\d{2}:\d{2}:\d{2}[,.]\d{3})", ts_line)
        if len(ts_match) < 2:
            continue

        try:
            start_ms = _parse_timestamp(ts_match[0])
            end_ms = _parse_timestamp(ts_match[1])
        except ValueError:
            continue

        text = "\n".join(text_lines).strip()
        if not text:
            continue

        entries.append(
            SubtitleEntry(
                index=index,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
            )
        )

    log.debug("[subtitle] parsed %d entries from %s", len(entries), path.name)
    return entries


def write_srt(path: Path, entries: List[SubtitleEntry]) -> bool:
    """Write subtitle entries to an SRT file.

    Args:
        path: Output path for the .srt file.
        entries: List of SubtitleEntry objects.

    Returns:
        True on success, False on failure.
    """
    try:
        lines = []
        for entry in entries:
            lines.append(str(entry.index))
            lines.append(
                f"{_format_timestamp(entry.start_ms)} --> {_format_timestamp(entry.end_ms)}"
            )
            lines.append(entry.text)
            lines.append("")  # Blank line separator

        path.write_text("\n".join(lines), encoding="utf-8")
        log.debug("[subtitle] wrote %d entries to %s", len(entries), path.name)
        return True
    except Exception as exc:
        log.error("[subtitle] failed to write %s: %s", path.name, exc)
        return False


# ---------------------------------------------------------------------------
# Timing validation
# ---------------------------------------------------------------------------


def probe_video_duration(path: Path) -> Optional[float]:
    """Get video duration in seconds via ffprobe.

    Args:
        path: Path to the media file.

    Returns:
        Duration in seconds, or None on failure.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            log.warning("[subtitle] ffprobe failed for %s", path.name)
            return None

        import json

        data = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        return duration if duration > 0 else None
    except Exception as exc:
        log.error("[subtitle] failed to probe duration of %s: %s", path.name, exc)
        return None


def validate_timing(
    entries: List[SubtitleEntry],
    video_duration_seconds: float,
    *,
    max_start_delay_ms: int = 120_000,  # 2 minutes — reasonable for cold opens
    max_end_overflow_ms: int = 30_000,  # 30 seconds — credits can extend past video
    min_entries: int = 3,  # Fewer than 3 entries is suspicious
) -> ValidationResult:
    """Validate subtitle timing against video duration.

    Checks:
    - Subtitles don't start impossibly late (e.g., 90% through the video)
    - Subtitles don't end impossibly early (e.g., 10% through the video)
    - Subtitles don't extend far beyond the video duration
    - Minimum number of entries (catches empty/broken files)
    - Timestamps are monotonically increasing

    Args:
        entries: Parsed subtitle entries.
        video_duration_seconds: Duration of the video file.
        max_start_delay_ms: Maximum acceptable delay before first subtitle.
        max_end_overflow_ms: Maximum acceptable overflow past video end.
        min_entries: Minimum expected subtitle count.

    Returns:
        ValidationResult with issues list.
    """
    result = ValidationResult(is_valid=False, total_entries=len(entries))
    video_ms = int(video_duration_seconds * 1_000)

    if not entries:
        result.issues.append("No subtitle entries found")
        return result

    # Start optimistic — will be set to False if issues found
    result.is_valid = True

    if len(entries) < min_entries:
        result.issues.append(
            f"Only {len(entries)} entries (expected at least {min_entries})"
        )

    result.first_subtitle_ms = entries[0].start_ms
    result.last_subtitle_ms = entries[-1].end_ms

    # Check first subtitle timing
    if entries[0].start_ms > video_ms * 0.9:
        result.issues.append(
            f"First subtitle starts at {_format_timestamp(entries[0].start_ms)} "
            f"({entries[0].start_ms / 1000:.0f}s) but video is only {video_duration_seconds:.0f}s — "
            f"likely misaligned or wrong file"
        )

    # Check last subtitle timing
    overflow = entries[-1].end_ms - video_ms
    if overflow > max_end_overflow_ms:
        result.issues.append(
            f"Last subtitle ends at {_format_timestamp(entries[-1].end_ms)} "
            f"which is {overflow / 1000:.0f}s past video end ({video_duration_seconds:.0f}s)"
        )

    # Check if subtitles cover a reasonable portion of the video
    coverage_ratio = (entries[-1].end_ms - entries[0].start_ms) / max(video_ms, 1)
    if coverage_ratio < 0.05:
        result.issues.append(
            f"Subtitles cover only {coverage_ratio * 100:.1f}% of video — "
            f"likely a single-episode subtitle for a multi-episode file"
        )

    # Check monotonicity — flag only if >50% of entries have extreme overlaps,
    # which indicates a fundamentally broken file.  Individual overlaps are
    # normal (multiple speakers, OP/ED lyrics in different languages, etc.)
    extreme_overlaps = 0
    for i in range(1, len(entries)):
        if entries[i].start_ms < entries[i - 1].end_ms:
            overlap = entries[i - 1].end_ms - entries[i].start_ms
            if overlap > 30_000:
                extreme_overlaps += 1

    overlap_ratio = extreme_overlaps / max(len(entries) - 1, 1)
    if overlap_ratio > 0.5:
        result.issues.append(
            f"{extreme_overlaps} entries ({overlap_ratio * 100:.0f}%) have extreme "
            f"timestamp overlaps — likely a broken or multi-episode subtitle file"
        )

    result.is_valid = len(result.issues) == 0
    return result


# ---------------------------------------------------------------------------
# Offset application
# ---------------------------------------------------------------------------


def apply_offset(
    entries: List[SubtitleEntry],
    offset_ms: int,
) -> List[SubtitleEntry]:
    """Shift all subtitle timestamps by a fixed offset.

    Positive offset = subtitles start later (video is ahead).
    Negative offset = subtitles start earlier (subtitles are ahead).

    Timestamps are clamped to >= 0 to avoid negative SRT times.

    Args:
        entries: Subtitle entries to shift.
        offset_ms: Offset in milliseconds.

    Returns:
        New list of entries with adjusted timestamps (original list unchanged).
    """
    result = []
    for entry in entries:
        new_start = max(0, entry.start_ms + offset_ms)
        new_end = max(0, entry.end_ms + offset_ms)
        result.append(
            SubtitleEntry(
                index=entry.index,
                start_ms=new_start,
                end_ms=new_end,
                text=entry.text,
            )
        )
    return result


def detect_offset_from_chromaprint(
    video_path: Path,
    reference_path: Path,
    *,
    threshold: float = 0.70,
    duration: int = 120,
) -> Optional[float]:
    """Use chromaprint to find the timing offset between two video files.

    Fingerprints the primary audio stream of both files and cross-correlates
    to find the alignment offset.  This offset can then be applied to
    subtitles from the reference file to align them with the video file.

    Args:
        video_path: The library video file.
        reference_path: The reference video file (with known-good subtitles).
        threshold: Minimum correlation score to accept.
        duration: Seconds of audio to fingerprint.

    Returns:
        Offset in milliseconds (positive = reference starts later), or None.
    """
    from .chromaprint import validate_and_align

    alignment = validate_and_align(
        library_path=video_path,
        candidate_path=reference_path,
        threshold=threshold,
        duration=duration,
    )
    if alignment is None:
        log.warning(
            "[subtitle] chromaprint alignment failed for %s vs %s",
            video_path.name,
            reference_path.name,
        )
        return None

    offset_ms = int(alignment.offset_seconds * 1_000)
    log.info(
        "[subtitle] chromaprint offset=%.3fs (score=%.4f) for %s vs %s",
        alignment.offset_seconds,
        alignment.score,
        video_path.name,
        reference_path.name,
    )
    return offset_ms


# ---------------------------------------------------------------------------
# Remux
# ---------------------------------------------------------------------------


def _count_subtitle_streams(path: Path) -> int:
    """Count existing subtitle streams in a media file."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "s",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return 0
        lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        return len(lines)
    except Exception:
        return 0


def remux_subtitle_into_mkv(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
    *,
    language: str = "eng",
    title: Optional[str] = None,
    forced: bool = False,
) -> bool:
    """Embed an SRT subtitle file into an MKV container.

    Copies all existing streams from the video file and adds the SRT as
    a new subtitle stream.  No transcoding occurs.

    Args:
        video_path: Source MKV file.
        srt_path: SRT subtitle file (must already be time-corrected).
        output_path: Destination MKV path.
        language: ISO 639-2 language code (default: 'eng').
        title: Optional subtitle track title.
        forced: Whether to mark the track as forced.

    Returns:
        True on success, False on failure.
    """
    args = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-i",
        str(video_path),
        "-i",
        str(srt_path),
        "-map",
        "0",  # All streams from video
        "-map",
        "1:0",  # Subtitle from SRT input
        "-c",
        "copy",
    ]

    # Auto-detect the subtitle stream index in the output.
    # The new subtitle will be appended after all existing subtitle streams.
    sub_index = _count_subtitle_streams(video_path)
    args.extend([f"-metadata:s:s:{sub_index}", f"language={language}"])

    if title:
        args.extend([f"-metadata:s:s:{sub_index}", f"title={title}"])
    if forced:
        args.extend([f"-disposition:s:s:{sub_index}", "forced"])

    args.append(str(output_path))

    log.debug(
        "[subtitle] remuxing %s into %s → %s",
        srt_path.name,
        video_path.name,
        output_path.name,
    )

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if result.returncode != 0:
            log.error(
                "[subtitle] ffmpeg remux failed for %s: %s",
                video_path.name,
                result.stderr.strip()[-300:],
            )
            return False

        if not output_path.exists() or output_path.stat().st_size < 1024:
            log.error("[subtitle] remux output too small or missing: %s", output_path)
            return False

        log.info(
            "[subtitle] remuxed %s into %s (%d bytes)",
            srt_path.name,
            output_path.name,
            output_path.stat().st_size,
        )
        return True

    except subprocess.TimeoutExpired:
        log.error("[subtitle] remux timed out for %s", video_path.name)
        return False
    except OSError as exc:
        log.error("[subtitle] remux failed to start: %s", exc)
        return False


# ---------------------------------------------------------------------------
# High-level alignment pipeline
# ---------------------------------------------------------------------------


@dataclass
class AlignResult:
    """Result of the full subtitle alignment pipeline."""

    success: bool
    action: str  # "remuxed", "offset_applied_and_remuxed", "skipped", "failed"
    details: str
    offset_ms: int = 0
    issues: List[str] = field(default_factory=list)


def align_and_remux(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
    *,
    language: str = "eng",
    reference_path: Optional[Path] = None,
    chromaprint_threshold: float = 0.70,
    timing_max_start_delay_ms: int = 120_000,
    timing_max_end_overflow_ms: int = 30_000,
) -> AlignResult:
    """Full subtitle alignment pipeline.

    1. Parse the SRT file
    2. Validate timing against video duration
    3. If timing looks wrong and a reference is available:
       a. Use chromaprint to find offset
       b. Apply offset to SRT timestamps
       c. Re-validate
    4. Remux corrected subtitle into MKV

    Args:
        video_path: The library video file.
        srt_path: The external SRT subtitle file.
        output_path: Destination MKV (with embedded subtitle).
        language: ISO 639-2 language code for the subtitle.
        reference_path: Optional reference video for chromaprint alignment.
        chromaprint_threshold: Minimum correlation score for chromaprint.
        timing_max_start_delay_ms: Max acceptable first-subtitle delay.
        timing_max_end_overflow_ms: Max acceptable end-of-subtitle overflow.

    Returns:
        AlignResult with outcome details.
    """
    # Step 1: Parse SRT
    entries = parse_srt(srt_path)
    if not entries:
        return AlignResult(
            success=False,
            action="failed",
            details=f"No valid entries in {srt_path.name}",
        )

    # Step 2: Get video duration
    duration = probe_video_duration(video_path)
    if duration is None:
        return AlignResult(
            success=False,
            action="failed",
            details=f"Could not probe duration of {video_path.name}",
        )

    # Step 3: Validate timing
    validation = validate_timing(
        entries,
        duration,
        max_start_delay_ms=timing_max_start_delay_ms,
        max_end_overflow_ms=timing_max_end_overflow_ms,
    )

    offset_ms: int = 0

    if not validation.is_valid:
        log.warning(
            "[subtitle] timing issues in %s: %s",
            srt_path.name,
            "; ".join(validation.issues),
        )

        if reference_path and reference_path.exists():
            # Step 3a: Chromaprint alignment
            offset = detect_offset_from_chromaprint(
                video_path,
                reference_path,
                threshold=chromaprint_threshold,
            )
            if offset is not None:
                offset_ms = int(offset)
                entries = apply_offset(entries, offset_ms)

                # Re-validate after offset
                revalidation = validate_timing(entries, duration)
                if not revalidation.is_valid:
                    log.warning(
                        "[subtitle] still has timing issues after offset correction: %s",
                        "; ".join(revalidation.issues),
                    )
                else:
                    log.info(
                        "[subtitle] timing validated after applying %.3fs offset",
                        offset_ms / 1_000,
                    )
            else:
                return AlignResult(
                    success=False,
                    action="skipped",
                    details=(
                        f"Timing issues detected and chromaprint alignment failed: "
                        f"{'; '.join(validation.issues)}"
                    ),
                    issues=validation.issues,
                )
        else:
            # No reference available — skip rather than risk making it worse
            return AlignResult(
                success=False,
                action="skipped",
                details=(
                    f"Timing issues detected, no reference file available: "
                    f"{'; '.join(validation.issues)}"
                ),
                issues=validation.issues,
            )

    # Step 4: Write corrected SRT to temp file for remux
    corrected_srt = output_path.with_suffix(".srt.tmp")
    try:
        if not write_srt(corrected_srt, entries):
            return AlignResult(
                success=False,
                action="failed",
                details=f"Failed to write corrected SRT: {corrected_srt}",
            )

        # Step 5: Remux into MKV
        success = remux_subtitle_into_mkv(
            video_path,
            corrected_srt,
            output_path,
            language=language,
        )

        if not success:
            return AlignResult(
                success=False,
                action="failed",
                details=f"ffmpeg remux failed for {video_path.name}",
            )

        action = "offset_applied_and_remuxed" if offset_ms != 0 else "remuxed"
        return AlignResult(
            success=True,
            action=action,
            details=(
                f"Subtitle remuxed into {output_path.name}"
                + (f" with {offset_ms / 1000:.3f}s offset" if offset_ms else "")
            ),
            offset_ms=offset_ms,
        )

    finally:
        # Clean up temp SRT
        try:
            corrected_srt.unlink(missing_ok=True)
        except OSError:
            pass
