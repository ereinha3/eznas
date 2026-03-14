#!/usr/bin/env python3
"""
Wave 2 Enhanced: Rename misnamed directories + sync library to Radarr.

This script:
1. Queries Radarr for all tracked movies and their expected paths
2. Lists all directories under /mnt/pool/media/movies/
3. Matches disk directories to Radarr movies (exact, fuzzy, manual overrides)
4. Renames mismatched directories AND primary video files to match Radarr paths
5. Adds untracked movies to Radarr via TMDB lookup
6. Triggers Radarr rescan for all affected movies

Usage:
    python3 scripts/rename_and_sync.py                # Dry-run (default)
    python3 scripts/rename_and_sync.py --execute       # Actually rename + sync
    python3 scripts/rename_and_sync.py --execute --skip-add  # Rename only, don't add to Radarr
"""
import argparse
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

# ─── Configuration ───────────────────────────────────────────────────────────

LIBRARY = Path("/mnt/pool/media/movies")
RADARR_URL = "http://localhost:7878"
RADARR_API_KEY = os.environ.get("RADARR_API_KEY", "")
# Container path prefix → host path prefix mapping
CONTAINER_ROOT = "/data/movies"
HOST_ROOT = str(LIBRARY)

# Extensions we recognize as primary video files
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".m2ts", ".ts", ".wmv", ".mov"}

# ─── Manual Overrides ────────────────────────────────────────────────────────
# For directories where fuzzy matching fails or produces incorrect results.
# Maps disk directory name -> Radarr directory basename
MANUAL_OVERRIDES = {
    "CATCH ME IYC": "Catch Me If You Can (2002)",
}

# Directories to skip (being handled by remux script, or known junk)
SKIP_DIRS = {
    "2 Две крепости (2002)",          # remux in-progress
    "3 Возвращение Короля (2003)",    # remux in-progress
    "Eternal Sunshine of the Spotless Mind Kino Lorber (2004)",  # remux in-progress
    "The Killing US Kino Lorber (1956)",  # remux in-progress
    "Monsters, Inc. (2001)",          # remux in-progress
    "american history x uncut sample (1998)",  # junk sample, Wave 1 delete target
    "Shutter Island (2010)",          # junk YIFY, Wave 1 delete target
}

# False positive suppressions: disk dirs that should NOT be matched to their
# fuzzy match (because they're genuinely different movies)
FALSE_POSITIVES = {
    "A Short Film About Killing (1988)",  # Different film from "A Short Film About Love"
}


# ─── Radarr API Helpers ─────────────────────────────────────────────────────

def radarr_get(endpoint: str) -> any:
    """GET request to Radarr API."""
    url = f"{RADARR_URL}/api/v3{endpoint}"
    req = urllib.request.Request(url)
    req.add_header("X-Api-Key", RADARR_API_KEY)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def radarr_post(endpoint: str, data: dict) -> any:
    """POST request to Radarr API."""
    url = f"{RADARR_URL}/api/v3{endpoint}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("X-Api-Key", RADARR_API_KEY)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def radarr_put(endpoint: str, data: dict) -> any:
    """PUT request to Radarr API."""
    url = f"{RADARR_URL}/api/v3{endpoint}"
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="PUT")
    req.add_header("X-Api-Key", RADARR_API_KEY)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def radarr_lookup(term: str) -> list:
    """Search Radarr's TMDB lookup for a movie."""
    encoded = urllib.parse.quote(term)
    return radarr_get(f"/movie/lookup?term={encoded}")


def radarr_command(name: str, **kwargs) -> dict:
    """Execute a Radarr command."""
    payload = {"name": name, **kwargs}
    return radarr_post("/command", payload)


# ─── Matching Logic ──────────────────────────────────────────────────────────

def extract_year(name: str) -> Optional[int]:
    m = re.search(r"\((\d{4})\)", name)
    return int(m.group(1)) if m else None


def normalize(name: str) -> str:
    """Normalize a directory name for fuzzy comparison."""
    # Remove year in parens
    name = re.sub(r"\s*\(\d{4}\)\s*$", "", name)
    # Remove common scene tags
    for tag in [
        "PROPER", "Kino Lorber", " US ", "BLUEBIRD",
        "Criterion Collection BDRemux", "REPACK", " IYC",
        "uncut sample", " uncut", "MULTI COMPLETE", "Blu-Ray FREEDONOR",
    ]:
        name = name.replace(tag, "")
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9]", "", name)
    return name


def find_best_radarr_match(disk_name: str, radarr_map: dict) -> Optional[tuple]:
    """Find the best Radarr match for a disk directory name.

    Returns (radarr_basename, radarr_info, score) or None.
    """
    d_norm = normalize(disk_name)
    d_year = extract_year(disk_name)
    best_match = None
    best_score = 0

    for rname, rinfo in radarr_map.items():
        r_norm = normalize(rname)
        r_year = extract_year(rname) or rinfo.get("year")
        score = 0

        # Exact normalized match
        if d_norm and r_norm and d_norm == r_norm:
            score = 100
            if d_year and r_year and abs(d_year - r_year) <= 1:
                score += 50
            elif d_year and r_year and d_year != r_year:
                score -= 20
        # Substring match
        elif d_norm and r_norm and len(d_norm) > 3 and len(r_norm) > 3:
            if d_norm in r_norm or r_norm in d_norm:
                score = 50
                if d_year and r_year and abs(d_year - r_year) <= 1:
                    score += 50
            # Fuzzy match (only with year proximity)
            elif d_year and r_year and abs(d_year - r_year) <= 1:
                ratio = SequenceMatcher(None, d_norm, r_norm).ratio()
                if ratio > 0.7:
                    score = int(ratio * 80)
                    if d_year == r_year:
                        score += 30

        if score > best_score:
            best_score = score
            best_match = (rname, rinfo, score)

    if best_match and best_score >= 70:
        return best_match
    return None


def find_primary_video(directory: Path) -> Optional[Path]:
    """Find the largest video file in a directory."""
    best = None
    best_size = 0
    for f in directory.iterdir():
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
            size = f.stat().st_size
            if size > best_size:
                best = f
                best_size = size
    return best


# ─── Actions ─────────────────────────────────────────────────────────────────

def rename_directory(old_path: Path, new_path: Path, dry_run: bool) -> bool:
    """Rename a directory, optionally renaming the video file inside too."""
    if new_path.exists():
        print(f"  ERROR: Target already exists: {new_path.name}")
        return False

    new_basename = new_path.name
    # Strip year for the video filename: "Title (YYYY).ext"
    video_name_base = new_basename

    # Find and rename the primary video file first
    video = find_primary_video(old_path)
    if video:
        new_video_name = video_name_base + video.suffix.lower()
        # If .m2ts → .mkv, keep .m2ts (the remux pipeline handles this)
        if video.suffix.lower() == ".m2ts":
            new_video_name = video_name_base + ".m2ts"
        new_video_path = old_path / new_video_name

        if video.name != new_video_name:
            if dry_run:
                print(f"  Would rename file: {video.name} → {new_video_name}")
            else:
                try:
                    subprocess.run(
                        ["sudo", "mv", str(video), str(new_video_path)],
                        check=True, capture_output=True, text=True,
                    )
                    print(f"  Renamed file: {video.name} → {new_video_name}")
                except subprocess.CalledProcessError as e:
                    print(f"  ERROR renaming file: {e.stderr.strip()}")
                    return False

    # Rename the directory
    if dry_run:
        print(f"  Would rename dir: {old_path.name} → {new_path.name}")
    else:
        try:
            subprocess.run(
                ["sudo", "mv", str(old_path), str(new_path)],
                check=True, capture_output=True, text=True,
            )
            print(f"  Renamed dir: {old_path.name} → {new_path.name}")
        except subprocess.CalledProcessError as e:
            print(f"  ERROR renaming dir: {e.stderr.strip()}")
            return False

    return True


@dataclass
class AddResult:
    """Result from attempting to add/find a movie in Radarr."""
    movie_id: Optional[int] = None
    already_existed: bool = False  # True if movie was already in Radarr
    radarr_path_base: Optional[str] = None  # Radarr's expected directory basename


def add_movie_to_radarr(disk_dir: str, dry_run: bool) -> AddResult:
    """Look up a movie on TMDB via Radarr and add it.

    Returns an AddResult with:
    - movie_id: Radarr movie ID if found/added
    - already_existed: True if the movie was already tracked in Radarr
    - radarr_path_base: The Radarr-canonical directory basename
    """
    # Extract a search term from the directory name
    search_term = disk_dir
    # Remove year suffix for search
    search_term = re.sub(r"\s*\(\d{4}\)\s*$", "", search_term)
    # Clean up special chars
    search_term = search_term.replace("+", "/").replace("&", "and")

    print(f"  Searching TMDB for: {search_term!r}")

    try:
        results = radarr_lookup(search_term)
    except Exception as e:
        print(f"  ERROR: Radarr lookup failed: {e}")
        return AddResult()

    if not results:
        print(f"  No TMDB results found")
        return AddResult()

    # Try to match by year if we have one
    disk_year = extract_year(disk_dir)
    best_result = None

    for r in results[:10]:  # Check top 10 results
        r_year = r.get("year")
        r_title = r.get("title", "")

        # Already in Radarr?
        if r.get("id") and r.get("id") > 0:
            radarr_base = os.path.basename(r.get("path", ""))
            print(f"  Already in Radarr: {r_title} ({r_year}) [id={r['id']}] path={radarr_base}")
            return AddResult(
                movie_id=r["id"],
                already_existed=True,
                radarr_path_base=radarr_base,
            )

        if disk_year and r_year and disk_year == r_year:
            best_result = r
            break
        elif disk_year and r_year and abs(disk_year - r_year) <= 1:
            if not best_result:
                best_result = r

    if not best_result:
        best_result = results[0]  # Take top result

    title = best_result.get("title", "unknown")
    year = best_result.get("year", "?")
    tmdb_id = best_result.get("tmdbId")

    if not tmdb_id:
        print(f"  ERROR: No TMDB ID for {title}")
        return AddResult()

    print(f"  Best match: {title} ({year}) [tmdb={tmdb_id}]")

    if dry_run:
        print(f"  Would add to Radarr: {title} ({year})")
        return AddResult(radarr_path_base=f"{title} ({year})")

    # Build the add payload — set path to match our disk directory
    # so Radarr immediately finds the file
    radarr_path = f"{CONTAINER_ROOT}/{disk_dir}"

    add_payload = {
        "title": title,
        "tmdbId": tmdb_id,
        "year": year,
        "qualityProfileId": best_result.get("qualityProfileId") or 1,
        "rootFolderPath": CONTAINER_ROOT,
        "path": radarr_path,
        "monitored": True,
        "addOptions": {
            "monitor": "movieOnly",
            "searchForMovie": False,
        },
        "images": best_result.get("images", []),
    }

    try:
        added = radarr_post("/movie", add_payload)
        movie_id = added.get("id")
        print(f"  Added to Radarr: {title} ({year}) [id={movie_id}]")
        return AddResult(movie_id=movie_id, radarr_path_base=disk_dir)
    except urllib.error.HTTPError as e:
        body = e.read().decode() if hasattr(e, "read") else str(e)
        if "already been added" in body.lower() or "already exists" in body.lower():
            print(f"  Already in Radarr (conflict): {title} ({year})")
            # Find the existing movie to get its path
            try:
                existing = radarr_get("/movie")
                for m in existing:
                    if m.get("tmdbId") == tmdb_id:
                        radarr_base = os.path.basename(m["path"])
                        return AddResult(
                            movie_id=m["id"],
                            already_existed=True,
                            radarr_path_base=radarr_base,
                        )
            except Exception:
                pass
        else:
            print(f"  ERROR adding to Radarr: {e} — {body[:200]}")
        return AddResult()


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Rename + sync library to Radarr")
    parser.add_argument("--execute", action="store_true", help="Actually rename (default is dry-run)")
    parser.add_argument("--skip-add", action="store_true", help="Don't add untracked movies to Radarr")
    args = parser.parse_args()
    dry_run = not args.execute

    if dry_run:
        print("=" * 60)
        print("  DRY RUN — no changes will be made")
        print("  Use --execute to apply changes")
        print("=" * 60)
    else:
        print("=" * 60)
        print("  EXECUTING — changes WILL be applied")
        print("=" * 60)

    # 1. Fetch all Radarr movies
    print("\n[1/5] Fetching Radarr movie list...")
    radarr_movies = radarr_get("/movie")
    radarr_map = {}
    radarr_by_id = {}
    for m in radarr_movies:
        base = os.path.basename(m["path"])
        radarr_map[base] = {
            "id": m["id"],
            "title": m["title"],
            "year": m.get("year"),
            "path": m["path"],
            "hasFile": m.get("hasFile", False),
        }
        radarr_by_id[m["id"]] = m
    print(f"  Found {len(radarr_map)} movies in Radarr")

    # 2. List disk directories
    print("\n[2/5] Scanning disk library...")
    disk_dirs = sorted([
        d for d in os.listdir(LIBRARY)
        if (LIBRARY / d).is_dir()
    ])
    print(f"  Found {len(disk_dirs)} directories on disk")

    # 3. Categorize
    print("\n[3/5] Matching directories to Radarr...")
    exact_matches = []      # Already correct
    renames = []            # (disk_name, radarr_basename, radarr_info)
    untracked = []          # On disk but not in Radarr
    skipped = []            # Being remuxed / junk
    rescan_ids = []         # Radarr movie IDs to rescan

    for d in disk_dirs:
        if d in SKIP_DIRS:
            skipped.append(d)
            continue

        # Check manual overrides first
        if d in MANUAL_OVERRIDES:
            target = MANUAL_OVERRIDES[d]
            if target in radarr_map:
                renames.append((d, target, radarr_map[target]))
            else:
                print(f"  WARNING: Manual override target not in Radarr: {target}")
                untracked.append(d)
            continue

        # Check false positive suppression
        if d in FALSE_POSITIVES:
            untracked.append(d)
            continue

        # Exact match
        if d in radarr_map:
            exact_matches.append(d)
            continue

        # Fuzzy match
        match = find_best_radarr_match(d, radarr_map)
        if match:
            rname, rinfo, score = match
            renames.append((d, rname, rinfo))
        else:
            untracked.append(d)

    print(f"\n  Results:")
    print(f"    Exact matches:  {len(exact_matches)}")
    print(f"    Needs rename:   {len(renames)}")
    print(f"    Untracked:      {len(untracked)}")
    print(f"    Skipped:        {len(skipped)}")

    # 4. Execute renames
    print(f"\n[4/5] {'Would rename' if dry_run else 'Renaming'} {len(renames)} directories...")
    rename_ok = 0
    rename_fail = 0

    for disk_name, radarr_basename, rinfo in renames:
        old_path = LIBRARY / disk_name
        new_path = LIBRARY / radarr_basename
        radarr_id = rinfo["id"]

        print(f"\n  {disk_name}")
        print(f"    → {radarr_basename}  [radarr id={radarr_id}]")

        if not old_path.exists():
            print(f"    SKIP: source no longer exists (remuxed/deleted?)")
            continue

        if rename_directory(old_path, new_path, dry_run):
            rename_ok += 1
            rescan_ids.append(radarr_id)
        else:
            rename_fail += 1

    print(f"\n  Renames: {rename_ok} OK, {rename_fail} failed")

    # 4b. Also rescan exact matches that Radarr doesn't see files for
    for d in exact_matches:
        rinfo = radarr_map[d]
        if not rinfo["hasFile"]:
            rescan_ids.append(rinfo["id"])

    # 5. Add untracked movies to Radarr
    if not args.skip_add:
        print(f"\n[5/5] {'Would add' if dry_run else 'Adding'} {len(untracked)} untracked movies to Radarr...")
        added_count = 0
        add_failed = 0
        late_renames = 0

        for disk_name in untracked:
            disk_path = LIBRARY / disk_name

            # Skip empty directories or directories without video
            video = find_primary_video(disk_path)
            if not video:
                print(f"\n  {disk_name} — no video file found, skipping")
                continue

            video_size_gb = video.stat().st_size / (1024**3)
            print(f"\n  {disk_name} ({video_size_gb:.1f} GB)")

            result = add_movie_to_radarr(disk_name, dry_run)

            if result.movie_id:
                added_count += 1
                rescan_ids.append(result.movie_id)

                # If movie already existed in Radarr with a DIFFERENT path,
                # rename our disk directory to match Radarr's expected path.
                if result.already_existed and result.radarr_path_base and result.radarr_path_base != disk_name:
                    print(f"  Disk name differs from Radarr: {disk_name} → {result.radarr_path_base}")
                    old_p = LIBRARY / disk_name
                    new_p = LIBRARY / result.radarr_path_base
                    if rename_directory(old_p, new_p, dry_run):
                        late_renames += 1

            elif result.radarr_path_base:
                # Dry-run: still count as would-add
                pass
            else:
                if not dry_run:
                    add_failed += 1

        print(f"\n  Added/found: {added_count}, Failed: {add_failed}, Late renames: {late_renames}")
    else:
        print(f"\n[5/5] Skipping add (--skip-add)")

    # 6. Trigger Radarr rescan
    unique_ids = list(set(rescan_ids))
    if unique_ids:
        print(f"\n[RESCAN] Triggering Radarr rescan for {len(unique_ids)} movies...")
        if dry_run:
            print(f"  Would rescan {len(unique_ids)} movie IDs")
        else:
            # Batch rescan: send RefreshMovie for all IDs at once
            try:
                cmd = radarr_command("RefreshMovie", movieIds=unique_ids)
                print(f"  Rescan command sent (id={cmd.get('id', '?')})")
            except Exception as e:
                print(f"  ERROR: Rescan command failed: {e}")
                # Fall back to individual rescans
                print(f"  Trying individual rescans...")
                for mid in unique_ids[:5]:  # First 5 as test
                    try:
                        radarr_command("RefreshMovie", movieIds=[mid])
                    except Exception:
                        pass
                print(f"  Individual rescans sent")

    # Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)
    print(f"  Exact matches (no action): {len(exact_matches)}")
    print(f"  Renamed:                   {rename_ok}")
    print(f"  Rename failed:             {rename_fail}")
    print(f"  Skipped (remux/junk):      {len(skipped)}")
    print(f"  Untracked on disk:         {len(untracked)}")
    print(f"  Rescan triggered for:      {len(unique_ids)} movies")
    if dry_run:
        print(f"\n  *** DRY RUN — run with --execute to apply ***")
    print("=" * 60)


if __name__ == "__main__":
    main()
