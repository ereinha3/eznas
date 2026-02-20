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


def _get_host_path(path: str) -> Path:
    """Get the actual path to check, considering Docker container mounts.

    When running in Docker with host filesystem mounted at /host, prepend /host/ to absolute paths.
    """
    p = Path(path)

    # Check if we're in a Docker container with /host mount
    host_mount = Path("/host")
    if host_mount.exists() and host_mount.is_dir() and p.is_absolute():
        # We're in Docker, check the path at /host/<path>
        return host_mount / str(p)[1:]  # Remove leading /

    return p


def _owner_info(p: Path) -> str:
    """Return 'owner:group (mode)' for a path, for diagnostic messages."""
    import os, pwd, grp
    try:
        st = p.stat()
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = str(st.st_uid)
        try:
            group = grp.getgrgid(st.st_gid).gr_name
        except KeyError:
            group = str(st.st_gid)
        return f"{owner}:{group} ({oct(st.st_mode)[-3:]})"
    except Exception:
        return "unknown"


def _fix_command(path: str, uid: int | None = None, gid: int | None = None) -> str:
    """Build the sudo command the user should run to fix permissions."""
    parts = [f"sudo chown -R {uid or '$(id -u)'}:{gid or '$(id -g)'} {path}"]
    parts.append(f"sudo chmod -R 775 {path}")
    return " && ".join(parts)


def validate_path(
    path: str,
    require_writable: bool = True,
    auto_create: bool = False,
    fix_permissions: bool = False,
) -> Dict[str, Any]:
    """Validate a path for use as storage location.

    Args:
        path: The path to validate (host path)
        require_writable: Whether the path must be writable
        auto_create: Whether to auto-create the directory if it doesn't exist
        fix_permissions: Whether to fix permissions if they're wrong

    Returns a dict with:
    - valid: bool
    - exists: bool
    - writable: bool
    - created: bool - whether the directory was created
    - permissions_fixed: bool - whether permissions were fixed
    - warning: str or None - warning message about existing directory
    - error: str or None
    - fix_command: str or None - exact command to fix the issue
    """
    import os

    result: Dict[str, Any] = {
        "valid": False,
        "exists": False,
        "writable": False,
        "created": False,
        "permissions_fixed": False,
        "warning": None,
        "error": None,
        "fix_command": None,
    }

    try:
        # Get the actual path to check (handles Docker /host mount)
        p = _get_host_path(path)
        result["exists"] = p.exists()

        if p.exists():
            # Path exists - check if it's a directory
            if not p.is_dir():
                result["error"] = f"Path exists but is not a directory: {path}"
                return result

            # Use os.access() — checks the REAL user's ability to write,
            # not just the file mode bits.
            is_writable = os.access(p, os.W_OK | os.X_OK)

            if require_writable:
                result["writable"] = is_writable
                result["valid"] = is_writable

                if not is_writable:
                    info = _owner_info(p)
                    fix_cmd = _fix_command(path)
                    if fix_permissions:
                        try:
                            p.chmod(0o775)
                            # Re-check after chmod — ownership might still block us
                            if os.access(p, os.W_OK | os.X_OK):
                                result["permissions_fixed"] = True
                                result["writable"] = True
                                result["valid"] = True
                                result["warning"] = (
                                    "Directory permissions were updated to allow writing"
                                )
                            else:
                                # chmod worked but we still can't write (ownership issue)
                                result["error"] = (
                                    f"Directory is owned by {info} and is not writable by the current user. "
                                    f"Run: {fix_cmd}"
                                )
                                result["fix_command"] = fix_cmd
                        except PermissionError:
                            result["error"] = (
                                f"Directory is owned by {info} — cannot fix permissions. "
                                f"Run: {fix_cmd}"
                            )
                            result["fix_command"] = fix_cmd
                    else:
                        result["error"] = (
                            f"Directory is not writable (owned by {info}). "
                            f"Run: {fix_cmd}"
                        )
                        result["fix_command"] = fix_cmd
                        result["valid"] = False
            else:
                result["writable"] = True
                result["valid"] = True
        else:
            # Path doesn't exist
            parent = p.parent

            if parent.exists() and parent.is_dir():
                parent_writable = os.access(parent, os.W_OK | os.X_OK)

                if auto_create:
                    if parent_writable:
                        try:
                            p.mkdir(parents=True, exist_ok=True)
                            p.chmod(0o775)
                            result["exists"] = True
                            result["created"] = True
                            result["writable"] = True
                            result["valid"] = True
                            result["warning"] = f"Directory was created at {path}"
                        except PermissionError as create_err:
                            fix_cmd = _fix_command(str(parent))
                            result["error"] = (
                                f"Failed to create directory: {create_err}. "
                                f"Run: {fix_cmd}"
                            )
                            result["fix_command"] = fix_cmd
                    else:
                        fix_cmd = f"sudo mkdir -p {path} && " + _fix_command(path)
                        result["error"] = (
                            f"Cannot create directory — parent {parent} is not writable "
                            f"(owned by {_owner_info(parent)}). "
                            f"Run: {fix_cmd}"
                        )
                        result["fix_command"] = fix_cmd
                else:
                    result["writable"] = parent_writable
                    result["valid"] = parent_writable

                    if parent_writable:
                        result["warning"] = (
                            "Directory does not exist but parent is writable. "
                            "It will be created automatically."
                        )
                    else:
                        fix_cmd = f"sudo mkdir -p {path} && " + _fix_command(path)
                        result["error"] = (
                            f"Parent directory {parent} is not writable "
                            f"(owned by {_owner_info(parent)}). "
                            f"Run: {fix_cmd}"
                        )
                        result["fix_command"] = fix_cmd
            else:
                fix_cmd = f"sudo mkdir -p {path} && " + _fix_command(path)
                result["error"] = (
                    f"Parent directory {parent} does not exist. "
                    f"Run: {fix_cmd}"
                )
                result["fix_command"] = fix_cmd
    except Exception as e:
        result["error"] = str(e)

    return result
