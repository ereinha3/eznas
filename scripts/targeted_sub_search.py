#!/usr/bin/env python3
"""Targeted subtitle search — slow-drip approach for critical missing subs.

Bazarr's bulk search (2,690+ items) exhausts provider rate limits before
reaching niche anime titles.  This script searches ONLY the critical files
(non-English audio with no English subs at all) one at a time, with 30-second
spacing to stay under rate limits.

Usage:
    python3 scripts/targeted_sub_search.py                  # dry-run
    python3 scripts/targeted_sub_search.py --execute        # actually search
    python3 scripts/targeted_sub_search.py --execute --delay 45  # custom delay
"""
import argparse
import json
import sys
import time
import urllib.request
import urllib.error

BAZARR_KEY = "b19320c3dd27f2025bc759921af353e7"
BAZARR_URL = "http://localhost:6767"
TARGET_FILE = "/tmp/targeted_sub_search.json"

DEFAULT_DELAY = 30  # seconds between requests


def load_targets(path: str) -> list:
    with open(path) as f:
        return json.load(f)


def search_episode(series_id: int, episode_id: int) -> int:
    """Trigger a subtitle search for a single episode. Returns HTTP status."""
    url = (
        f"{BAZARR_URL}/api/episodes/subtitles"
        f"?seriesid={series_id}&episodeid={episode_id}"
        f"&language=eng&forced=False&hi=False"
    )
    req = urllib.request.Request(url, method="PATCH")
    req.add_header("x-api-key", BAZARR_KEY)
    try:
        resp = urllib.request.urlopen(req, timeout=60)
        return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


def check_srt_exists(target: dict) -> bool:
    """Check if an .srt file now exists for this episode (quick heuristic)."""
    # This is a best-effort check — Bazarr may not have saved the file yet
    # when we check immediately after the search.
    return False  # We'll do a bulk check at the end instead


def main():
    parser = argparse.ArgumentParser(description="Targeted subtitle search")
    parser.add_argument("--execute", action="store_true", help="Actually trigger searches")
    parser.add_argument("--delay", type=int, default=DEFAULT_DELAY, help="Seconds between requests")
    parser.add_argument("--target-file", default=TARGET_FILE, help="JSON file with search targets")
    args = parser.parse_args()

    targets = load_targets(args.target_file)
    if not targets:
        print("No targets found")
        return

    print(f"Targets: {len(targets)} episodes")
    print(f"Delay: {args.delay}s between requests")
    print(f"Estimated runtime: {len(targets) * args.delay / 60:.0f} minutes")
    print()

    if not args.execute:
        print("DRY RUN — pass --execute to actually search")
        from collections import Counter
        shows = Counter(t["show"] for t in targets)
        for show, count in shows.most_common():
            print(f"  {show}: {count} episodes")
        return

    # Group by show for cleaner output
    current_show = None
    searched = 0
    errors = 0

    for i, target in enumerate(targets):
        show = target["show"]
        if show != current_show:
            current_show = show
            print(f"\n=== {show} ===")

        sid = target["seriesId"]
        eid = target["episodeId"]
        filename = target.get("file", "?")

        status = search_episode(sid, eid)
        searched += 1

        if status == 204:
            print(f"  [{i+1}/{len(targets)}] ✓ {filename[:60]}")
        else:
            print(f"  [{i+1}/{len(targets)}] ✗ {filename[:60]} (HTTP {status})")
            errors += 1

        # Wait between requests to avoid rate limits
        if i < len(targets) - 1:
            time.sleep(args.delay)

    print(f"\n=== Done ===")
    print(f"Searched: {searched}, Errors: {errors}")
    print(f"Check Bazarr logs and re-run the subtitle audit to see results.")


if __name__ == "__main__":
    main()
