#!/usr/bin/env python3
"""Test script to simulate downloads for pipeline testing.

This script can:
1. Copy existing media files from the library to the download staging area
2. Add them to qBittorrent as completed torrents (if qBittorrent is running)
3. Or directly test the pipeline worker with fake torrent records

Usage:
    # Test with a movie file
    python test_pipeline.py --source /mnt/raid/data/media/movies/Some.Movie.2024.mkv --category movies

    # Test with a TV show file
    python test_pipeline.py --source /mnt/raid/data/media/tv/Some.Show.S01E01.mkv --category tv

    # Test directly without qBittorrent (bypasses API)
    python test_pipeline.py --source /mnt/raid/data/media/movies/Some.Movie.2024.mkv --category movies --direct
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import httpx

# Add the orchestrator to the path
sys.path.insert(0, str(Path(__file__).parent))

from orchestrator.models import StackConfig
from orchestrator.pipeline.runner import PipelineRunner, QbittorrentAPI, TorrentRecord
from orchestrator.pipeline.worker import PipelineWorker, TorrentInfo
from orchestrator.storage import ConfigRepository


def create_minimal_torrent_file(file_path: Path, output_path: Path) -> None:
    """Create a minimal .torrent file for a single file.
    
    Note: Creating proper torrent files requires bencode encoding.
    For testing purposes, we'll use qBittorrent's API to add files directly
    or rely on the direct test mode which doesn't require torrent files.
    """
    # For now, create a placeholder - in practice you'd use bencode
    # The qBittorrent API mode may not work without proper torrent files
    print("Warning: Creating placeholder torrent file (may not work with qBittorrent API)")
    output_path.write_text(f"placeholder torrent for {file_path.name}\n")


def add_torrent_to_qbittorrent(
    api: QbittorrentAPI,
    torrent_file: Path,
    save_path: Path,
    category: str,
) -> Optional[str]:
    """Add a torrent to qBittorrent and mark it as completed."""
    try:
        # Add the torrent
        with open(torrent_file, 'rb') as f:
            files = {'torrents': (torrent_file.name, f, 'application/x-bittorrent')}
            data = {
                'savepath': str(save_path),
                'category': category,
                'skip_checking': 'true',  # Skip hash checking since file already exists
                'paused': 'false',
            }
            response = api.client.post(
                f"{api.base_url}/api/v2/torrents/add",
                files=files,
                data=data,
            )
            response.raise_for_status()
        
        # Get the torrent hash from the file name or by listing recent torrents
        # For simplicity, we'll list all torrents and find the one matching our file
        time.sleep(1)  # Give qBittorrent time to process
        torrents = api.list_completed()
        for torrent in torrents:
            if torrent.name == torrent_file.stem or save_path in str(torrent.save_path):
                return torrent.hash
        
        # If not found in completed, check all torrents
        response = api.client.get(f"{api.base_url}/api/v2/torrents/info")
        response.raise_for_status()
        all_torrents = response.json() or []
        for torrent in all_torrents:
            if save_path in str(torrent.get('save_path', '')):
                return torrent.get('hash')
        
        return None
    except Exception as e:
        print(f"Error adding torrent to qBittorrent: {e}")
        return None


def test_direct_processing(
    source_file: Path,
    category: str,
    config: StackConfig,
) -> None:
    """Test pipeline processing directly without qBittorrent."""
    print(f"[direct] Testing direct processing of {source_file.name}")
    
    # Create a fake torrent record
    file_hash = hashlib.sha256(str(source_file).encode()).hexdigest()[:40]
    
    # Determine download path based on category
    categories = config.download_policy.categories
    if category == 'movies':
        download_category = categories.radarr
    elif category == 'tv':
        download_category = categories.sonarr
    else:
        download_category = category
    
    # Use actual paths from config
    pool_root = Path(config.paths.pool)
    scratch = config.paths.scratch
    scratch_root = Path(scratch) if scratch is not None else pool_root / "downloads"
    download_path = scratch_root / "complete" / download_category
    
    # Copy file to download path
    download_path.mkdir(parents=True, exist_ok=True)
    test_file = download_path / source_file.name
    print(f"[direct] Copying {source_file} to {test_file}")
    shutil.copy2(source_file, test_file)
    
    # Create torrent info
    torrent_info = TorrentInfo(
        hash=file_hash,
        name=source_file.stem,
        category=download_category,
        download_path=download_path,
        files=[test_file],
    )
    
    # Process it
    print(f"[direct] Building remux plan...")
    worker = PipelineWorker(config)
    try:
        plan = worker.build_plan(torrent_info)
        print(f"[direct] Plan created:")
        print(f"  Source: {plan.source}")
        print(f"  Staging: {plan.staging_output}")
        print(f"  Final: {plan.final_output}")
        print(f"  FFmpeg command: {' '.join(plan.ffmpeg_command)}")
        
        # Run ffmpeg
        print(f"[direct] Running ffmpeg...")
        import subprocess
        result = subprocess.run(plan.ffmpeg_command, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[direct] FFmpeg failed:")
            print(result.stderr)
            return
        
        print(f"[direct] FFmpeg succeeded!")
        print(f"[direct] Moving to final location...")
        plan.final_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(plan.staging_output), str(plan.final_output))
        print(f"[direct] Success! File moved to {plan.final_output}")
        
        # Cleanup
        if test_file.exists():
            test_file.unlink()
            print(f"[direct] Cleaned up test file")
    except Exception as e:
        print(f"[direct] Error: {e}")
        import traceback
        traceback.print_exc()


def test_with_qbittorrent(
    source_file: Path,
    category: str,
    config: StackConfig,
) -> None:
    """Test pipeline processing by adding a torrent to qBittorrent."""
    print(f"[qb] Testing with qBittorrent for {source_file.name}")
    
    qb_cfg = config.services.qbittorrent
    base_url = f"http://127.0.0.1:{qb_cfg.port}"
    
    api = QbittorrentAPI(
        base_url=base_url,
        username=qb_cfg.username,
        password=qb_cfg.password,
    )
    
    try:
        api.login()
        print(f"[qb] Connected to qBittorrent")
        
        # Determine download path
        categories = config.download_policy.categories
        if category == 'movies':
            download_category = categories.radarr
        elif category == 'tv':
            download_category = categories.sonarr
        else:
            download_category = category
        
        # Use actual paths from config
        pool_root = Path(config.paths.pool)
        scratch = config.paths.scratch
        scratch_root = Path(scratch) if scratch is not None else pool_root / "downloads"
        download_path = scratch_root / "complete" / download_category
        download_path.mkdir(parents=True, exist_ok=True)
        
        # Copy file to download path
        test_file = download_path / source_file.name
        print(f"[qb] Copying {source_file} to {test_file}")
        shutil.copy2(source_file, test_file)
        
        # Create a minimal torrent file
        with tempfile.NamedTemporaryFile(suffix='.torrent', delete=False) as tmp:
            torrent_file = Path(tmp.name)
        
        print(f"[qb] Creating torrent file {torrent_file}")
        create_minimal_torrent_file(test_file, torrent_file)
        
        # Add to qBittorrent
        print(f"[qb] Adding torrent to qBittorrent...")
        torrent_hash = add_torrent_to_qbittorrent(api, torrent_file, download_path, download_category)
        
        if torrent_hash:
            print(f"[qb] Torrent added with hash: {torrent_hash}")
            print(f"[qb] The pipeline worker should pick this up on its next run")
            print(f"[qb] You can monitor the pipeline worker logs to see it process this file")
        else:
            print(f"[qb] Warning: Could not determine torrent hash")
            print(f"[qb] The torrent may still have been added - check qBittorrent UI")
        
        # Cleanup torrent file
        torrent_file.unlink()
        
    except Exception as e:
        print(f"[qb] Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        api.close()


def main() -> None:
    parser = argparse.ArgumentParser(description='Test pipeline with existing media files')
    parser.add_argument('--source', required=True, type=Path, help='Source media file to test with')
    parser.add_argument('--category', required=True, choices=['movies', 'tv', 'anime'], help='Category for the test file')
    parser.add_argument('--direct', action='store_true', help='Test directly without qBittorrent')
    parser.add_argument('--root', type=Path, help='Root directory for config (default: current directory)')
    
    args = parser.parse_args()
    
    if not args.source.exists():
        print(f"Error: Source file {args.source} does not exist")
        sys.exit(1)
    
    # Load config
    root = args.root or Path.cwd()
    repo = ConfigRepository(root)
    config = repo.load_stack()
    
    if args.direct:
        test_direct_processing(args.source, args.category, config)
    else:
        test_with_qbittorrent(args.source, args.category, config)


if __name__ == '__main__':
    main()

