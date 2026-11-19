"""Gluetun port forwarding is opt-in and pushes the forwarded port to qBittorrent.

Private trackers rank peers that can accept incoming connections, which
behind a VPN requires the provider to forward a port. Proton hands one out
over NAT-PMP, and it changes between sessions, so qBittorrent must be told
the new port every time gluetun obtains one.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import yaml

from orchestrator.models import StackConfig
from orchestrator.rendering import ComposeRenderer

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"

WG_CONFIG = """[Interface]
PrivateKey = cHJpdmF0ZWtleXByaXZhdGVrZXlwcml2YXRla2V5MDA=
Address = 10.2.0.2/32
DNS = 10.2.0.1

[Peer]
PublicKey = cHVibGlja2V5cHVibGlja2V5cHVibGlja2V5cHViMDA=
AllowedIPs = 0.0.0.0/0
Endpoint = 185.159.158.1:51820
"""


def _gluetun_env(sample_config: Dict[str, Any], tmp_path: Path, **gluetun: Any) -> Dict[str, str]:
    sample_config["services"]["gluetun"] = {
        "enabled": True,
        "wireguard_config": WG_CONFIG,
        **gluetun,
    }
    config = StackConfig.model_validate(sample_config)
    result = ComposeRenderer(TEMPLATES).render(config, tmp_path)
    compose = yaml.safe_load(Path(result.compose_path).read_text())
    return dict(item.split("=", 1) for item in compose["services"]["gluetun"]["environment"])


def test_port_forwarding_off_by_default(sample_config: Dict[str, Any], tmp_path: Path) -> None:
    env = _gluetun_env(sample_config, tmp_path)

    assert env.get("VPN_PORT_FORWARDING", "off") == "off"
    assert "VPN_PORT_FORWARDING_UP_COMMAND" not in env


def test_port_forwarding_sets_provider_and_qbittorrent_hook(
    sample_config: Dict[str, Any], tmp_path: Path
) -> None:
    env = _gluetun_env(sample_config, tmp_path, port_forwarding=True)

    assert env["VPN_PORT_FORWARDING"] == "on"
    assert env["VPN_PORT_FORWARDING_PROVIDER"] == "protonvpn"
    hook = env["VPN_PORT_FORWARDING_UP_COMMAND"]
    # Gluetun substitutes {{PORTS}}; Jinja must leave it literal.
    assert '\\"listen_port\\":{{PORTS}}' in hook
    assert "http://127.0.0.1:8080/api/v2/app/setPreferences" in hook
    assert "--retry-connrefused" in hook
