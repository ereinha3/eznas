"""Rendering helpers for docker compose and environment files."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from jinja2 import Environment, FileSystemLoader, Template

from .models import RenderResult, StackConfig


def parse_wireguard_config(raw: str) -> Dict[str, str]:
    """Parse a raw WireGuard config into gluetun environment variables.

    Returns a dict with keys like ``private_key``, ``addresses``,
    ``dns``, ``public_key``, ``endpoint_ip``, ``endpoint_port``.
    """
    result: Dict[str, str] = {}
    if not raw.strip():
        return result

    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        if key == "PrivateKey":
            result["private_key"] = value
        elif key == "Address":
            # Strip IPv6 addresses — only keep IPv4
            addrs = [a.strip() for a in value.split(",")]
            ipv4 = [a for a in addrs if ":" not in a]
            result["addresses"] = ", ".join(ipv4) if ipv4 else value
        elif key == "DNS":
            dns_entries = [d.strip() for d in value.split(",")]
            ipv4_dns = [d for d in dns_entries if ":" not in d]
            parsed_dns = ", ".join(ipv4_dns) if ipv4_dns else value
            # ProtonVPN's internal DNS (10.2.0.1) sometimes fails to
            # respond through the tunnel.  Fall back to a privacy-
            # respecting public DNS — queries are still encrypted by
            # the WireGuard tunnel so the ISP cannot see them.
            if parsed_dns.startswith("10.2.0."):
                parsed_dns = "1.1.1.1"
            result["dns"] = parsed_dns
        elif key == "PublicKey":
            result["public_key"] = value
        elif key == "Endpoint":
            # Parse "IP:PORT" or "[IPv6]:PORT"
            m = re.match(r"^(.+):(\d+)$", value)
            if m:
                result["endpoint_ip"] = m.group(1)
                result["endpoint_port"] = m.group(2)
        elif key == "AllowedIPs":
            pass  # Gluetun handles routing internally

    return result


@dataclass
class SecretTemplate:
    """Represents a single secret file template."""

    relative_path: Path
    template: Template


@dataclass
class TemplateBundle:
    compose: Template
    env: Template
    secrets: List[SecretTemplate]


class ComposeRenderer:
    """Renders docker-compose and env files from Jinja templates."""

    def __init__(self, template_dir: Path) -> None:
        self.template_dir = template_dir
        self.env = Environment(loader=FileSystemLoader(str(template_dir)))

    @staticmethod
    def _get_config_host_path() -> Optional[str]:
        """
        Detect the host path for the config directory by inspecting the current container.
        Returns the directory path containing stack.yaml on the host, or None if not in a container.
        """
        try:
            # Quick check: are we in Docker at all?
            if not Path("/.dockerenv").exists():
                return None

            # Try common orchestrator container names
            for container_name in ["orchestrator-dev", "nas-orchestrator"]:
                result = subprocess.run(
                    ["docker", "inspect", container_name, "--format={{json .Mounts}}"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if result.returncode != 0:
                    continue

                mounts = json.loads(result.stdout)
                for mount in mounts:
                    # Look for the stack.yaml bind mount
                    if mount.get("Destination") == "/config/stack.yaml":
                        source = mount.get("Source", "")
                        # Return the parent directory
                        return str(Path(source).parent)
                    # Or look for /config directory mount
                    elif mount.get("Destination") == "/config" and mount.get("Type") == "bind":
                        return mount.get("Source")

            return None
        except Exception:
            # If anything fails, return None (not in a container or can't introspect)
            return None

    def load_templates(self) -> TemplateBundle:
        compose_template = self.env.get_template("docker-compose.yml.j2")
        env_template = self.env.get_template("env.j2")

        secrets_dir = self.template_dir / "secrets"
        secret_templates: List[SecretTemplate] = []

        if secrets_dir.exists():
            for template_path in sorted(secrets_dir.rglob("*.j2")):
                relative_template = template_path.relative_to(self.template_dir).as_posix()
                template = self.env.get_template(relative_template)
                output_rel_path = template_path.relative_to(secrets_dir).with_suffix("")
                secret_templates.append(
                    SecretTemplate(relative_path=output_rel_path, template=template)
                )

        return TemplateBundle(
            compose=compose_template,
            env=env_template,
            secrets=secret_templates,
        )

    def _build_context(
        self,
        config: StackConfig,
        secrets: Optional[dict[str, dict[str, str]]],
    ) -> dict:
        config_host_path = self._get_config_host_path()
        wg_parsed = parse_wireguard_config(config.services.gluetun.wireguard_config)
        return {
            "config": config.model_dump(mode="json"),
            "config_obj": config,
            "config_hash": config.model_dump_json(),
            "secrets": secrets or {},
            "config_host_path": config_host_path,
            "wg": wg_parsed,
        }

    def _write_secrets(
        self,
        templates: TemplateBundle,
        context: dict,
        output_dir: Path,
    ) -> tuple[Optional[Path], dict[str, Path]]:
        secret_paths: dict[str, Path] = {}
        secrets_dir_path: Optional[Path] = None

        if templates.secrets:
            secrets_dir_path = output_dir / ".secrets"
            secrets_dir_path.mkdir(parents=True, exist_ok=True)
            for secret in templates.secrets:
                target_path = secrets_dir_path / secret.relative_path
                target_path.parent.mkdir(parents=True, exist_ok=True)
                rendered_secret = secret.template.render(**context)
                target_path.write_text(rendered_secret)
                secret_paths[secret.relative_path.as_posix()] = target_path

        return secrets_dir_path, secret_paths

    def render(
        self,
        config: StackConfig,
        output_dir: Path,
        secrets: Optional[dict[str, dict[str, str]]] = None,
    ) -> RenderResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        templates = self.load_templates()
        context = self._build_context(config, secrets)
        compose_content = templates.compose.render(**context)
        env_content = templates.env.render(**context)
        compose_path = output_dir / "docker-compose.yml"
        env_path = output_dir / ".env"
        compose_path.write_text(compose_content)
        env_path.write_text(env_content)
        secrets_dir_path, secret_paths = self._write_secrets(templates, context, output_dir)

        return RenderResult(
            compose_path=compose_path,
            env_path=env_path,
            secrets_dir=secrets_dir_path,
            secret_files=secret_paths,
        )

    def render_secrets(
        self,
        config: StackConfig,
        output_dir: Path,
        secrets: Optional[dict[str, dict[str, str]]] = None,
    ) -> tuple[Optional[Path], dict[str, Path]]:
        output_dir.mkdir(parents=True, exist_ok=True)
        templates = self.load_templates()
        context = self._build_context(config, secrets)
        return self._write_secrets(templates, context, output_dir)

