"""Chromaprint acoustic fingerprinting for audio alignment.

Uses the ``fpcalc`` binary (from libchromaprint-tools) to generate raw
acoustic fingerprints from audio streams and cross-correlates them to
find the precise timing offset between two recordings of the same
content.  This is the core alignment mechanism for the media enrichment
pipeline — every cross-mux operation must pass chromaprint validation.

The algorithm:
  1. Run ``fpcalc -raw`` on each audio stream to get integer fingerprint
     arrays (one 32-bit int per ~0.1238s of audio at default settings).
  2. Slide one fingerprint across the other at every offset in a search
     window, counting matching bits (32 - popcount(a XOR b)) at each
     position.
  3. The offset with the highest bit-match ratio is the alignment point.
  4. If the best score exceeds the configured threshold (default 0.70),
     the alignment is accepted and the offset is converted to seconds.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

# Default fpcalc sample rate: ~8.065 items/second (chromaprint internal).
# Each fingerprint integer represents ~0.1238 seconds of audio.
_FPCALC_ITEM_DURATION = 0.1238

# Maximum shift (in fingerprint items) when cross-correlating.
# At ~0.12s per item, 2400 items ≈ ~5 minutes of shift in either direction.
_MAX_SHIFT_ITEMS = 2400


@dataclass
class AlignmentResult:
    """Result of a successful chromaprint alignment."""

    score: float  # 0.0 – 1.0 correlation score
    offset_seconds: float  # Positive = candidate starts later than library
    library_fp_length: int  # Number of fingerprint items
    candidate_fp_length: int


def fingerprint(
    path: Path,
    *,
    stream_index: int = 0,
    duration: int = 120,
) -> Optional[List[int]]:
    """Generate a raw chromaprint fingerprint for an audio stream.

    When ``stream_index`` > 0, uses ffmpeg to pipe the specific audio
    stream to fpcalc (since fpcalc itself always fingerprints stream 0).
    This ensures we compare the correct audio tracks when files have
    multiple audio streams (e.g., dub as stream 0, original as stream 1).

    Args:
        path: Path to the media file.
        stream_index: Audio stream index to fingerprint (0-based among
            audio streams, not the absolute ffmpeg stream index).
        duration: Seconds of audio to analyze.

    Returns:
        List of raw 32-bit integers, or None on failure.
    """
    if stream_index > 0:
        # Use ffmpeg to extract the specific audio stream to a pipe,
        # then feed it to fpcalc via stdin (WAV format).
        return _fingerprint_via_ffmpeg(path, stream_index, duration)

    cmd = [
        "fpcalc",
        "-raw",
        "-length", str(duration),
        "-channels", "1",  # Mono — consistent regardless of source layout
        str(path),
    ]
    log.debug("running fpcalc: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        log.error("fpcalc binary not found — install libchromaprint-tools")
        return None
    except subprocess.TimeoutExpired:
        log.error("fpcalc timed out after 120s on %s", path.name)
        return None

    if result.returncode != 0:
        log.warning(
            "fpcalc failed (rc=%d) for %s: %s",
            result.returncode, path.name, result.stderr.strip()[:200],
        )
        return None

    # Parse output: look for FINGERPRINT=<comma-separated ints>
    for line in result.stdout.splitlines():
        if line.startswith("FINGERPRINT="):
            raw = line[len("FINGERPRINT="):]
            try:
                return [int(x) for x in raw.split(",") if x.strip()]
            except ValueError:
                log.error("failed to parse fpcalc fingerprint integers")
                return None

    log.warning("no FINGERPRINT line in fpcalc output for %s", path.name)
    return None


def _fingerprint_via_ffmpeg(
    path: Path, stream_index: int, duration: int,
) -> Optional[List[int]]:
    """Extract a specific audio stream via ffmpeg and fingerprint it.

    Uses ffmpeg to demux the audio stream to a temporary WAV file,
    then runs fpcalc on that file.  This is necessary because fpcalc
    always fingerprints audio stream 0.
    """
    import tempfile
    import os

    # Create a temp WAV file
    fd, tmp_wav = tempfile.mkstemp(suffix=".wav", prefix="_chromaprint_")
    os.close(fd)

    try:
        # Extract the specific audio stream to WAV
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-t", str(duration),
            "-i", str(path),
            "-map", f"0:a:{stream_index}",
            "-ac", "1",  # Mono
            "-ar", "11025",  # Match fpcalc default sample rate
            tmp_wav,
        ]
        result = subprocess.run(
            ffmpeg_cmd, capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            log.warning(
                "ffmpeg audio extraction failed for stream %d of %s: %s",
                stream_index, path.name, result.stderr[:200],
            )
            return None

        # Now fingerprint the extracted WAV (stream 0, since it's the only one)
        return fingerprint(Path(tmp_wav), stream_index=0, duration=duration)
    except subprocess.TimeoutExpired:
        log.error("ffmpeg audio extraction timed out for %s", path.name)
        return None
    finally:
        try:
            os.unlink(tmp_wav)
        except OSError:
            pass


def _popcount32(x: int) -> int:
    """Count set bits in a 32-bit integer."""
    return bin(x & 0xFFFFFFFF).count("1")


def _score_overlap(fp_a: List[int], fp_b: List[int],
                   start_a: int, start_b: int, overlap: int) -> float:
    """Compute bit-match score for a specific alignment.

    Uses bin().count('1') which CPython optimizes internally.
    """
    matching = 0
    for i in range(overlap):
        matching += 32 - _popcount32(fp_a[start_a + i] ^ fp_b[start_b + i])
    return matching / (overlap * 32)


def correlate(
    fp_a: List[int],
    fp_b: List[int],
    *,
    max_shift: int = _MAX_SHIFT_ITEMS,
) -> tuple[float, float]:
    """Cross-correlate two raw chromaprint fingerprints.

    Uses a two-pass approach for performance:
      1. Coarse pass: test every 8th offset to find the best region
      2. Fine pass: test every offset within ±16 of the coarse best

    This reduces the O(n*m) cost by ~8x while maintaining alignment
    accuracy to within 1 fingerprint item (~0.12 seconds).

    Args:
        fp_a: Library file fingerprint.
        fp_b: Candidate file fingerprint.
        max_shift: Maximum shift in fingerprint items (each ~0.1238s).

    Returns:
        (best_score, offset_seconds) where:
        - best_score is 0.0–1.0 (fraction of matching bits at best offset)
        - offset_seconds is the time offset to apply to the candidate.
          Positive means candidate audio should start *later* (i.e., use
          ``-itsoffset <offset>`` before the candidate ``-i`` in ffmpeg).
    """
    len_a = len(fp_a)
    len_b = len(fp_b)

    if not fp_a or not fp_b:
        return 0.0, 0.0

    best_score = 0.0
    best_offset_items = 0

    effective_max = min(max_shift, max(len_a, len_b))

    def _eval(offset: int) -> float:
        if offset >= 0:
            sa, sb = offset, 0
        else:
            sa, sb = 0, -offset
        overlap = min(len_a - sa, len_b - sb)
        if overlap < 10:
            return 0.0
        return _score_overlap(fp_a, fp_b, sa, sb, overlap)

    # Pass 1: Coarse scan (every 8th offset)
    coarse_step = 8
    for offset in range(-effective_max, effective_max + 1, coarse_step):
        score = _eval(offset)
        if score > best_score:
            best_score = score
            best_offset_items = offset

    # Pass 2: Fine scan around the coarse best (±16 items = ±2 seconds)
    fine_radius = 16
    fine_start = max(-effective_max, best_offset_items - fine_radius)
    fine_end = min(effective_max, best_offset_items + fine_radius)
    for offset in range(fine_start, fine_end + 1):
        score = _eval(offset)
        if score > best_score:
            best_score = score
            best_offset_items = offset

    offset_seconds = best_offset_items * _FPCALC_ITEM_DURATION

    return best_score, offset_seconds


def validate_and_align(
    library_path: Path,
    candidate_path: Path,
    *,
    lib_stream: int = 0,
    cand_stream: int = 0,
    threshold: float = 0.70,
    duration: int = 120,
) -> Optional[AlignmentResult]:
    """High-level: fingerprint both files and check alignment quality.

    This is the main entry point for the enrichment pipeline.  It
    fingerprints both the library file and the candidate file, cross-
    correlates them, and returns an AlignmentResult if the score meets
    the threshold.

    Args:
        library_path: Existing library file.
        candidate_path: Candidate file with desired audio track.
        lib_stream: Audio stream index in library file.
        cand_stream: Audio stream index in candidate file.
        threshold: Minimum correlation score to accept (0.0–1.0).
        duration: Seconds of audio to fingerprint.

    Returns:
        AlignmentResult if score >= threshold, else None.
    """
    log.info(
        "chromaprint: fingerprinting library=%s candidate=%s (threshold=%.2f)",
        library_path.name, candidate_path.name, threshold,
    )

    fp_lib = fingerprint(library_path, stream_index=lib_stream, duration=duration)
    if fp_lib is None:
        log.error("chromaprint: failed to fingerprint library file %s", library_path.name)
        return None

    fp_cand = fingerprint(candidate_path, stream_index=cand_stream, duration=duration)
    if fp_cand is None:
        log.error("chromaprint: failed to fingerprint candidate file %s", candidate_path.name)
        return None

    log.info(
        "chromaprint: fingerprinted %d items (lib) vs %d items (cand)",
        len(fp_lib), len(fp_cand),
    )

    score, offset = correlate(fp_lib, fp_cand)

    log.info(
        "chromaprint: correlation=%.4f offset=%.2fs (threshold=%.2f) — %s",
        score, offset, threshold,
        "ACCEPTED" if score >= threshold else "REJECTED",
    )

    if score < threshold:
        return None

    return AlignmentResult(
        score=score,
        offset_seconds=offset,
        library_fp_length=len(fp_lib),
        candidate_fp_length=len(fp_cand),
    )
