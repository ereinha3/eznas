"""Rendering helpers for docker compose and environment files."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from jinja2 import Environment, FileSystemLoader, Template

from .models import RenderResult, StackConfig


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
        return {
            "config": config.model_dump(mode="json"),
            "config_obj": config,
            "config_hash": config.model_dump_json(),
            "secrets": secrets or {},
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

