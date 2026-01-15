"""Helpers for managing Traefik proxy assets."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Tuple

from .models import StackConfig
from .storage import ConfigRepository


def ensure_traefik_assets(
    repo: ConfigRepository, config: StackConfig
) -> Tuple[bool, str]:
    """Ensure TLS assets exist for Traefik when HTTPS is enabled."""
    if not config.proxy.enabled:
        return False, "skipped (proxy disabled)"
    if config.proxy.https_port is None:
        return False, "skipped (https disabled)"

    traefik_dir = Path(config.paths.appdata) / "traefik"
    certs_dir = traefik_dir / "certs"
    certs_dir.mkdir(parents=True, exist_ok=True)

    cert_path = certs_dir / "local.crt"
    key_path = certs_dir / "local.key"
    metadata_path = certs_dir / "metadata.json"
    tls_config_path = traefik_dir / "tls.yml"

    hostnames = _collect_proxy_hostnames(config)
    if not hostnames:
        hostnames = ["nas-orchestrator.local"]

    changed = False

    if _ensure_self_signed_cert(cert_path, key_path, metadata_path, hostnames):
        changed = True

    if _ensure_tls_config(tls_config_path, cert_path, key_path):
        changed = True

    detail_hostnames = ", ".join(hostnames)
    detail = f"tls assets ready ({detail_hostnames})"
    return changed, detail


def _collect_proxy_hostnames(config: StackConfig) -> List[str]:
    services = config.services
    candidates: Iterable[str | None] = (
        services.qbittorrent.proxy_url,
        services.radarr.proxy_url,
        services.sonarr.proxy_url,
        services.prowlarr.proxy_url,
        services.jellyseerr.proxy_url,
        services.jellyfin.proxy_url,
        services.pipeline.proxy_url,
    )
    hostnames = {
        value.strip()
        for value in candidates
        if isinstance(value, str) and value.strip()
    }
    return sorted(hostnames)


def _ensure_self_signed_cert(
    cert_path: Path,
    key_path: Path,
    metadata_path: Path,
    hostnames: List[str],
) -> bool:
    metadata = {"hostnames": hostnames}
    if (
        cert_path.exists()
        and key_path.exists()
        and metadata_path.exists()
    ):
        try:
            current = json.loads(metadata_path.read_text())
        except json.JSONDecodeError:
            current = {}
        if current.get("hostnames") == hostnames:
            return False

    openssl = shutil.which("openssl")
    if not openssl:
        raise RuntimeError("openssl binary not found; required for self-signed cert generation")

    san = ",".join(f"DNS:{hostname}" for hostname in hostnames)
    subject = f"/CN={hostnames[0]}"

    command = [
        openssl,
        "req",
        "-x509",
        "-newkey",
        "rsa:4096",
        "-sha256",
        "-days",
        "825",
        "-nodes",
        "-keyout",
        str(key_path),
        "-out",
        str(cert_path),
        "-subj",
        subject,
        "-addext",
        f"subjectAltName={san}",
    ]

    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"openssl failed ({stderr or 'unknown error'})")

    metadata_path.write_text(json.dumps(metadata, indent=2))
    return True


def _ensure_tls_config(tls_path: Path, cert_path: Path, key_path: Path) -> bool:
    config_text = (
        "tls:\n"
        "  certificates:\n"
        "    - certFile: /config/certs/local.crt\n"
        "      keyFile: /config/certs/local.key\n"
        "  stores:\n"
        "    default:\n"
        "      defaultCertificate:\n"
        "        certFile: /config/certs/local.crt\n"
        "        keyFile: /config/certs/local.key\n"
    )
    if tls_path.exists() and tls_path.read_text() == config_text:
        return False
    tls_path.write_text(config_text)
    return True

