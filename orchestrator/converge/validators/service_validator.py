import httpx
import asyncio
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from urllib.parse import urlparse

from orchestrator.converge.verification_models import (
    ValidationResult,
    ValidationError,
    ClientValidationRule,
    create_validation_error,
)


class ServiceValidator:
    """Validates service endpoints and configurations"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.results = ValidationResult(success=True, duration_ms=0.0)

    async def validate_all_services(self) -> ValidationResult:
        """Validate all services in the configuration"""
        start_time = datetime.utcnow()

        # Validate services in priority order
        await self._validate_qbittorrent()
        await self._validate_prowlarr()
        await self._validate_radarr()
        await self._validate_sonarr()
        await self._validate_jellyfin()
        await self._validate_jellyseerr()
        await self._validate_remux_agent()

        # Validate service dependencies
        await self._validate_service_dependencies()

        # Add client-side validation rules
        self._add_service_validation_rules()

        # Calculate duration
        self.results.duration_ms = (
            datetime.utcnow() - start_time
        ).total_seconds() * 1000

        return self.results

    async def _validate_qbittorrent(self):
        """Validate qBittorrent configuration"""
        qb_config = self.config.get("qbittorrent", {})
        if not qb_config:
            self.results.warnings.append(
                ValidationError(
                    field="qbittorrent",
                    message="qBittorrent configuration is missing",
                    severity="warning",
                    suggestions=[
                        "Configure qBittorrent for download management",
                        "qBittorrent is required for the media pipeline",
                    ],
                    code="SERVICE_NOT_CONFIGURED",
                )
            )
            return

        # Validate host and port
        host = qb_config.get("host", "localhost")
        port = qb_config.get("web_port", 8080)

        # Test API connectivity
        endpoint = f"http://{host}:{port}/api/v2/app/version"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(endpoint)
                if response.status_code == 200:
                    self.results.warnings.append(
                        ValidationError(
                            field="qbittorrent.connectivity",
                            message="qBittorrent API is accessible without authentication",
                            severity="info",
                            suggestions=[
                                "Configure authentication for production use",
                                "qBittorrent should be protected in production",
                            ],
                            code="SERVICE_AUTHENTICATION_RECOMMENDED",
                        )
                    )
                else:
                    self.results.errors.append(
                        create_validation_error(
                            "SERVICE_UNREACHABLE",
                            "qbittorrent.endpoint",
                            {"service": "qBittorrent", "endpoint": endpoint},
                        )
                    )
                    self.results.success = False
        except httpx.RequestError:
            # Expected - service not running yet
            pass

        # Validate download directory
        download_dir = qb_config.get("download_dir")
        if download_dir:
            # This will be validated by the path validator
            pass

    async def _validate_prowlarr(self):
        """Validate Prowlarr configuration"""
        prow_config = self.config.get("prowlarr", {})
        if not prow_config:
            self.results.warnings.append(
                ValidationError(
                    field="prowlarr",
                    message="Prowlarr configuration is missing",
                    severity="warning",
                    suggestions=[
                        "Configure Prowlarr for indexer management",
                        "Prowlarr provides automatic indexers for Radarr/Sonarr",
                    ],
                    code="SERVICE_NOT_CONFIGURED",
                )
            )
            return

        # Validate host and port
        host = prow_config.get("host", "localhost")
        port = prow_config.get("port", 9696)
        api_key = prow_config.get("api_key")

        # Test API connectivity
        endpoint = f"http://{host}:{port}/api/v1/status"
        try:
            headers = {"X-Api-Key": api_key} if api_key else {}
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(endpoint, headers=headers)
                if response.status_code == 401:
                    self.results.errors.append(
                        create_validation_error(
                            "SERVICE_AUTHENTICATION_FAILED",
                            "prowlarr.api_key",
                            {"service": "Prowlarr"},
                        )
                    )
                    self.results.success = False
                elif response.status_code == 200:
                    # API is working
                    pass
                else:
                    self.results.errors.append(
                        create_validation_error(
                            "SERVICE_UNREACHABLE",
                            "prowlarr.endpoint",
                            {"service": "Prowlarr", "endpoint": endpoint},
                        )
                    )
                    self.results.success = False
        except httpx.RequestError:
            # Expected - service not running yet
            pass

    async def _validate_radarr(self):
        """Validate Radarr configuration"""
        radarr_config = self.config.get("radarr", {})
        if not radarr_config:
            self.results.warnings.append(
                ValidationError(
                    field="radarr",
                    message="Radarr configuration is missing",
                    severity="warning",
                    suggestions=[
                        "Configure Radarr for movie management",
                        "Radarr manages movie downloads and library",
                    ],
                    code="SERVICE_NOT_CONFIGURED",
                )
            )
            return

        # Validate host and port
        host = radarr_config.get("host", "localhost")
        port = radarr_config.get("port", 7878)
        api_key = radarr_config.get("api_key")

        # Test API connectivity
        endpoint = f"http://{host}:{port}/api/v3/status"
        try:
            headers = {"X-Api-Key": api_key} if api_key else {}
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(endpoint, headers=headers)
                if response.status_code == 401:
                    self.results.errors.append(
                        create_validation_error(
                            "SERVICE_AUTHENTICATION_FAILED",
                            "radarr.api_key",
                            {"service": "Radarr"},
                        )
                    )
                    self.results.success = False
                elif response.status_code == 200:
                    # API is working
                    pass
                else:
                    self.results.errors.append(
                        create_validation_error(
                            "SERVICE_UNREACHABLE",
                            "radarr.endpoint",
                            {"service": "Radarr", "endpoint": endpoint},
                        )
                    )
                    self.results.success = False
        except httpx.RequestError:
            # Expected - service not running yet
            pass

        # Validate root folder
        root_folder = radarr_config.get("root_folder")
        if root_folder:
            # This will be validated by the path validator
            pass

    async def _validate_sonarr(self):
        """Validate Sonarr configuration"""
        sonarr_config = self.config.get("sonarr", {})
        if not sonarr_config:
            self.results.warnings.append(
                ValidationError(
                    field="sonarr",
                    message="Sonarr configuration is missing",
                    severity="warning",
                    suggestions=[
                        "Configure Sonarr for TV show management",
                        "Sonarr manages TV show downloads and library",
                    ],
                    code="SERVICE_NOT_CONFIGURED",
                )
            )
            return

        # Validate host and port
        host = sonarr_config.get("host", "localhost")
        port = sonarr_config.get("port", 8989)
        api_key = sonarr_config.get("api_key")

        # Test API connectivity
        endpoint = f"http://{host}:{port}/api/v3/status"
        try:
            headers = {"X-Api-Key": api_key} if api_key else {}
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(endpoint, headers=headers)
                if response.status_code == 401:
                    self.results.errors.append(
                        create_validation_error(
                            "SERVICE_AUTHENTICATION_FAILED",
                            "sonarr.api_key",
                            {"service": "Sonarr"},
                        )
                    )
                    self.results.success = False
                elif response.status_code == 200:
                    # API is working
                    pass
                else:
                    self.results.errors.append(
                        create_validation_error(
                            "SERVICE_UNREACHABLE",
                            "sonarr.endpoint",
                            {"service": "Sonarr", "endpoint": endpoint},
                        )
                    )
                    self.results.success = False
        except httpx.RequestError:
            # Expected - service not running yet
            pass

        # Validate root folder
        root_folder = sonarr_config.get("root_folder")
        if root_folder:
            # This will be validated by the path validator
            pass

    async def _validate_jellyfin(self):
        """Validate Jellyfin configuration"""
        jellyfin_config = self.config.get("jellyfin", {})
        if not jellyfin_config:
            self.results.warnings.append(
                ValidationError(
                    field="jellyfin",
                    message="Jellyfin configuration is missing",
                    severity="warning",
                    suggestions=[
                        "Configure Jellyfin for media serving",
                        "Jellyfin provides media streaming and library management",
                    ],
                    code="SERVICE_NOT_CONFIGURED",
                )
            )
            return

        # Validate host and port
        host = jellyfin_config.get("host", "localhost")
        port = jellyfin_config.get("port", 8096)

        # Test web interface connectivity
        endpoint = f"http://{host}:{port}/web/index.html"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(endpoint)
                if response.status_code == 200:
                    # Web interface is accessible
                    pass
                else:
                    self.results.warnings.append(
                        create_validation_error(
                            "SERVICE_UNREACHABLE",
                            "jellyfin.endpoint",
                            {"service": "Jellyfin", "endpoint": endpoint},
                        )
                    )
        except httpx.RequestError:
            # Expected - service not running yet
            pass

        # Validate media paths
        media_paths = jellyfin_config.get("media_paths", [])
        for media_path in media_paths:
            # This will be validated by the path validator
            pass

    async def _validate_jellyseerr(self):
        """Validate Jellyseerr configuration"""
        jellyseerr_config = self.config.get("jellyseerr", {})
        if not jellyseerr_config:
            self.results.warnings.append(
                ValidationError(
                    field="jellyseerr",
                    message="Jellyseerr configuration is missing",
                    severity="warning",
                    suggestions=[
                        "Configure Jellyseerr for request management",
                        "Jellyseerr provides user-friendly media request interface",
                    ],
                    code="SERVICE_NOT_CONFIGURED",
                )
            )
            return

        # Validate host and port
        host = jellyseerr_config.get("host", "localhost")
        port = jellyseerr_config.get("port", 5055)

        # Test web interface connectivity
        endpoint = f"http://{host}:{port}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(endpoint)
                if response.status_code == 200:
                    # Web interface is accessible
                    pass
                else:
                    self.results.warnings.append(
                        create_validation_error(
                            "SERVICE_UNREACHABLE",
                            "jellyseerr.endpoint",
                            {"service": "Jellyseerr", "endpoint": endpoint},
                        )
                    )
        except httpx.RequestError:
            # Expected - service not running yet
            pass

    async def _validate_remux_agent(self):
        """Validate Remux Agent configuration"""
        remux_config = self.config.get("remux_agent", {})
        if not remux_config:
            self.results.warnings.append(
                ValidationError(
                    field="remux_agent",
                    message="Remux Agent configuration is missing",
                    severity="warning",
                    suggestions=[
                        "Configure Remux Agent for media processing",
                        "Remux Agent converts media to optimal formats",
                    ],
                    code="SERVICE_NOT_CONFIGURED",
                )
            )
            return

        # Validate FFmpeg availability
        ffmpeg_path = remux_config.get("ffmpeg_path", "ffmpeg")
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg_path,
                "-version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()

            if proc.returncode != 0:
                self.results.errors.append(
                    create_validation_error(
                        "DEPENDENCY_NOT_FOUND",
                        "remux_agent.ffmpeg_path",
                        {"dependency": "FFmpeg"},
                    )
                )
                self.results.success = False
        except (FileNotFoundError, Exception):
            self.results.errors.append(
                create_validation_error(
                    "DEPENDENCY_NOT_FOUND",
                    "remux_agent.ffmpeg_path",
                    {"dependency": "FFmpeg"},
                )
            )
            self.results.success = False

        # Validate language filters
        language_filters = remux_config.get("language_filters", {})
        if not language_filters:
            self.results.warnings.append(
                ValidationError(
                    field="remux_agent.language_filters",
                    message="No language filters configured",
                    severity="warning",
                    suggestions=[
                        "Configure language filters for media processing",
                        "Specify which audio/subtitle languages to keep",
                    ],
                    code="REMUX_CONFIGURATION_MISSING",
                )
            )

        # Validate working directory
        work_dir = remux_config.get("work_dir")
        if work_dir:
            # This will be validated by the path validator
            pass

    async def _validate_service_dependencies(self):
        """Validate dependencies between services"""
        # Check if qBittorrent is configured when Radarr/Sonarr are configured
        radarr_config = self.config.get("radarr", {})
        sonarr_config = self.config.get("sonarr", {})
        qb_config = self.config.get("qbittorrent", {})

        if (radarr_config or sonarr_config) and not qb_config:
            self.results.warnings.append(
                ValidationError(
                    field="service_dependencies",
                    message="Radarr/Sonarr configured without qBittorrent",
                    severity="warning",
                    suggestions=[
                        "Configure qBittorrent for download management",
                        "Radarr/Sonarr require qBittorrent for downloads",
                    ],
                    code="MISSING_DEPENDENCY",
                )
            )

        # Check if Prowlarr is configured when Radarr/Sonarr are configured
        prow_config = self.config.get("prowlarr", {})
        if (radarr_config or sonarr_config) and not prow_config:
            self.results.warnings.append(
                ValidationError(
                    field="service_dependencies",
                    message="Radarr/Sonarr configured without Prowlarr",
                    severity="warning",
                    suggestions=[
                        "Configure Prowlarr for automatic indexer management",
                        "Prowlarr provides indexers to Radarr/Sonarr",
                    ],
                    code="MISSING_DEPENDENCY",
                )
            )

        # Check if Jellyfin is configured when using media library
        jellyfin_config = self.config.get("jellyfin", {})
        if (radarr_config or sonarr_config) and not jellyfin_config:
            self.results.warnings.append(
                ValidationError(
                    field="service_dependencies",
                    message="Media services configured without Jellyfin",
                    severity="warning",
                    suggestions=[
                        "Configure Jellyfin for media serving",
                        "Jellyfin provides access to your media library",
                    ],
                    code="MISSING_DEPENDENCY",
                )
            )

    def _add_service_validation_rules(self):
        """Add client-side validation rules for services"""
        # API key validation rule
        api_key_rule = ClientValidationRule(
            field="api_key",
            type="string",
            required=True,
            min_length=20,
            max_length=100,
            pattern=None,
            min_value=None,
            max_value=None,
            custom_rules=["api_key_format"],
        )

        # URL/endpoint validation rule
        endpoint_rule = ClientValidationRule(
            field="endpoint",
            type="url",
            required=False,
            min_length=10,
            max_length=255,
            pattern=None,
            min_value=None,
            max_value=None,
            custom_rules=["url_format"],
        )

        # Apply rules to services
        services_with_api_keys = ["prowlarr", "radarr", "sonarr"]
        for service in services_with_api_keys:
            self.results.client_side_rules[f"{service}.api_key"] = api_key_rule
            self.results.client_side_rules[f"{service}.host"] = ClientValidationRule(
                field=f"{service}.host",
                type="string",
                required=True,
                min_length=3,
                max_length=255,
                pattern=None,
                min_value=None,
                max_value=None,
                custom_rules=["hostname_format"],
            )

        # Special rules for remux agent
        self.results.client_side_rules["remux_agent.ffmpeg_path"] = (
            ClientValidationRule(
                field="ffmpeg_path",
                type="string",
                required=True,
                min_length=4,
                max_length=255,
                pattern=None,
                min_value=None,
                max_value=None,
                custom_rules=["executable_path"],
            )
        )
