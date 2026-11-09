#!/usr/bin/env python3
"""Bulk-embed all orphaned .srt subtitle files into their parent MKVs.

Designed to run inside the pipeline-worker container to clear the SRT
backlog in one shot.  Uses the same embed logic as the pipeline's Phase 8
daemon, but with higher parallelism and progress reporting.

Usage (from host):
    # Dry-run — scan only, show what would be embedded:
    docker exec pipeline-worker python3 /app/scripts/bulk_srt_embed.py

    # Execute — embed all SRTs in parallel:
    docker exec pipeline-worker python3 /app/scripts/bulk_srt_embed.py --execute

    # Custom parallelism (default 8):
    docker exec pipeline-worker python3 /app/scripts/bulk_srt_embed.py --execute --workers 12
"""
import argparse
import os
import sys
import time
from pathlib import Path

# Ensure the orchestrator package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Bulk-embed orphaned SRT files into MKVs")
    parser.add_argument("--execute", action="store_true", help="Actually embed (default is dry-run)")
    parser.add_argument("--workers", type=int, default=8, help="Parallel ffmpeg workers (default: 8)")
    args = parser.parse_args()

    from orchestrator.storage import ConfigRepository

    root = Path(os.getenv("ORCH_ROOT", str(Path(__file__).resolve().parents[1])))
    repo = ConfigRepository(root, read_only=True)
    config = repo.load_stack()

    from orchestrator.pipeline.sweep import LibrarySweeper

    sweeper = LibrarySweeper(config, repo)

    if not args.execute:
        print("=" * 60)
        print("DRY RUN — scanning for orphaned SRT files")
        print("=" * 60)
        sweeper.embed_orphaned_srt(dry_run=True)
        print()
        print("Re-run with --execute to embed all SRTs.")
        return

    print("=" * 60)
    print(f"EMBEDDING SRT FILES ({args.workers} workers)")
    print("=" * 60)

    t0 = time.time()
    count = sweeper.embed_orphaned_srt(dry_run=False, max_workers=args.workers)
    elapsed = time.time() - t0

    print()
    print("=" * 60)
    print(f"Done. Embedded {count} subtitle file(s) in {elapsed/60:.1f} minutes.")
    print("=" * 60)


if __name__ == "__main__":
    main()
