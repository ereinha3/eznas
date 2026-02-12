import os
import stat
import shutil
from pathlib import Path
from typing import List, Dict, Optional, Any
from datetime import datetime

from orchestrator.converge.verification_models import (
    ValidationResult,
    ValidationError,
    ClientValidationRule,
    create_validation_error,
)


class PathValidator:
    """Validates filesystem paths for configuration"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.results = ValidationResult(success=True, duration_ms=0.0)

    def validate_all_paths(self) -> ValidationResult:
        """Validate all paths in the configuration"""
        start_time = datetime.utcnow()

        # Validate main paths
        self._validate_media_path()
        self._validate_downloads_path()
        self._validate_appdata_path()
        self._validate_scratch_path()

        # Validate service-specific paths
        self._validate_service_paths()

        # Validate path permissions and space
        self._validate_path_permissions()
        self._validate_path_space()

        # Add client-side validation rules
        self._add_path_validation_rules()

        # Calculate duration
        self.results.duration_ms = (
            datetime.utcnow() - start_time
        ).total_seconds() * 1000

        return self.results

    def _validate_media_path(self):
        """Validate media library path"""
        media_path = self.config.get("media_path")
        if not media_path:
            self.results.errors.append(
                create_validation_error(
                    "PATH_REQUIRED", "media_path", {"field": "Media Library Path"}
                )
            )
            self.results.success = False
            return

        path_obj = Path(media_path)

        # Check if path exists
        if not path_obj.exists():
            self.results.errors.append(
                create_validation_error(
                    "PATH_NOT_FOUND", "media_path", {"path": media_path}
                )
            )
            self.results.success = False
            return

        # Check if it's a directory
        if not path_obj.is_dir():
            self.results.errors.append(
                create_validation_error(
                    "PATH_NOT_DIRECTORY", "media_path", {"path": media_path}
                )
            )
            self.results.success = False
            return

    def _validate_downloads_path(self):
        """Validate downloads path"""
        downloads_path = self.config.get("downloads_path")
        if not downloads_path:
            self.results.errors.append(
                create_validation_error(
                    "PATH_REQUIRED", "downloads_path", {"field": "Downloads Path"}
                )
            )
            self.results.success = False
            return

        path_obj = Path(downloads_path)

        if not path_obj.exists():
            self.results.errors.append(
                create_validation_error(
                    "PATH_NOT_FOUND", "downloads_path", {"path": downloads_path}
                )
            )
            self.results.success = False
            return

        if not path_obj.is_dir():
            self.results.errors.append(
                create_validation_error(
                    "PATH_NOT_DIRECTORY", "downloads_path", {"path": downloads_path}
                )
            )
            self.results.success = False
            return

    def _validate_appdata_path(self):
        """Validate appdata path"""
        appdata_path = self.config.get("appdata_path")
        if not appdata_path:
            self.results.errors.append(
                create_validation_error(
                    "PATH_REQUIRED", "appdata_path", {"field": "App Data Path"}
                )
            )
            self.results.success = False
            return

        path_obj = Path(appdata_path)

        if not path_obj.exists():
            self.results.errors.append(
                create_validation_error(
                    "PATH_NOT_FOUND", "appdata_path", {"path": appdata_path}
                )
            )
            self.results.success = False
            return

        if not path_obj.is_dir():
            self.results.errors.append(
                create_validation_error(
                    "PATH_NOT_DIRECTORY", "appdata_path", {"path": appdata_path}
                )
            )
            self.results.success = False
            return

    def _validate_scratch_path(self):
        """Validate optional scratch path"""
        scratch_path = self.config.get("scratch_path")
        if scratch_path:
            path_obj = Path(scratch_path)

            if not path_obj.exists():
                self.results.errors.append(
                    create_validation_error(
                        "PATH_NOT_FOUND", "scratch_path", {"path": scratch_path}
                    )
                )
                self.results.success = False
                return

            if not path_obj.is_dir():
                self.results.errors.append(
                    create_validation_error(
                        "PATH_NOT_DIRECTORY", "scratch_path", {"path": scratch_path}
                    )
                )
                self.results.success = False
                return

    def _validate_service_paths(self):
        """Validate paths for specific services"""
        # Validate qBittorrent paths
        if "qbittorrent" in self.config:
            qb_config = self.config["qbittorrent"]

            # Validate download directory
            qb_download_dir = qb_config.get("download_dir")
            if qb_download_dir:
                path_obj = Path(qb_download_dir)
                if not path_obj.exists():
                    self.results.errors.append(
                        create_validation_error(
                            "PATH_NOT_FOUND",
                            "qbittorrent.download_dir",
                            {"path": qb_download_dir},
                        )
                    )
                    self.results.success = False

        # Validate Radarr/Sonarr paths
        for service in ["radarr", "sonarr"]:
            if service in self.config:
                service_config = self.config[service]

                # Validate root folder path
                root_folder = service_config.get("root_folder")
                if root_folder:
                    path_obj = Path(root_folder)
                    if not path_obj.exists():
                        self.results.errors.append(
                            create_validation_error(
                                "PATH_NOT_FOUND",
                                f"{service}.root_folder",
                                {"path": root_folder},
                            )
                        )
                        self.results.success = False

    def _validate_path_permissions(self):
        """Validate read/write permissions for paths"""
        paths_to_check = [
            ("media_path", "Media Library"),
            ("downloads_path", "Downloads"),
            ("appdata_path", "App Data"),
        ]

        for config_key, description in paths_to_check:
            path_value = self.config.get(config_key)
            if path_value:
                path_obj = Path(path_value)

                # Check read permission
                if not os.access(path_obj, os.R_OK):
                    self.results.errors.append(
                        create_validation_error(
                            "PATH_NO_READ_PERMISSION",
                            config_key,
                            {"path": path_value, "description": description},
                        )
                    )
                    self.results.success = False

                # Check write permission
                if not os.access(path_obj, os.W_OK):
                    self.results.errors.append(
                        create_validation_error(
                            "PATH_NO_WRITE_PERMISSION",
                            config_key,
                            {"path": path_value, "description": description},
                        )
                    )
                    self.results.success = False

    def _validate_path_space(self):
        """Validate available disk space for paths"""
        paths_to_check = [
            ("media_path", "10GB"),
            ("downloads_path", "50GB"),
            ("appdata_path", "5GB"),
        ]

        for config_key, min_space in paths_to_check:
            path_value = self.config.get(config_key)
            if path_value:
                path_obj = Path(path_value)

                try:
                    # Get disk usage
                    total, used, free = shutil.disk_usage(path_obj)
                    free_gb = free / (1024**3)

                    # Convert min_space to GB
                    if min_space.endswith("GB"):
                        min_gb = float(min_space[:-2])
                    else:
                        min_gb = 10.0  # Default

                    if free_gb < min_gb:
                        self.results.warnings.append(
                            ValidationError(
                                field=config_key,
                                message=f"Low disk space: {free_gb:.1f}GB available (need {min_gb}GB)",
                                severity="warning",
                                suggestions=[
                                    f"Free up space on {path_value}",
                                    f"Select a different path with more space",
                                    f"Remove unnecessary files",
                                ],
                                code="LOW_DISK_SPACE",
                            )
                        )

                except Exception as e:
                    self.results.warnings.append(
                        ValidationError(
                            field=config_key,
                            message=f"Could not check disk space: {str(e)}",
                            severity="warning",
                            suggestions=[
                                f"Check disk space manually on {path_value}",
                                f"Ensure path is accessible",
                            ],
                            code="DISK_SPACE_CHECK_FAILED",
                        )
                    )

    def _add_path_validation_rules(self):
        """Add client-side validation rules for paths"""
        self.results.client_side_rules.update(
            {
                "media_path": ClientValidationRule(
                    field="media_path",
                    type="string",
                    required=True,
                    min_length=3,
                    max_length=255,
                    pattern=None,
                    min_value=None,
                    max_value=None,
                    custom_rules=[],
                ),
                "downloads_path": ClientValidationRule(
                    field="downloads_path",
                    type="string",
                    required=True,
                    min_length=3,
                    max_length=255,
                    pattern=None,
                    min_value=None,
                    max_value=None,
                    custom_rules=[],
                ),
                "appdata_path": ClientValidationRule(
                    field="appdata_path",
                    type="string",
                    required=True,
                    min_length=3,
                    max_length=255,
                    pattern=None,
                    min_value=None,
                    max_value=None,
                    custom_rules=[],
                ),
                "scratch_path": ClientValidationRule(
                    field="scratch_path",
                    type="string",
                    required=False,
                    min_length=3,
                    max_length=255,
                    pattern=None,
                    min_value=None,
                    max_value=None,
                    custom_rules=[],
                ),
            }
        )


def get_mount_points() -> List[str]:
    """Get list of available mount points"""
    mount_points = []

    try:
        with open("/proc/mounts", "r") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) > 1:
                    mount_points.append(parts[1])
    except FileNotFoundError:
        pass  # Not on Linux

    return mount_points


def is_path_mounted(path: str) -> bool:
    """Check if a path is mounted"""
    path_obj = Path(path).resolve()

    try:
        stat_info = os.stat(path_obj)
        stat_dev = stat_info.st_dev

        # Check parent directories
        parent = path_obj.parent
        while parent != path_obj:
            parent_stat = os.stat(parent)
            if parent_stat.st_dev != stat_dev:
                return True
            parent = parent.parent

        return False
    except FileNotFoundError:
        return False
