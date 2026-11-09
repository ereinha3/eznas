"""Staged media remediation planning helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..storage import ConfigRepository
from .media_audit import (
    BATCH_DESCRIPTIONS,
    BATCH_LABELS,
    BATCH_ORDER,
    REMUXABLE_BATCHES,
    find_latest_audit_json,
    load_audit_report,
)


def latest_audit_path(repo: ConfigRepository, repo_root: Path) -> Path | None:
    """Return the latest audit path from durable state or generated reports."""
    try:
        pipeline = repo.load_pipeline_state()
    except PermissionError:
        pipeline = {}
    configured = (pipeline.get("media_audit") or {}).get("latest_json")
    if configured:
        path = Path(configured)
        if path.exists():
            return path
    return find_latest_audit_json(repo_root)


def load_latest_audit(repo: ConfigRepository, repo_root: Path) -> dict[str, Any] | None:
    path = latest_audit_path(repo, repo_root)
    if path is None:
        return None
    return load_audit_report(path)


def build_remediation_plan(report: dict[str, Any]) -> dict[str, Any]:
    """Build UI/API remediation batch metadata from an audit report."""
    summary = report.get("summary", {}) or {}
    batch_summary = summary.get("batches", {}) or {}
    batches: list[dict[str, Any]] = []

    for name in BATCH_ORDER:
        raw = dict(batch_summary.get(name, {}) or {})
        file_count = int(raw.get("file_count", 0) or 0)
        total_bytes = int(raw.get("total_bytes", 0) or 0)
        mode = "lossless_remux" if name in REMUXABLE_BATCHES else "replace_or_encode"
        batches.append(
            {
                "name": name,
                "label": raw.get("label") or BATCH_LABELS[name],
                "description": raw.get("description") or BATCH_DESCRIPTIONS[name],
                "mode": mode,
                "remuxable": name in REMUXABLE_BATCHES,
                "file_count": file_count,
                "total_bytes": total_bytes,
                "severity_counts": raw.get("severity_counts", {}) or {},
                "issue_counts": raw.get("issue_counts", {}) or {},
            }
        )

    return {
        "audit_path": report.get("json_path"),
        "markdown_path": report.get("markdown_path"),
        "started_at": summary.get("started_at"),
        "total_files": summary.get("total_files", 0),
        "batches": batches,
    }


def target_paths_for_batch(report: dict[str, Any], batch_name: str) -> list[str]:
    if batch_name not in BATCH_ORDER:
        raise ValueError(f"Unknown remediation batch: {batch_name}")
    paths: list[str] = []
    for item in report.get("files", []) or []:
        batches = item.get("batches", []) or []
        if batch_name in batches:
            paths.append(str(item.get("path", "")))
    return [path for path in paths if path]
