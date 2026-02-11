import socket
from typing import List, Dict, Optional, Any, Tuple
from datetime import datetime

from orchestrator.converge.verification_models import (
    ValidationResult,
    ValidationError,
    ClientValidationRule,
    create_validation_error,
)


class PortValidator:
    """Validates network ports for configuration"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.results = ValidationResult(success=True, duration_ms=0.0)

    def validate_all_ports(self) -> ValidationResult:
        """Validate all ports in the configuration"""
        start_time = datetime.utcnow()

        # Get available network interfaces
        self._validate_network_interfaces()

        # Validate main service ports
        self._validate_service_ports()

        # Check for port conflicts
        self._check_port_conflicts()

        # Add client-side validation rules
        self._add_port_validation_rules()

        # Calculate duration
        self.results.duration_ms = (
            datetime.utcnow() - start_time
        ).total_seconds() * 1000

        return self.results

    def _validate_network_interfaces(self):
        """Validate network interface configuration"""
        try:
            # Simple network interface detection without netifaces
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)

            # Network interface detected successfully - no additional validation needed

        except Exception as e:
            self.results.warnings.append(
                ValidationError(
                    code="NETWORK_INTERFACE_WARNING",
                    field="network_interfaces",
                    message=f"Network interface detection limited: {e}",
                    severity="warning",
                    suggestions=[
                        "Network connectivity validation will be basic",
                        "Port validation will still function",
                    ],
                )
            )

    def _validate_service_ports(self):
        """Validate ports for each service"""
        service_ports = {
            "qbittorrent": self.config.get("qbittorrent", {}).get("web_port", 8080),
            "prowlarr": self.config.get("prowlarr", {}).get("port", 9696),
            "radarr": self.config.get("radarr", {}).get("port", 7878),
            "sonarr": self.config.get("sonarr", {}).get("port", 8989),
            "jellyfin": self.config.get("jellyfin", {}).get("port", 8096),
            "jellyseerr": self.config.get("jellyseerr", {}).get("port", 5055),
        }

        for service, port in service_ports.items():
            if port:
                self._validate_single_port(service, port)

    def _validate_single_port(self, service: str, port: int):
        """Validate a single port for a service"""
        # Check if port is in valid range
        if not (1 <= port <= 65535):
            self.results.errors.append(
                create_validation_error(
                    "PORT_OUT_OF_RANGE",
                    f"{service}.port",
                    {"port": port, "service": service},
                )
            )
            self.results.success = False
            return

        # Check if port is available
        if self._is_port_in_use(port):
            self.results.errors.append(
                create_validation_error(
                    "PORT_IN_USE", f"{service}.port", {"port": port, "service": service}
                )
            )
            self.results.success = False
        else:
            # Port is available, add warning for commonly used ports
            if port in [80, 443, 22, 21, 23, 25, 53, 110, 143]:
                self.results.warnings.append(
                    ValidationError(
                        field=f"{service}.port",
                        message=f"Port {port} is commonly used by other services",
                        severity="warning",
                        suggestions=[
                            f"Consider using a different port for {service}",
                            "Check for conflicts with system services",
                        ],
                        code="COMMON_PORT_IN_USE",
                    )
                )

    def _check_port_conflicts(self):
        """Check for conflicts between service ports"""
        service_ports = {}

        # Collect all service ports
        services = [
            "qbittorrent",
            "prowlarr",
            "radarr",
            "sonarr",
            "jellyfin",
            "jellyseerr",
        ]
        for service in services:
            port = self.config.get(service, {}).get("port")
            if port:
                if port not in service_ports:
                    service_ports[port] = []
                service_ports[port].append(service)

        # Check for conflicts
        for port, services_list in service_ports.items():
            if len(services_list) > 1:
                self.results.errors.append(
                    ValidationError(
                        field="port_conflict",
                        message=f"Port {port} is assigned to multiple services: {', '.join(services_list)}",
                        severity="error",
                        suggestions=[
                            f"Assign different ports to each service",
                            "Recommended ports: qBittorrent (8080), Prowlarr (9696), Radarr (7878), Sonarr (8989), Jellyfin (8096), Jellyseerr (5055)",
                        ],
                        code="DUPLICATE_PORT_ASSIGNMENT",
                    )
                )
                self.results.success = False

    def _is_port_in_use(self, port: int) -> bool:
        """Check if a port is currently in use"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1)
                result = sock.connect_ex(("127.0.0.1", port))
                return result == 0
        except socket.error:
            return False

    def _add_port_validation_rules(self):
        """Add client-side validation rules for ports"""
        port_rule = ClientValidationRule(
            field="port",
            type="number",
            required=True,
            min_value=1,
            max_value=65535,
            pattern=None,
            min_length=None,
            max_length=None,
            custom_rules=["port_available", "unique_port"],
        )

        # Add port validation rules for each service
        services = [
            "qbittorrent",
            "prowlarr",
            "radarr",
            "sonarr",
            "jellyfin",
            "jellyseerr",
        ]
        for service in services:
            self.results.client_side_rules[f"{service}.port"] = port_rule


def get_available_ports(start_port: int = 8000, end_port: int = 9000) -> List[int]:
    """Get list of available ports in a range"""
    available_ports = []

    for port in range(start_port, end_port + 1):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.1)
                result = sock.connect_ex(("127.0.0.1", port))
                if result != 0:  # Port is not in use
                    available_ports.append(port)
        except socket.error:
            continue

    return available_ports


def get_network_interfaces() -> List[Dict[str, str]]:
    """Get list of network interfaces with IP addresses"""
    interfaces = []

    try:
        hostname = socket.gethostname()
        local_ip = socket.gethostbyname(hostname)
        interfaces.append(
            {"name": hostname, "ip": local_ip, "netmask": "255.255.255.0"}
        )
    except Exception:
        pass

    return interfaces


def validate_port_range(port: int) -> bool:
    """Validate that port is in correct range"""
    return 1 <= port <= 65535


def is_reserved_port(port: int) -> bool:
    """Check if port is in reserved range (1-1023)"""
    return 1 <= port <= 1023
