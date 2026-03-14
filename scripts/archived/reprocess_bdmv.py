#!/usr/bin/env python3
"""One-off script to re-process BDMV structures on scratch using the new
BDMV detection, CLPI language parsing, and language-aware remux pipeline.

Usage:
    python3 scripts/reprocess_bdmv.py [--dry-run]

This script runs on the HOST (not inside Docker) and has direct access to
/mnt/scratch and /mnt/pool paths. It uses the orchestrator's pipeline modules
for BDMV detection and CLPI parsing.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

# Add project root to path so we can import pipeline modules.
# The orchestrator package __init__ imports FastAPI (not available on host),
# so we load the pipeline submodules directly by file path.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

def _load_module(name: str, filepath: Path):
    """Load a Python module from file path, bypassing package __init__."""
    spec = importlib.util.spec_from_file_location(name, filepath)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[name] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod

_bdmv = _load_module(
    "orchestrator.pipeline.bdmv",
    PROJECT_ROOT / "orchestrator" / "pipeline" / "bdmv.py",
)
_remux = _load_module(
    "orchestrator.pipeline.remux",
    PROJECT_ROOT / "orchestrator" / "pipeline" / "remux.py",
)

detect_bdmv = _bdmv.detect_bdmv
find_main_feature = _bdmv.find_main_feature
get_bdmv_stream_languages = _bdmv.get_bdmv_stream_languages
map_clpi_to_ffprobe_indices = _bdmv.map_clpi_to_ffprobe_indices
TrackSelection = _remux.TrackSelection
build_ffmpeg_command = _remux.build_ffmpeg_command

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# User's media policy
KEEP_AUDIO = ["eng", "und"]
KEEP_SUBS = ["eng"]

# BDMVs to process: (scratch_path, movie_title, year, original_language_iso)
# NOTE: All others already processed successfully.
BDMV_JOBS = [
    (
        "/mnt/scratch/complete/Leon_1994_BD",
        "Léon - The Professional", "1994", "eng",
    ),
]

LIBRARY_ROOT = Path("/mnt/pool/media/movies")
# Stage output directly alongside the final destination (on pool, not scratch!)
# This avoids doubling disk usage on scratch where the source BDMVs also live.
STAGING_ROOT = Path("/mnt/pool/media/.staging-bdmv-reprocess")


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------

def process_bdmv(
    scratch_path: str,
    title: str,
    year: str,
    original_language: str,
    *,
    dry_run: bool = False,
) -> bool:
    """Process a single BDMV and output to library."""
    source_dir = Path(scratch_path)
    if not source_dir.exists():
        print(f"  SKIP: {scratch_path} does not exist")
        return False

    # Detect BDMV structure
    bdmv_root = detect_bdmv(source_dir)
    if bdmv_root is None:
        print(f"  ERROR: No BDMV structure found in {scratch_path}")
        return False

    # Find main feature
    feature = find_main_feature(bdmv_root)
    if feature is None:
        print(f"  ERROR: No .m2ts files found in {bdmv_root}/STREAM/")
        return False

    main_m2ts, clip_id = feature
    size_gb = main_m2ts.stat().st_size / (1024 ** 3)
    print(f"  Main feature: {clip_id}.m2ts ({size_gb:.1f} GB)")

    # Parse CLPI for language metadata
    stream_languages = None
    clpi_raw = get_bdmv_stream_languages(bdmv_root, clip_id)
    if clpi_raw:
        stream_languages = map_clpi_to_ffprobe_indices(main_m2ts, clpi_raw)
        if stream_languages:
            audio_langs = [s["lang"] for s in stream_languages if s["type"] == "audio"]
            sub_langs = [s["lang"] for s in stream_languages if s["type"] == "subtitle"]
            print(f"  CLPI audio: {audio_langs}")
            print(f"  CLPI subs:  {sub_langs}")
        else:
            print("  WARNING: CLPI entries found but ffprobe mapping failed")
    else:
        print("  WARNING: No CLPI data found, will keep all tracks")

    # Compute paths
    folder_name = f"{title} ({year})"
    file_name = f"{title} ({year}).mkv"
    final_dir = LIBRARY_ROOT / folder_name
    final_output = final_dir / file_name

    staging_output = STAGING_ROOT / f"{clip_id}_{title}.mkv"

    print(f"  Output: {final_output}")

    # Build ffmpeg command
    selection = TrackSelection(audio=KEEP_AUDIO, subtitles=KEEP_SUBS)
    command = build_ffmpeg_command(
        main_m2ts, staging_output, selection,
        original_language=original_language,
        stream_languages=stream_languages,
    )

    if dry_run:
        # In dry-run mode, show the ffmpeg command but don't execute
        # Truncate long commands for readability
        cmd_str = " ".join(command)
        if len(cmd_str) > 200:
            cmd_str = cmd_str[:200] + "..."
        print(f"  DRY RUN — would execute: {cmd_str}")
        return True

    # Create directories
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    final_dir.mkdir(parents=True, exist_ok=True)

    # Run ffmpeg
    print(f"  Running ffmpeg (this may take several minutes)...")
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True,
            timeout=7200,  # 2 hour timeout for large BDMVs
        )
    except subprocess.TimeoutExpired:
        print(f"  ERROR: ffmpeg timed out after 2 hours")
        return False

    if result.returncode != 0:
        stderr_lines = result.stderr.strip().split("\n")
        tail = "\n".join(stderr_lines[-5:])
        print(f"  ERROR: ffmpeg failed (exit {result.returncode}):\n{tail}")
        return False

    # Verify output
    if not staging_output.exists():
        print(f"  ERROR: Output file not created")
        return False

    output_size = staging_output.stat().st_size
    if output_size < 1024 * 1024:  # Less than 1MB is suspicious
        print(f"  ERROR: Output too small ({output_size} bytes), likely corrupt")
        staging_output.unlink()
        return False

    output_gb = output_size / (1024 ** 3)
    print(f"  Output size: {output_gb:.2f} GB")

    # Move to library
    shutil.move(str(staging_output), str(final_output))
    print(f"  Moved to library: {final_output}")

    # Verify with ffprobe
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(final_output)],
            capture_output=True, text=True, timeout=30,
        )
        if probe.returncode == 0:
            data = json.loads(probe.stdout)
            fmt = data.get("format", {})
            duration = float(fmt.get("duration", 0))
            streams = data.get("streams", [])
            audio_tracks = [
                s.get("tags", {}).get("language", "und")
                for s in streams if s.get("codec_type") == "audio"
            ]
            sub_tracks = [
                s.get("tags", {}).get("language", "und")
                for s in streams if s.get("codec_type") == "subtitle"
            ]
            print(f"  Verification: {duration/60:.1f} min, "
                  f"audio={audio_tracks}, subs={sub_tracks}")
    except Exception:
        pass

    return True


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("=" * 60)
        print("DRY RUN MODE — no files will be modified")
        print("=" * 60)
    else:
        print("=" * 60)
        print("BDMV RE-PROCESSING — will create MKV files in library")
        print("=" * 60)

    print()

    succeeded = 0
    failed = 0

    for scratch_path, title, year, orig_lang in BDMV_JOBS:
        print(f"--- {title} ({year}) [original: {orig_lang}] ---")
        print(f"  Source: {scratch_path}")

        ok = process_bdmv(
            scratch_path, title, year, orig_lang,
            dry_run=dry_run,
        )

        if ok:
            succeeded += 1
            print(f"  DONE")
        else:
            failed += 1
            print(f"  FAILED")
        print()

    # Cleanup staging
    if not dry_run and STAGING_ROOT.exists():
        try:
            STAGING_ROOT.rmdir()
        except OSError:
            pass

    print("=" * 60)
    print(f"Results: {succeeded} succeeded, {failed} failed")

    if not dry_run and succeeded > 0:
        print()
        print("Next steps:")
        print("  1. Verify the output files in Jellyfin")
        print("  2. Delete the BDMV sources from scratch:")
        for scratch_path, title, year, _ in BDMV_JOBS:
            if Path(scratch_path).exists():
                print(f"     rm -rf \"{scratch_path}\"")
        print("  3. Trigger a Radarr rescan:")
        print("     curl -X POST http://localhost:7878/api/v3/command \\")
        print("       -H 'X-Api-Key: $RADARR_API_KEY' \\")
        print("       -H 'Content-Type: application/json' \\")
        print("       -d '{\"name\": \"RescanMovie\"}'")


if __name__ == "__main__":
    main()
