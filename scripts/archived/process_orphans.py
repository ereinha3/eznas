#!/usr/bin/env python3
"""One-shot script to process orphan files on scratch that qBittorrent lost track of.

These are fully downloaded movies/shows sitting in /mnt/scratch/complete/ that
were successfully downloaded but never processed because:
1. qBittorrent's container was recreated and lost its torrent database
2. The pipeline worker couldn't reach qBT (VPN hostname issue)
3. Scratch was full so the old staging-based pipeline couldn't run

This script feeds each orphan directly to PipelineWorker.build_plans() +
ffmpeg, bypassing qBittorrent entirely. After successful remux, the source
files are deleted to reclaim scratch space.

Usage:
    # Dry run (show what would be processed):
    python scripts/process_orphans.py --dry-run

    # Process all orphans:
    python scripts/process_orphans.py

    # Process a single orphan:
    python scripts/process_orphans.py --name "Friday.1995.DC.1080p.BluRay.Remux.TrueHD.7.1"
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.models import StackConfig
from orchestrator.storage import ConfigRepository
from orchestrator.pipeline.worker import PipelineWorker, TorrentInfo
from orchestrator.pipeline.runner import PipelineRunner


def find_orphans(scratch_complete: Path, qbt_names: set[str]) -> list[Path]:
    """Find files/dirs in scratch that qBittorrent doesn't track."""
    orphans = []
    if not scratch_complete.exists():
        return orphans
    for item in scratch_complete.iterdir():
        if item.name not in qbt_names:
            orphans.append(item)
    return sorted(orphans, key=lambda p: _dir_size(p))


def _dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


def _collect_files(path: Path) -> list[Path]:
    """Collect all files under a path (or the path itself if it's a file)."""
    if path.is_file():
        return [path]
    return sorted(path.rglob("*"), key=lambda p: p.stat().st_size if p.is_file() else 0, reverse=True)


def process_orphan(
    orphan_path: Path,
    config: StackConfig,
    repo: ConfigRepository,
    category: str,
    dry_run: bool = False,
) -> bool:
    """Process a single orphan through the pipeline.

    Returns True if successful.
    """
    name = orphan_path.name
    size_gb = _dir_size(orphan_path) / (1024**3)
    print(f"\n{'='*60}")
    print(f"Processing: {name} ({size_gb:.1f} GB)")
    print(f"Category: {category}")
    print(f"{'='*60}")

    # Build a fake TorrentInfo — the pipeline worker just needs the
    # file list and metadata, not an actual qBT torrent.
    files = _collect_files(orphan_path)
    if not files:
        print(f"  SKIP: no files found in {orphan_path}")
        return False

    # Use the parent as download_path (e.g. /mnt/scratch/complete)
    info = TorrentInfo(
        hash=f"orphan_{name[:16]}",
        name=name,
        category=category,
        download_path=orphan_path.parent,
        files=files,
    )

    worker = PipelineWorker(config)

    # Look up metadata from Radarr/Sonarr
    runner = PipelineRunner(repo)
    from orchestrator.pipeline.runner import TorrentRecord
    fake_record = TorrentRecord(
        hash=info.hash,
        name=name,
        category=category,
        save_path=orphan_path.parent,
        content_path=orphan_path,
    )
    metadata = runner._lookup_arr_metadata(config, fake_record)

    # Check for ISO files — mount if found
    iso_dir = None
    iso_file = runner._find_iso_file(files)
    if iso_file:
        print(f"  ISO detected: {iso_file.name}")
        try:
            iso_dir = runner._open_iso(iso_file, info.hash)
        except (RuntimeError, OSError) as exc:
            print(f"  ISO open failed: {exc}")
            return False

    try:
        plans = worker.build_plans(
            info,
            original_language=metadata.original_language if metadata else None,
            library_path=metadata.library_path if metadata else None,
            iso_mount_dir=iso_dir,
        )
    except ValueError as exc:
        print(f"  PLAN FAILED: {exc}")
        if iso_dir:
            runner._close_iso(iso_dir)
        return False

    if not plans:
        print(f"  SKIP: no video files to process")
        if iso_dir:
            runner._close_iso(iso_dir)
        return False

    print(f"  Plans: {len(plans)} file(s) to remux")
    for i, plan in enumerate(plans, 1):
        print(f"    [{i}] {plan.source.name} -> {plan.final_output}")

    if dry_run:
        print("  DRY RUN: skipping ffmpeg and cleanup")
        if iso_dir:
            runner._close_iso(iso_dir)
        return True

    # Execute each plan
    succeeded = 0
    failed = 0
    for i, plan in enumerate(plans, 1):
        print(f"\n  [{i}/{len(plans)}] remuxing: {plan.source.name}")

        # Check destination space
        dest_free = shutil.disk_usage(plan.final_output.parent).free
        source_size = plan.source.stat().st_size if plan.source.exists() else 0
        if source_size > 0 and dest_free < source_size:
            print(f"  SKIP: not enough space on pool ({dest_free/(1024**3):.1f} GB free, need {source_size/(1024**3):.1f} GB)")
            failed += 1
            continue

        # Run ffmpeg
        print(f"  Running: {' '.join(plan.ffmpeg_command[:6])}...")
        timeout = max(7200, int(source_size / (1024**3) / 25 * 3600) + 7200)
        timeout = min(timeout, 8 * 3600)
        try:
            result = subprocess.run(
                plan.ffmpeg_command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            print(f"  FAILED: ffmpeg timed out after {timeout}s")
            if plan.staging_output.exists():
                plan.staging_output.unlink()
            failed += 1
            continue

        if result.returncode != 0:
            print(f"  FAILED: ffmpeg returned {result.returncode}")
            stderr_lines = result.stderr.strip().split("\n")
            for line in stderr_lines[-5:]:
                print(f"    {line}")
            if plan.staging_output.exists():
                plan.staging_output.unlink()
            failed += 1
            continue

        # Validate output exists and is reasonable size
        if not plan.staging_output.exists():
            print(f"  FAILED: output file not created")
            failed += 1
            continue

        output_size = plan.staging_output.stat().st_size
        if output_size < 1024 * 1024:  # < 1MB is suspicious
            print(f"  FAILED: output too small ({output_size} bytes)")
            plan.staging_output.unlink()
            failed += 1
            continue

        # Don't overwrite larger existing file
        plan.final_output.parent.mkdir(parents=True, exist_ok=True)
        if plan.final_output.exists():
            existing_size = plan.final_output.stat().st_size
            if existing_size > output_size:
                print(f"  SKIP: existing file is larger ({existing_size/(1024**3):.2f} > {output_size/(1024**3):.2f} GB)")
                plan.staging_output.unlink()
                failed += 1
                continue

        # Move staging -> final (same filesystem = atomic rename)
        shutil.move(str(plan.staging_output), str(plan.final_output))
        print(f"  OK: {plan.final_output.name} ({output_size/(1024**3):.2f} GB)")
        succeeded += 1

    # Clean up ISO mount if we opened one
    if iso_dir:
        try:
            runner._close_iso(iso_dir)
        except Exception as exc:
            print(f"  Warning: ISO cleanup failed: {exc}")

    print(f"\n  Result: {succeeded}/{len(plans)} succeeded, {failed}/{len(plans)} failed")

    if failed == 0:
        # All succeeded — clean up source
        print(f"  Cleaning up source: {orphan_path}")
        if orphan_path.is_file():
            orphan_path.unlink()
        else:
            shutil.rmtree(orphan_path, ignore_errors=True)
        print(f"  Freed {size_gb:.1f} GB on scratch")
        return True
    elif succeeded > 0:
        print(f"  Partial success — keeping source for failed files")
        return True
    else:
        print(f"  All failed — source preserved")
        return False


def detect_category(name: str, config: StackConfig) -> str:
    """Guess category from torrent name."""
    name_lower = name.lower()
    # TV patterns
    if any(p in name_lower for p in [".s0", ".s1", ".s2", "season", " s0", " s1"]):
        return config.download_policy.categories.sonarr
    return config.download_policy.categories.radarr


def main():
    parser = argparse.ArgumentParser(description="Process orphan downloads on scratch")
    parser.add_argument("--dry-run", action="store_true", help="Show plans without executing")
    parser.add_argument("--name", type=str, help="Process only this specific orphan")
    parser.add_argument("--scratch", type=str, default="/mnt/scratch/complete",
                        help="Path to scratch complete directory")
    args = parser.parse_args()

    # Load config
    config_root = Path(__file__).resolve().parent.parent
    repo = ConfigRepository(config_root)
    config = repo.load_stack()

    scratch_complete = Path(args.scratch)
    if not scratch_complete.exists():
        print(f"Scratch path does not exist: {scratch_complete}")
        sys.exit(1)

    # We don't query qBT here — just process whatever is in the directory.
    # The caller can filter with --name if needed.
    orphans = sorted(scratch_complete.iterdir(), key=lambda p: _dir_size(p))

    if args.name:
        orphans = [o for o in orphans if o.name == args.name]
        if not orphans:
            print(f"Orphan not found: {args.name}")
            sys.exit(1)

    if not orphans:
        print("No orphan files found.")
        return

    print(f"Found {len(orphans)} item(s) to process:")
    total_size = 0
    for o in orphans:
        size = _dir_size(o)
        total_size += size
        cat = detect_category(o.name, config)
        print(f"  {size/(1024**3):6.1f} GB  [{cat}]  {o.name}")
    print(f"  Total: {total_size/(1024**3):.1f} GB")

    if args.dry_run:
        print("\n--- DRY RUN ---")

    succeeded = 0
    failed = 0
    for orphan in orphans:
        category = detect_category(orphan.name, config)
        ok = process_orphan(orphan, config, repo, category, dry_run=args.dry_run)
        if ok:
            succeeded += 1
        else:
            failed += 1

    print(f"\n{'='*60}")
    print(f"Done: {succeeded} succeeded, {failed} failed")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
