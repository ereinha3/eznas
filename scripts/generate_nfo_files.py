#!/usr/bin/env python3
"""Generate NFO files for all existing media in the library.

Queries Sonarr and Radarr APIs for TVDB/TMDB/IMDB IDs, then writes
tvshow.nfo and movie.nfo files so Jellyfin can correctly identify
each series/movie without relying on folder name matching.

Usage:
    python scripts/generate_nfo_files.py                    # dry run
    python scripts/generate_nfo_files.py --execute          # write files
    python scripts/generate_nfo_files.py --execute --force  # overwrite existing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx
import yaml

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.pipeline.nfo import write_tvshow_nfo, write_movie_nfo


def load_config():
    """Load stack config and secrets."""
    root = Path(__file__).resolve().parent.parent
    stack = yaml.safe_load((root / "stack.yaml").read_text())
    secrets = {}
    secrets_dir = root / "generated" / ".secrets"
    for f in secrets_dir.glob("*.env"):
        service = f.stem
        secrets[service] = {}
        for line in f.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                secrets[service][k] = v
    return stack, secrets


def get_arr_host(service_name: str, stack: dict) -> str:
    """Determine the correct hostname for an arr service.

    When running outside Docker (host), use localhost with the exposed port.
    The ports are mapped through Gluetun to localhost.
    """
    return "localhost"


def generate_tv_nfos(
    execute: bool = False,
    force: bool = False,
    media_root: Path = Path("/mnt/pool/media"),
) -> int:
    """Generate tvshow.nfo for all Sonarr series."""
    stack, secrets = load_config()
    api_key = secrets.get("sonarr", {}).get("SONARR_API_KEY", "")
    if not api_key:
        print("No Sonarr API key found")
        return 0

    host = get_arr_host("sonarr", stack)
    print(f"Querying Sonarr at {host}:8989...")

    resp = httpx.get(
        f"http://{host}:8989/api/v3/series",
        headers={"X-Api-Key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    series_list = resp.json()
    print(f"Found {len(series_list)} series in Sonarr")

    written = 0
    skipped = 0

    for series in series_list:
        title = series.get("title", "?")
        tvdb_id = series.get("tvdbId")
        imdb_id = series.get("imdbId")
        year = series.get("year")

        # Sonarr path is container path (/data/tv/...), map to host path
        sonarr_path = series.get("path", "")
        # /data/tv/Hellsing Ultimate → /mnt/pool/media/tv/Hellsing Ultimate
        series_dir = media_root / "tv" / Path(sonarr_path).name

        if not series_dir.exists():
            print(f"  SKIP {title}: directory not found at {series_dir}")
            skipped += 1
            continue

        nfo_exists = (series_dir / "tvshow.nfo").exists()
        if nfo_exists and not force:
            skipped += 1
            continue

        # Get TMDB ID if available (Sonarr may not have it)
        tmdb_id = None

        action = "OVERWRITE" if nfo_exists else "CREATE"
        print(f"  {action} {title} (tvdb={tvdb_id}, imdb={imdb_id})")

        if execute:
            ok = write_tvshow_nfo(
                series_dir,
                title=title,
                tvdb_id=tvdb_id,
                imdb_id=imdb_id,
                year=year,
                overwrite=force,
            )
            if ok:
                written += 1
        else:
            written += 1  # Count what would be written

    return written


def generate_movie_nfos(
    execute: bool = False,
    force: bool = False,
    media_root: Path = Path("/mnt/pool/media"),
) -> int:
    """Generate movie.nfo for all Radarr movies."""
    stack, secrets = load_config()
    api_key = secrets.get("radarr", {}).get("RADARR_API_KEY", "")
    if not api_key:
        print("No Radarr API key found")
        return 0

    host = get_arr_host("radarr", stack)
    print(f"Querying Radarr at {host}:7878...")

    resp = httpx.get(
        f"http://{host}:7878/api/v3/movie",
        headers={"X-Api-Key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    movie_list = resp.json()
    print(f"Found {len(movie_list)} movies in Radarr")

    written = 0
    skipped = 0

    for movie in movie_list:
        title = movie.get("title", "?")
        tmdb_id = movie.get("tmdbId")
        imdb_id = movie.get("imdbId")
        year = movie.get("year")

        # Radarr path: /data/movies/Hereditary (2018) → /mnt/pool/media/movies/Hereditary (2018)
        radarr_path = movie.get("path", "")
        movie_dir = media_root / "movies" / Path(radarr_path).name

        if not movie_dir.exists():
            skipped += 1
            continue

        nfo_exists = (movie_dir / "movie.nfo").exists()
        if nfo_exists and not force:
            skipped += 1
            continue

        action = "OVERWRITE" if nfo_exists else "CREATE"
        print(f"  {action} {title} (tmdb={tmdb_id}, imdb={imdb_id})")

        if execute:
            ok = write_movie_nfo(
                movie_dir,
                title=title,
                tmdb_id=tmdb_id,
                imdb_id=imdb_id,
                year=year,
                overwrite=force,
            )
            if ok:
                written += 1
        else:
            written += 1

    return written


def main():
    parser = argparse.ArgumentParser(description="Generate NFO files for Jellyfin")
    parser.add_argument("--execute", action="store_true", help="Actually write files (default: dry run)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing NFO files")
    args = parser.parse_args()

    if not args.execute:
        print("DRY RUN — pass --execute to write files\n")

    tv_count = generate_tv_nfos(execute=args.execute, force=args.force)
    print()
    movie_count = generate_movie_nfos(execute=args.execute, force=args.force)

    action = "wrote" if args.execute else "would write"
    print(f"\nTotal: {action} {tv_count} TV NFOs + {movie_count} movie NFOs")


if __name__ == "__main__":
    main()
