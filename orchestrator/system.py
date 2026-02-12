"""System utilities for volume scanning and path detection."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Dict, Any


def scan_volumes() -> List[Dict[str, Any]]:
    """Scan for available mounted volumes on the system.

    Returns a list of volumes with their mount points, sizes, and filesystems.
    """
    volumes = []

    try:
        # Use df to get mounted filesystems
        result = subprocess.run(
            ["df", "-h", "--output=source,size,avail,target"],
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            # Skip header line
            for line in lines[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    device = parts[0]
                    size = parts[1]
                    available = parts[2]
                    mountpoint = parts[3]

                    # Only include real filesystems (not tmpfs, devtmpfs, etc.)
                    if device.startswith("/dev/"):
                        filesystem = get_filesystem_type(mountpoint)
                        volumes.append(
                            {
                                "device": device,
                                "mountpoint": mountpoint,
                                "size": size,
                                "available": available,
                                "filesystem": filesystem,
                                "suggested_paths": suggest_paths(mountpoint),
                            }
                        )
    except Exception as e:
        print(f"Error scanning volumes: {e}")

    return volumes


def get_filesystem_type(mountpoint: str) -> str:
    """Get the filesystem type for a mount point."""
    try:
        result = subprocess.run(
            ["df", "-T", mountpoint], capture_output=True, text=True, timeout=2
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) > 1:
                    return parts[1]
    except:
        pass
    return "unknown"


def suggest_paths(mountpoint: str) -> Dict[str, str]:
    """Suggest standard paths for media, downloads, and appdata."""
    mount = Path(mountpoint)
    return {
        "media": str(mount / "media"),
        "downloads": str(mount / "downloads"),
        "appdata": str(mount / "appdata"),
    }


def check_path_exists(path: str) -> bool:
    """Check if a path exists and is accessible."""
    try:
        return Path(path).exists()
    except:
        return False


def validate_path(path: str, require_writable: bool = True) -> Dict[str, Any]:
    """Validate a path for use as storage location.

    Returns a dict with:
    - valid: bool
    - exists: bool
    - writable: bool
    - error: str or None
    """
    result = {"valid": False, "exists": False, "writable": False, "error": None}

    try:
        p = Path(path)
        result["exists"] = p.exists()

        if p.exists():
            # Check if writable
            if require_writable:
                result["writable"] = p.is_dir() and p.stat().st_mode & 0o200
            else:
                result["writable"] = True

            result["valid"] = result["writable"]
        else:
            # Check if parent exists and is writable
            parent = p.parent
            if parent.exists() and parent.is_dir():
                result["writable"] = parent.stat().st_mode & 0o200
                result["valid"] = result["writable"]
            else:
                result["error"] = (
                    f"Parent directory {parent} does not exist or is not writable"
                )
    except Exception as e:
        result["error"] = str(e)

    return result
