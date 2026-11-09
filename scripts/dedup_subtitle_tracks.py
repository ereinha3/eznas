#!/usr/bin/env python3
"""Detect and repair duplicate subtitle tracks in library MKV files.

The SRT-embed sweep had a thread-leak bug (fixed in runner.py) that ran
multiple concurrent sweep threads. Each thread embedded the same orphan
.srt file into the same MKV, producing identical-language SubRip tracks.

Run with no args to get a damage report. Run with --fix to repair the
files in-place via mkvmerge (drops every duplicate after the first
occurrence per (codec, language, track_name, forced, hi) key).

Usage:
    docker exec pipeline-worker python3 /config/scripts/dedup_subtitle_tracks.py
    docker exec pipeline-worker python3 /config/scripts/dedup_subtitle_tracks.py --fix
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

VIDEO_EXTS = {".mkv"}
LIBRARY_ROOT = Path("/data")


def probe(path: Path):
    try:
        result = subprocess.run(
            ["mkvmerge", "-J", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode not in (0, 1):
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
        return None


def subtitle_signature(track: dict) -> Tuple:
    """Identifying key for a subtitle track. Duplicates share this key."""
    props = track.get("properties", {}) or {}
    return (
        track.get("codec"),
        props.get("language"),
        props.get("track_name", ""),
        bool(props.get("forced_track", False)),
        bool(props.get("flag_hearing_impaired", False)),
    )


def find_duplicates(tracks: List[dict]) -> List[int]:
    """Return mkvmerge track-IDs (the global ID, not subtitle index) to drop."""
    seen = {}
    drop_ids: List[int] = []
    for t in tracks:
        if t.get("type") != "subtitles":
            continue
        sig = subtitle_signature(t)
        if sig in seen:
            drop_ids.append(t["id"])
        else:
            seen[sig] = t["id"]
    return drop_ids


def repair(path: Path, drop_ids: List[int]) -> bool:
    """Rewrite path via mkvmerge, excluding the duplicate subtitle tracks."""
    if not drop_ids:
        return True
    output = path.parent / f".{path.stem}.dedup.tmp.mkv"

    info = probe(path)
    if info is None:
        return False
    keep_ids: List[str] = []
    for t in info.get("tracks", []):
        if t.get("type") != "subtitles":
            continue
        if t["id"] not in drop_ids:
            keep_ids.append(str(t["id"]))

    if not keep_ids:
        args = ["mkvmerge", "--quiet", "-o", str(output), "--no-subtitles", str(path)]
    else:
        args = [
            "mkvmerge", "--quiet", "-o", str(output),
            "--subtitle-tracks", ",".join(keep_ids), str(path),
        ]

    size = path.stat().st_size
    timeout_s = max(1800, int(size / (50 * 1024 * 1024)))

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        if output.exists():
            output.unlink()
        return False

    if result.returncode not in (0, 1):
        if output.exists():
            output.unlink()
        err = (result.stderr.strip() or result.stdout.strip())[-200:]
        print(f"  mkvmerge failed (rc={result.returncode}): {err}", file=sys.stderr)
        return False

    if not output.exists() or output.stat().st_size < 1024:
        if output.exists():
            output.unlink()
        return False

    try:
        os.replace(output, path)
    except OSError:
        shutil.copy2(output, path)
        output.unlink()
    return True


def walk(root: Path):
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() in VIDEO_EXTS:
                yield Path(dirpath) / name


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="Repair affected files in-place")
    parser.add_argument("--root", default=str(LIBRARY_ROOT), help="Library root to scan")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N affected files (0=unlimited)")
    parser.add_argument("--max-size-gb", type=float, default=0.0,
                        help="In --fix mode, skip files larger than this many GB (0=no limit)")
    parser.add_argument("--only", default=None, help="Only process files whose path contains this substring")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"error: root {root} does not exist", file=sys.stderr)
        return 1

    mode = "REPAIR" if args.fix else "REPORT"
    print(f"[{mode}] scanning {root}\n")

    total_scanned = 0
    affected: List[Tuple[Path, int]] = []
    skipped_too_big = 0
    fixed = 0
    failed = 0
    start = time.time()

    max_bytes = int(args.max_size_gb * 1024 * 1024 * 1024) if args.max_size_gb > 0 else 0

    for path in walk(root):
        total_scanned += 1
        if total_scanned % 500 == 0:
            elapsed = time.time() - start
            print(f"  [{elapsed:5.0f}s] scanned {total_scanned} files, {len(affected)} affected", flush=True)

        if args.only and args.only not in str(path):
            continue

        info = probe(path)
        if info is None:
            continue
        drop_ids = find_duplicates(info.get("tracks", []))
        if not drop_ids:
            continue

        affected.append((path, len(drop_ids)))

        if args.fix:
            rel = str(path).replace(str(root) + "/", "")
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            if max_bytes and size > max_bytes:
                skipped_too_big += 1
                print(f"  skipped (size={size/1e9:.1f}GB > {args.max_size_gb}GB): {rel}", flush=True)
                continue
            t0 = time.time()
            ok = repair(path, drop_ids)
            dt = time.time() - t0
            if ok:
                fixed += 1
                print(f"  fixed (-{len(drop_ids)} tracks, {dt:.0f}s, {size/1e9:.1f}GB): {rel}", flush=True)
            else:
                failed += 1
                print(f"  FAILED: {rel}", flush=True)

        if args.limit and len(affected) >= args.limit:
            print(f"  reached --limit {args.limit}, stopping")
            break

    elapsed = time.time() - start
    print()
    print(f"=== {mode} SUMMARY ===")
    print(f"  files scanned:    {total_scanned}")
    print(f"  files affected:   {len(affected)}")
    print(f"  duplicate tracks: {sum(n for _, n in affected)}")
    print(f"  elapsed:          {elapsed:.0f}s")
    if args.fix:
        print(f"  fixed:            {fixed}")
        print(f"  failed:           {failed}")
        if skipped_too_big:
            print(f"  skipped (size):   {skipped_too_big}")
    else:
        if affected:
            print()
            print("  top 20 worst offenders (extra-track count):")
            for path, n in sorted(affected, key=lambda x: -x[1])[:20]:
                rel = str(path).replace(str(root) + "/", "")
                print(f"    +{n}  {rel}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
