#!/usr/bin/env python3
"""Strip duplicate audio tracks created by the enrichment pipeline bug.

A bug in _normalize_lang() caused the gap scanner to miss existing audio
tracks with 2-letter ISO 639-1 codes (e.g., "ja" vs "jpn"), leading to
duplicate audio being added.  This script finds those files and removes
the duplicate "Enrichment" tracks via ffmpeg copy-mode remux.

Usage (from host):
    # Dry-run — scan only:
    docker exec pipeline-worker python3 /app/scripts/strip_duplicate_audio.py

    # Execute — strip duplicates:
    docker exec pipeline-worker python3 /app/scripts/strip_duplicate_audio.py --execute

    # Custom parallelism (default 4):
    docker exec pipeline-worker python3 /app/scripts/strip_duplicate_audio.py --execute --workers 8
"""
import argparse
import json
import os
import subprocess
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Tuple

# ISO 639-1 → 639-2/B mapping (subset for normalization)
_ISO639_1_TO_2: Dict[str, str] = {
    "ja": "jpn", "en": "eng", "es": "spa", "fr": "fre", "de": "ger",
    "it": "ita", "pt": "por", "ru": "rus", "ko": "kor", "zh": "chi",
    "ar": "ara", "hi": "hin", "th": "tha", "vi": "vie", "pl": "pol",
    "nl": "dut", "sv": "swe", "da": "dan", "no": "nor", "fi": "fin",
}


def _norm(code: str) -> str:
    code = code.lower()
    if len(code) == 2:
        return _ISO639_1_TO_2.get(code, code)
    return code


def probe_streams(path: Path) -> list:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return []
    return json.loads(result.stdout).get("streams", [])


def find_duplicates(path: Path) -> Tuple[List[str], List[str]]:
    """Returns (keep_indices, drop_indices) for a file with duplicate audio.

    Strategy: for each language with >1 audio track, drop tracks titled
    "Enrichment (...)".  If all tracks for a language are enrichment tracks
    (shouldn't happen), keep the first one.
    """
    streams = probe_streams(path)
    if not streams:
        return [], []

    # Group audio tracks by normalized language
    by_lang: Dict[str, list] = defaultdict(list)
    for s in streams:
        if s.get("codec_type") != "audio":
            continue
        lang = _norm(s.get("tags", {}).get("language", "und"))
        by_lang[lang].append(s)

    keep: List[str] = []
    drop: List[str] = []

    for lang, tracks in by_lang.items():
        if len(tracks) <= 1:
            keep.append(str(tracks[0]["index"]))
            continue

        # Separate enrichment vs original tracks
        originals = []
        enrichments = []
        for t in tracks:
            title = (t.get("tags", {}).get("title", "") or "").lower()
            if "enrichment" in title:
                enrichments.append(t)
            else:
                originals.append(t)

        if originals:
            # Keep all originals, drop all enrichments
            for t in originals:
                keep.append(str(t["index"]))
            for t in enrichments:
                drop.append(str(t["index"]))
        else:
            # All are enrichment — keep first, drop rest
            keep.append(str(tracks[0]["index"]))
            for t in tracks[1:]:
                drop.append(str(t["index"]))

    return keep, drop


def strip_duplicates(path: Path) -> bool:
    """Remove duplicate audio tracks via ffmpeg copy-mode remux."""
    keep, drop = find_duplicates(path)

    if not drop:
        return True  # Nothing to do

    # Dot-prefix hides from media scanners (Radarr/Sonarr) to avoid them
    # indexing the temp file and marking the movie as deleted.
    staging = path.parent / f".{path.stem}.dedup.tmp.mkv"

    # Build ffmpeg command: map video + selected audio + all subs
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning", "-i", str(path)]

    # Map video
    args.extend(["-map", "0:v:0"])

    # Map only kept audio tracks
    for idx in keep:
        args.extend(["-map", f"0:{idx}"])

    # Map all subtitle tracks
    args.extend(["-map", "0:s?"])

    args.extend(["-c", "copy", str(staging)])

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=3600)
        if result.returncode != 0:
            print(f"  FAILED ffmpeg: {result.stderr.strip()[-200:]}")
            if staging.exists():
                staging.unlink()
            return False

        if not staging.exists() or staging.stat().st_size < 1024:
            print(f"  FAILED: output too small or missing")
            if staging.exists():
                staging.unlink()
            return False

        # Atomic replace
        os.replace(staging, path)
        return True

    except subprocess.TimeoutExpired:
        print(f"  FAILED: ffmpeg timed out")
        if staging.exists():
            staging.unlink()
        return False
    except Exception as exc:
        print(f"  FAILED: {exc}")
        if staging.exists():
            staging.unlink()
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Strip duplicate audio from enriched files")
    parser.add_argument("--execute", action="store_true", help="Actually strip (default is dry-run)")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    args = parser.parse_args()

    media_root = Path("/data")
    if not media_root.exists():
        media_root = Path("/mnt/pool")

    scratch = Path("/mnt/scratch")
    if not scratch.exists():
        scratch = Path("/tmp")

    print("Scanning for files with duplicate audio tracks...")
    targets: List[Tuple[Path, int]] = []  # (path, num_dupes)

    for subdir in ["movies", "tv"]:
        root = media_root / subdir
        if not root.exists():
            continue
        for mkv in root.rglob("*.mkv"):
            _, drop = find_duplicates(mkv)
            if drop:
                targets.append((mkv, len(drop)))

    if not targets:
        print("No files with duplicate audio tracks found.")
        return

    print(f"\nFound {len(targets)} files with duplicate audio tracks:\n")
    for path, count in sorted(targets, key=lambda x: str(x[0])):
        print(f"  [{count} dupes] {path.relative_to(media_root)}")

    if not args.execute:
        total_dupes = sum(c for _, c in targets)
        print(f"\n{len(targets)} files, {total_dupes} duplicate tracks total.")
        print("Re-run with --execute to strip duplicates.")
        return

    print(f"\nStripping duplicates ({args.workers} workers)...\n")
    t0 = time.time()
    ok = 0
    fail = 0

    def process(item: Tuple[Path, int]) -> Tuple[Path, bool]:
        path, _ = item
        return path, strip_duplicates(path)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(process, item): item for item in targets}
        for future in as_completed(futures):
            path, success = future.result()
            if success:
                ok += 1
                print(f"  OK  {path.name}")
            else:
                fail += 1
                print(f"  ERR {path.name}")

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed/60:.1f} minutes. Stripped: {ok}, Failed: {fail}")


if __name__ == "__main__":
    main()
