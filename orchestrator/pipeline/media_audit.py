#!/usr/bin/env python3
"""Read-only Jellyfin media library audit.

The audit focuses on playback risks we have seen in this stack:
- duplicated/defaulted subtitles that slow startup or trigger burn-in
- image subtitles without text alternatives
- audio/video codecs that commonly force smart-TV transcoding
- very high average bitrate files
- files that would change under the active remux policy
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from orchestrator.models import StackConfig
from orchestrator.pipeline.languages import arr_language_to_iso
from orchestrator.pipeline.remux import (
    SubtitleIdentity,
    TrackSelection,
    detect_stream_policy_changes,
    normalize_language_code,
    subtitle_identity_from_srt_filename,
)
from orchestrator.pipeline.worker import selection_for_policy
from orchestrator.storage import ConfigRepository


VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".m4v",
    ".avi",
    ".mov",
    ".wmv",
    ".ts",
    ".m2ts",
}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt"}

IMAGE_SUBTITLE_CODECS = {
    "hdmv_pgs_subtitle",
    "dvd_subtitle",
    "dvb_subtitle",
    "xsub",
}
TEXT_SUBTITLE_CODECS = {
    "subrip",
    "srt",
    "ass",
    "ssa",
    "webvtt",
    "text",
    "hdmv_text_subtitle",
}
SMART_TV_AUDIO_TRANSCODE_RISK = {
    "truehd",
    "dts",
    "flac",
    "mlp",
    "pcm_bluray",
    "pcm_s16le",
    "pcm_s24le",
    "pcm_s32le",
    "alac",
}
SMART_TV_VIDEO_TRANSCODE_RISK = {
    "vc1",
    "mpeg2video",
    "mpeg4",
    "msmpeg4v3",
    "wmv3",
    "rv40",
    "theora",
}
SUBTITLE_FORCED_KEYWORDS = {
    "forced",
    "foreign only",
    "foreign parts",
    "foreign audio",
    "signs only",
    "signs/songs",
}
SUBTITLE_HI_KEYWORDS = {
    "sdh",
    "hearing impaired",
    "hearing-impaired",
    "hearing_impaired",
    "closed captions",
    "closed-caption",
    "captions",
    "cc",
}
COMMENTARY_KEYWORDS = {
    "commentary",
    "director",
    "filmmaker",
    "isolated",
    "descriptive",
    "audio description",
    "ad track",
    "visually impaired",
}

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}

BATCH_ORDER = [
    "pilot_known",
    "critical_subtitle",
    "track_hygiene",
    "codec_replace",
    "streaming_pressure",
]

BATCH_LABELS = {
    "pilot_known": "Pilot Known Issues",
    "critical_subtitle": "Critical Subtitles",
    "track_hygiene": "Track Hygiene",
    "codec_replace": "Codec Replacement",
    "streaming_pressure": "Streaming Pressure",
}

BATCH_DESCRIPTIONS = {
    "pilot_known": "Known problematic titles used to validate remediation before broad writes.",
    "critical_subtitle": "Critical default subtitle conditions, especially image defaults that force burn-in.",
    "track_hygiene": "Duplicate, defaulted, or unwanted audio/subtitle tracks fixable by lossless remux.",
    "codec_replace": "Video codec problems that need replacement or explicit encode work.",
    "streaming_pressure": "High bitrate, risky audio, HDR, or image subtitle cases that may need follow-up.",
}

REMUXABLE_BATCHES = {"pilot_known", "critical_subtitle", "track_hygiene"}

PILOT_TITLE_HINTS = {
    "28 days later",
    "austin powers international man of mystery",
    "austin powers the spy who shagged me",
}

TRACK_HYGIENE_CODES = {
    "policy_subtitle_remux_needed",
    "policy_audio_remux_needed",
    "default_subtitle",
    "duplicate_embedded_subtitle_identity",
    "duplicate_external_subtitle_identity",
    "embedded_external_subtitle_overlap",
    "multiple_default_audio",
    "duplicate_audio_language",
    "large_subtitle_count",
    "excessive_subtitle_count",
}

CODEC_REPLACE_CODES = {
    "video_codec_transcode_risk",
    "h264_high10",
}

STREAMING_PRESSURE_CODES = {
    "audio_codec_transcode_risk",
    "high_average_bitrate",
    "extreme_average_bitrate",
    "high_4k_bitrate",
    "dolby_vision",
    "image_subtitle_burn_in_risk",
    "image_subtitle_without_text_alternative",
}


@dataclass
class Issue:
    severity: str
    code: str
    message: str


@dataclass
class FileAudit:
    path: str
    category: str
    size_bytes: int
    duration_seconds: float | None = None
    average_bitrate_mbps: float | None = None
    container: str = ""
    video: list[dict[str, Any]] = field(default_factory=list)
    audio: list[dict[str, Any]] = field(default_factory=list)
    subtitles: list[dict[str, Any]] = field(default_factory=list)
    external_subtitles: list[dict[str, Any]] = field(default_factory=list)
    policy_changes: dict[str, list[str]] = field(
        default_factory=lambda: {"audio": [], "subtitles": []}
    )
    issues: list[Issue] = field(default_factory=list)


def _issue_codes(audit: FileAudit) -> set[str]:
    return {issue.code for issue in audit.issues}


def _issue_severity(audit: FileAudit, code: str) -> str | None:
    severities = [
        issue.severity
        for issue in audit.issues
        if issue.code == code
    ]
    if not severities:
        return None
    return min(severities, key=lambda value: SEVERITY_ORDER.get(value, 99))


def classify_batches(audit: FileAudit) -> list[str]:
    """Return remediation batches that apply to a file audit."""
    codes = _issue_codes(audit)
    batches: list[str] = []
    folded_path = audit.path.lower()
    compact_path = re.sub(r"[^a-z0-9]+", " ", folded_path)

    if any(hint in compact_path for hint in PILOT_TITLE_HINTS):
        batches.append("pilot_known")

    default_subtitle_severity = _issue_severity(audit, "default_subtitle")
    if default_subtitle_severity == "critical":
        batches.append("critical_subtitle")

    if codes & TRACK_HYGIENE_CODES:
        batches.append("track_hygiene")

    if codes & CODEC_REPLACE_CODES:
        batches.append("codec_replace")

    if codes & STREAMING_PRESSURE_CODES:
        batches.append("streaming_pressure")

    return [name for name in BATCH_ORDER if name in set(batches)]


def build_batch_summary(audits: list[FileAudit]) -> dict[str, dict[str, Any]]:
    """Summarize remediation batches for API/UI planning."""
    summary: dict[str, dict[str, Any]] = {
        name: {
            "name": name,
            "label": BATCH_LABELS[name],
            "description": BATCH_DESCRIPTIONS[name],
            "remuxable": name in REMUXABLE_BATCHES,
            "file_count": 0,
            "total_bytes": 0,
            "severity_counts": {},
            "issue_counts": {},
        }
        for name in BATCH_ORDER
    }

    issue_counts: dict[str, Counter[str]] = {
        name: Counter()
        for name in BATCH_ORDER
    }
    severity_counts: dict[str, Counter[str]] = {
        name: Counter()
        for name in BATCH_ORDER
    }

    for audit in audits:
        for batch in classify_batches(audit):
            summary[batch]["file_count"] += 1
            summary[batch]["total_bytes"] += audit.size_bytes
            severity_counts[batch][severity_for_file(audit)] += 1
            for issue in audit.issues:
                issue_counts[batch][issue.code] += 1

    for name in BATCH_ORDER:
        summary[name]["severity_counts"] = dict(severity_counts[name])
        summary[name]["issue_counts"] = dict(issue_counts[name].most_common())

    return summary


def keyword_in_text(text: str, keywords: set[str]) -> bool:
    folded = text.lower()
    return any(keyword in folded for keyword in keywords)


def float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def stream_lang(stream: dict[str, Any]) -> str:
    lang = (stream.get("tags", {}) or {}).get("language", "und") or "und"
    return normalize_language_code(str(lang).lower())


def subtitle_kind(stream: dict[str, Any]) -> str:
    disposition = stream.get("disposition", {}) or {}
    title = str((stream.get("tags", {}) or {}).get("title", "") or "")
    forced = bool(disposition.get("forced", 0)) or keyword_in_text(
        title,
        SUBTITLE_FORCED_KEYWORDS,
    )
    hearing_impaired = bool(
        disposition.get("hearing_impaired", 0)
    ) or keyword_in_text(title, SUBTITLE_HI_KEYWORDS)
    if forced:
        return "forced"
    if hearing_impaired:
        return "hearing_impaired"
    return "normal"


def subtitle_identity_from_ffprobe(stream: dict[str, Any]) -> SubtitleIdentity:
    return SubtitleIdentity(lang=stream_lang(stream), kind=subtitle_kind(stream))  # type: ignore[arg-type]


def issue_sort_key(issue: Issue) -> tuple[int, str, str]:
    return (SEVERITY_ORDER.get(issue.severity, 99), issue.code, issue.message)


def discover_video_files(media_root: Path, config: StackConfig) -> list[tuple[Path, str]]:
    categories = config.download_policy.categories
    roots = {
        categories.radarr: media_root / "movies",
        categories.sonarr: media_root / "tv",
        categories.anime: media_root / "anime",
    }
    files: list[tuple[Path, str]] = []
    for category, root in roots.items():
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS:
                files.append((path, category))
    return sorted(files, key=lambda item: str(item[0]).lower())


def policy_for_category(config: StackConfig, category: str) -> TrackSelection:
    categories = config.download_policy.categories
    if category == categories.anime:
        entry = config.media_policy.anime
    else:
        entry = config.media_policy.movies
    # Audits check existing files, which keep their own container.
    return selection_for_policy(entry)


def load_original_language_map(
    repo: ConfigRepository,
    config: StackConfig,
) -> dict[str, str]:
    try:
        secrets = repo.load_secrets()
    except PermissionError as exc:
        print(
            f"[media-audit] cannot read service secrets; skipping Arr metadata: {exc}",
            flush=True,
        )
        return {}
    mapping: dict[str, str] = {}

    def add_key(key: str, value: str) -> None:
        if key:
            mapping[key.lower()] = value

    def request_json(service: str, port: int, api_key: str, path: str) -> list[dict[str, Any]]:
        urls = [
            f"http://127.0.0.1:{port}{path}",
            f"http://localhost:{port}{path}",
            f"http://{service}:{port}{path}",
        ]
        headers = {"X-Api-Key": api_key}
        for url in urls:
            try:
                response = httpx.get(url, headers=headers, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                return data if isinstance(data, list) else []
            except Exception:
                continue
        return []

    radarr_key = secrets.get("radarr", {}).get("api_key")
    if radarr_key:
        movies = request_json(
            "radarr",
            config.services.radarr.port,
            radarr_key,
            "/api/v3/movie",
        )
        for movie in movies:
            lang_name = ((movie.get("originalLanguage") or {}).get("name") or "")
            iso_code = arr_language_to_iso(lang_name) if lang_name else None
            if not iso_code or iso_code == "und":
                continue
            for key in (movie.get("folderName"), movie.get("path"), movie.get("title")):
                if key:
                    add_key(str(key), iso_code)
                    add_key(Path(str(key)).name, iso_code)
            for alternate in movie.get("alternateTitles", []) or []:
                add_key(str(alternate.get("title", "")), iso_code)

    sonarr_key = secrets.get("sonarr", {}).get("api_key")
    if sonarr_key:
        series_list = request_json(
            "sonarr",
            config.services.sonarr.port,
            sonarr_key,
            "/api/v3/series",
        )
        for series in series_list:
            lang_name = ((series.get("originalLanguage") or {}).get("name") or "")
            iso_code = arr_language_to_iso(lang_name) if lang_name else None
            if not iso_code or iso_code == "und":
                continue
            for key in (series.get("path"), series.get("title")):
                if key:
                    add_key(str(key), iso_code)
                    add_key(Path(str(key)).name, iso_code)
            for alternate in series.get("alternateTitles", []) or []:
                add_key(str(alternate.get("title", "")), iso_code)

    return mapping


def original_language_for_path(path: Path, mapping: dict[str, str]) -> str | None:
    candidates = [
        str(path.parent),
        str(path.parent.parent),
        path.parent.name,
        path.parent.parent.name,
    ]
    for candidate in candidates:
        found = mapping.get(candidate.lower())
        if found:
            return found

    title = path.parent.name.lower()
    cleaned = title
    for token in (" remastered", " remux", " bdremux", " extended", " unrated"):
        cleaned = cleaned.replace(token, "")
    cleaned = cleaned.strip()
    if cleaned in mapping:
        return mapping[cleaned]

    for cached_title, lang in mapping.items():
        if len(cached_title) >= 5 and cached_title in title:
            return lang
    return None


def probe(path: Path, timeout: int) -> tuple[dict[str, Any] | None, str | None]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"ffprobe timed out after {timeout}s"
    except OSError as exc:
        return None, f"ffprobe failed to start: {exc}"
    if result.returncode != 0:
        stderr = result.stderr.strip().splitlines()
        return None, stderr[-1] if stderr else f"ffprobe exited {result.returncode}"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"ffprobe returned invalid JSON: {exc}"


def external_subtitles_for(path: Path) -> list[dict[str, Any]]:
    external: list[dict[str, Any]] = []
    for candidate in path.parent.iterdir():
        if not candidate.is_file() or candidate.suffix.lower() not in SUBTITLE_EXTENSIONS:
            continue
        if not candidate.stem.startswith(path.stem):
            continue
        identity = subtitle_identity_from_srt_filename(candidate, path.stem)
        external.append(
            {
                "path": str(candidate),
                "language": identity.lang,
                "kind": identity.kind,
                "size_bytes": candidate.stat().st_size,
            }
        )
    return sorted(external, key=lambda item: item["path"].lower())


def summarize_streams(streams: list[dict[str, Any]]) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    videos: list[dict[str, Any]] = []
    audios: list[dict[str, Any]] = []
    subtitles: list[dict[str, Any]] = []
    for stream in streams:
        codec_type = stream.get("codec_type")
        tags = stream.get("tags", {}) or {}
        disposition = stream.get("disposition", {}) or {}
        if codec_type == "video":
            videos.append(
                {
                    "index": stream.get("index"),
                    "codec": stream.get("codec_name", ""),
                    "profile": stream.get("profile", ""),
                    "width": stream.get("width"),
                    "height": stream.get("height"),
                    "pix_fmt": stream.get("pix_fmt", ""),
                    "color_transfer": stream.get("color_transfer", ""),
                    "color_primaries": stream.get("color_primaries", ""),
                    "codec_tag_string": stream.get("codec_tag_string", ""),
                    "bit_rate": int_or_zero(stream.get("bit_rate")),
                }
            )
        elif codec_type == "audio":
            audios.append(
                {
                    "index": stream.get("index"),
                    "language": stream_lang(stream),
                    "codec": stream.get("codec_name", ""),
                    "profile": stream.get("profile", ""),
                    "channels": int_or_zero(stream.get("channels")),
                    "bit_rate": int_or_zero(stream.get("bit_rate")),
                    "title": tags.get("title", ""),
                    "default": bool(disposition.get("default", 0)),
                    "commentary": keyword_in_text(
                        str(tags.get("title", "")),
                        COMMENTARY_KEYWORDS,
                    ),
                }
            )
        elif codec_type == "subtitle":
            subtitles.append(
                {
                    "index": stream.get("index"),
                    "language": stream_lang(stream),
                    "kind": subtitle_kind(stream),
                    "codec": stream.get("codec_name", ""),
                    "title": tags.get("title", ""),
                    "default": bool(disposition.get("default", 0)),
                    "forced": bool(disposition.get("forced", 0)),
                    "hearing_impaired": bool(disposition.get("hearing_impaired", 0)),
                    "text": stream.get("codec_name", "") in TEXT_SUBTITLE_CODECS,
                    "image": stream.get("codec_name", "") in IMAGE_SUBTITLE_CODECS,
                }
            )
    return videos, audios, subtitles


def audit_file(
    path: Path,
    category: str,
    config: StackConfig,
    original_languages: dict[str, str],
    timeout: int,
) -> FileAudit:
    size = path.stat().st_size
    audit = FileAudit(path=str(path), category=category, size_bytes=size)
    data, error = probe(path, timeout)
    if error:
        audit.issues.append(Issue("critical", "probe_failed", error))
        return audit

    assert data is not None
    streams = data.get("streams", []) or []
    fmt = data.get("format", {}) or {}
    audit.container = fmt.get("format_name", "") or ""
    duration = float_or_none(fmt.get("duration"))
    audit.duration_seconds = duration
    if duration:
        audit.average_bitrate_mbps = round((size * 8) / duration / 1_000_000, 2)

    audit.video, audit.audio, audit.subtitles = summarize_streams(streams)
    audit.external_subtitles = external_subtitles_for(path)
    issues = audit.issues

    if not audit.video:
        issues.append(Issue("critical", "no_video", "No video stream detected"))
    if not audit.audio:
        issues.append(Issue("critical", "no_audio", "No audio stream detected"))

    selection = policy_for_category(config, category)
    original_language = original_language_for_path(path, original_languages)
    unwanted_audio, unwanted_subtitles = detect_stream_policy_changes(
        streams,
        selection,
        original_language=original_language,
    )
    audit.policy_changes = {
        "audio": unwanted_audio,
        "subtitles": unwanted_subtitles,
    }
    if unwanted_subtitles:
        issues.append(
            Issue(
                "high",
                "policy_subtitle_remux_needed",
                f"{len(unwanted_subtitles)} subtitle stream(s) would be changed",
            )
        )
    if unwanted_audio:
        issues.append(
            Issue(
                "medium",
                "policy_audio_remux_needed",
                f"{len(unwanted_audio)} audio stream(s) would be changed",
            )
        )

    video = audit.video[0] if audit.video else {}
    video_codec = str(video.get("codec", "")).lower()
    video_profile = str(video.get("profile", "")).lower()
    pix_fmt = str(video.get("pix_fmt", "")).lower()
    height = int_or_zero(video.get("height"))
    if video_codec in SMART_TV_VIDEO_TRANSCODE_RISK:
        issues.append(
            Issue(
                "high",
                "video_codec_transcode_risk",
                f"{video_codec} video commonly requires transcoding on smart-TV clients",
            )
        )
    if video_codec == "h264" and ("10" in video_profile or "10" in pix_fmt):
        issues.append(
            Issue(
                "high",
                "h264_high10",
                "H.264 10-bit is a common direct-play failure mode",
            )
        )
    if video_codec == "hevc" and "10" in pix_fmt:
        issues.append(
            Issue(
                "info",
                "hevc_10bit",
                "HEVC 10-bit should direct-play on newer 4K clients but is client-specific",
            )
        )
    if any(
        "DOVI" in json.dumps(stream.get("side_data_list", []))
        or "dv" in str(stream.get("codec_tag_string", "")).lower()
        for stream in streams
        if stream.get("codec_type") == "video"
    ):
        issues.append(
            Issue(
                "medium",
                "dolby_vision",
                "Dolby Vision compatibility varies across Fire TV/Roku clients",
            )
        )
    if audit.average_bitrate_mbps is not None:
        if audit.average_bitrate_mbps >= 120:
            issues.append(
                Issue(
                    "critical",
                    "extreme_average_bitrate",
                    f"Average bitrate is {audit.average_bitrate_mbps} Mbps",
                )
            )
        elif audit.average_bitrate_mbps >= 80:
            issues.append(
                Issue(
                    "high",
                    "high_average_bitrate",
                    f"Average bitrate is {audit.average_bitrate_mbps} Mbps",
                )
            )
        elif height >= 2160 and audit.average_bitrate_mbps >= 55:
            issues.append(
                Issue(
                    "medium",
                    "high_4k_bitrate",
                    f"4K average bitrate is {audit.average_bitrate_mbps} Mbps",
                )
            )

    audio_codecs = Counter(str(item["codec"]).lower() for item in audit.audio)
    risky_audio = sorted(codec for codec in audio_codecs if codec in SMART_TV_AUDIO_TRANSCODE_RISK)
    if risky_audio:
        issues.append(
            Issue(
                "medium",
                "audio_codec_transcode_risk",
                "Audio codec(s) likely to force transcoding: " + ", ".join(risky_audio),
            )
        )
    default_audio = [item for item in audit.audio if item["default"]]
    if len(default_audio) > 1:
        issues.append(
            Issue(
                "medium",
                "multiple_default_audio",
                f"{len(default_audio)} audio tracks are marked default",
            )
        )
    non_commentary_audio_langs = [
        item["language"] for item in audit.audio if not item.get("commentary")
    ]
    for lang, count in Counter(non_commentary_audio_langs).items():
        if count > 1:
            issues.append(
                Issue(
                    "medium",
                    "duplicate_audio_language",
                    f"{count} non-commentary audio tracks share language {lang}",
                )
            )

    if len(audit.subtitles) > 12:
        issues.append(
            Issue(
                "high",
                "excessive_subtitle_count",
                f"{len(audit.subtitles)} embedded subtitle tracks",
            )
        )
    elif len(audit.subtitles) > 6:
        issues.append(
            Issue(
                "medium",
                "large_subtitle_count",
                f"{len(audit.subtitles)} embedded subtitle tracks",
            )
        )

    embedded_identities = [subtitle_identity_from_ffprobe(stream) for stream in streams if stream.get("codec_type") == "subtitle"]
    for identity, count in Counter(embedded_identities).items():
        if count > 1:
            severity = "high" if count >= 4 else "medium"
            issues.append(
                Issue(
                    severity,
                    "duplicate_embedded_subtitle_identity",
                    f"{count} embedded subtitles share {identity.lang}/{identity.kind}",
                )
            )

    default_subtitles = [item for item in audit.subtitles if item["default"]]
    if default_subtitles:
        image_defaults = [item for item in default_subtitles if item["image"]]
        severity = "critical" if image_defaults else "high"
        issues.append(
            Issue(
                severity,
                "default_subtitle",
                f"{len(default_subtitles)} subtitle track(s) marked default",
            )
        )

    image_subtitles = [item for item in audit.subtitles if item["image"]]
    if image_subtitles:
        image_forced = [item for item in image_subtitles if item["kind"] == "forced"]
        severity = "high" if image_forced else "medium"
        issues.append(
            Issue(
                severity,
                "image_subtitle_burn_in_risk",
                f"{len(image_subtitles)} image subtitle track(s) present",
            )
        )

    text_identities = {
        (item["language"], item["kind"])
        for item in audit.subtitles
        if item["text"]
    }
    for item in image_subtitles:
        identity = (item["language"], item["kind"])
        if identity not in text_identities:
            issues.append(
                Issue(
                    "medium",
                    "image_subtitle_without_text_alternative",
                    f"{item['language']}/{item['kind']} has no text subtitle alternative",
                )
            )

    external_identities = [
        SubtitleIdentity(lang=item["language"], kind=item["kind"])
        for item in audit.external_subtitles
    ]
    for identity, count in Counter(external_identities).items():
        if count > 1:
            issues.append(
                Issue(
                    "medium",
                    "duplicate_external_subtitle_identity",
                    f"{count} external subtitles share {identity.lang}/{identity.kind}",
                )
            )

    combined_counts = Counter(embedded_identities + external_identities)
    for identity, count in combined_counts.items():
        if count > 1 and identity in external_identities:
            issues.append(
                Issue(
                    "medium",
                    "embedded_external_subtitle_overlap",
                    f"{count} total subtitles share {identity.lang}/{identity.kind}",
                )
            )

    audit.issues = sorted(issues, key=issue_sort_key)
    return audit


def severity_for_file(audit: FileAudit) -> str:
    if not audit.issues:
        return "clean"
    return min(audit.issues, key=lambda issue: SEVERITY_ORDER.get(issue.severity, 99)).severity


def build_summary(audits: list[FileAudit], started_at: str, elapsed_seconds: float) -> dict[str, Any]:
    severity_counts = Counter(severity_for_file(audit) for audit in audits)
    issue_counts: Counter[str] = Counter()
    issue_counts_by_severity: dict[str, Counter[str]] = defaultdict(Counter)
    issue_severity: dict[str, str] = {}
    category_counts = Counter(audit.category for audit in audits)
    category_issue_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for audit in audits:
        for issue in audit.issues:
            issue_counts[issue.code] += 1
            issue_counts_by_severity[issue.code][issue.severity] += 1
            current = issue_severity.get(issue.code)
            if current is None or SEVERITY_ORDER[issue.severity] < SEVERITY_ORDER[current]:
                issue_severity[issue.code] = issue.severity
            category_issue_counts[audit.category][issue.code] += 1

    return {
        "started_at": started_at,
        "elapsed_seconds": round(elapsed_seconds, 2),
        "total_files": len(audits),
        "severity_counts": dict(severity_counts),
        "category_counts": dict(category_counts),
        "issue_counts": dict(issue_counts.most_common()),
        "issue_counts_by_severity": {
            code: dict(counter)
            for code, counter in issue_counts_by_severity.items()
        },
        "issue_severity": issue_severity,
        "category_issue_counts": {
            category: dict(counter.most_common())
            for category, counter in category_issue_counts.items()
        },
        "batches": build_batch_summary(audits),
    }


def top_files(audits: list[FileAudit], limit: int) -> list[FileAudit]:
    def key(audit: FileAudit) -> tuple[int, int, float, str]:
        sev = SEVERITY_ORDER.get(severity_for_file(audit), 99)
        return (
            sev,
            -len(audit.issues),
            -(audit.average_bitrate_mbps or 0),
            audit.path,
        )

    return sorted([audit for audit in audits if audit.issues], key=key)[:limit]


def write_markdown(
    path: Path,
    summary: dict[str, Any],
    audits: list[FileAudit],
    limit: int,
) -> None:
    lines = [
        "# Media Audit",
        "",
        f"- Started: `{summary['started_at']}`",
        f"- Duration: `{summary['elapsed_seconds']}s`",
        f"- Files scanned: `{summary['total_files']}`",
        "",
        "## Severity",
        "",
    ]
    for severity in ("critical", "high", "medium", "low", "info", "clean"):
        count = summary["severity_counts"].get(severity, 0)
        lines.append(f"- {severity}: `{count}`")

    lines.extend(["", "## Issue Counts", ""])
    for code, count in summary["issue_counts"].items():
        severity = summary["issue_severity"].get(code, "")
        by_severity = summary.get("issue_counts_by_severity", {}).get(code, {})
        severity_detail = ", ".join(
            f"{name}={by_severity[name]}"
            for name in ("critical", "high", "medium", "low", "info")
            if by_severity.get(name)
        )
        label = f"{severity}; {severity_detail}" if severity_detail else severity
        lines.append(f"- `{code}` ({label}): `{count}`")

    lines.extend(["", "## Remediation Batches", ""])
    for name in BATCH_ORDER:
        batch = summary.get("batches", {}).get(name, {})
        label = batch.get("label", BATCH_LABELS[name])
        count = batch.get("file_count", 0)
        size_tb = batch.get("total_bytes", 0) / 1_000_000_000_000
        remuxable = "lossless remux" if batch.get("remuxable") else "replacement/encode"
        lines.append(f"- `{name}` ({label}, {remuxable}): `{count}` files, `{size_tb:.3f} TB`")

    lines.extend(["", f"## Top {limit} Files", ""])
    for audit in top_files(audits, limit):
        lines.append(f"### {severity_for_file(audit).upper()} - `{audit.path}`")
        if audit.duration_seconds:
            minutes = round(audit.duration_seconds / 60, 1)
            bitrate = audit.average_bitrate_mbps
            lines.append(f"- Duration: `{minutes} min`, average bitrate: `{bitrate} Mbps`")
        if audit.video:
            video = audit.video[0]
            lines.append(
                "- Video: "
                f"`{video.get('codec')}` `{video.get('profile')}` "
                f"`{video.get('width')}x{video.get('height')}` `{video.get('pix_fmt')}`"
            )
        if audit.audio:
            audio_summary = Counter(item["codec"] for item in audit.audio)
            lines.append(
                "- Audio: "
                + ", ".join(f"`{codec}` x{count}" for codec, count in audio_summary.items())
            )
        if audit.subtitles:
            sub_summary = Counter(
                f"{item['language']}/{item['kind']}/{item['codec']}"
                for item in audit.subtitles
            )
            lines.append(
                "- Subtitles: "
                + ", ".join(f"`{key}` x{count}" for key, count in sub_summary.items())
            )
        for issue in audit.issues[:8]:
            lines.append(f"- {issue.severity}: `{issue.code}` - {issue.message}")
        if audit.policy_changes["audio"] or audit.policy_changes["subtitles"]:
            if audit.policy_changes["audio"]:
                lines.append(
                    "- Policy audio changes: "
                    + "; ".join(audit.policy_changes["audio"][:5])
                )
            if audit.policy_changes["subtitles"]:
                lines.append(
                    "- Policy subtitle changes: "
                    + "; ".join(audit.policy_changes["subtitles"][:5])
                )
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def dataclass_to_json(obj: Any) -> Any:
    if isinstance(obj, FileAudit):
        return {
            "path": obj.path,
            "category": obj.category,
            "size_bytes": obj.size_bytes,
            "duration_seconds": obj.duration_seconds,
            "average_bitrate_mbps": obj.average_bitrate_mbps,
            "container": obj.container,
            "video": obj.video,
            "audio": obj.audio,
            "subtitles": obj.subtitles,
            "external_subtitles": obj.external_subtitles,
            "policy_changes": obj.policy_changes,
            "batches": classify_batches(obj),
            "issues": [dataclass_to_json(issue) for issue in obj.issues],
        }
    if isinstance(obj, Issue):
        return {
            "severity": obj.severity,
            "code": obj.code,
            "message": obj.message,
        }
    raise TypeError(f"Object is not JSON serializable: {obj!r}")


def issue_from_json(payload: dict[str, Any]) -> Issue:
    return Issue(
        severity=str(payload.get("severity", "info")),
        code=str(payload.get("code", "unknown")),
        message=str(payload.get("message", "")),
    )


def file_audit_from_json(payload: dict[str, Any]) -> FileAudit:
    return FileAudit(
        path=str(payload.get("path", "")),
        category=str(payload.get("category", "")),
        size_bytes=int(payload.get("size_bytes", 0) or 0),
        duration_seconds=payload.get("duration_seconds"),
        average_bitrate_mbps=payload.get("average_bitrate_mbps"),
        container=str(payload.get("container", "")),
        video=list(payload.get("video", []) or []),
        audio=list(payload.get("audio", []) or []),
        subtitles=list(payload.get("subtitles", []) or []),
        external_subtitles=list(payload.get("external_subtitles", []) or []),
        policy_changes=dict(
            payload.get("policy_changes", {"audio": [], "subtitles": []})
            or {"audio": [], "subtitles": []}
        ),
        issues=[issue_from_json(item) for item in payload.get("issues", []) or []],
    )


def find_latest_audit_json(repo_root: Path) -> Path | None:
    audit_dir = repo_root / "generated" / "media_audits"
    if not audit_dir.exists():
        return None
    candidates = sorted(
        audit_dir.glob("media-audit-*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load_audit_report(path: Path) -> dict[str, Any]:
    """Load an audit JSON file and backfill batch metadata for old reports."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    audits = [file_audit_from_json(item) for item in payload.get("files", []) or []]
    summary = dict(payload.get("summary", {}) or {})
    summary["batches"] = build_batch_summary(audits)
    files = [dataclass_to_json(audit) for audit in audits]
    return {
        "summary": summary,
        "files": files,
        "json_path": str(path),
        "markdown_path": str(path.with_suffix(".md")),
    }


def persist_latest_audit(
    repo: ConfigRepository,
    *,
    json_path: Path,
    markdown_path: Path,
    summary: dict[str, Any],
) -> None:
    if repo.read_only:
        return
    pipeline = repo.load_pipeline_state()
    pipeline["media_audit"] = {
        "latest_json": str(json_path),
        "latest_markdown": str(markdown_path),
        "summary": summary,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    repo.save_pipeline_state(pipeline)


def run_media_audit(
    *,
    repo_root: Path,
    media_root: Path | None = None,
    workers: int = 6,
    timeout: int = 45,
    top: int = 80,
    json_out: Path | None = None,
    markdown_out: Path | None = None,
    persist: bool = False,
) -> dict[str, Any]:
    """Run a media audit and return JSON-serializable report metadata."""
    repo_root = repo_root.resolve()
    repo = ConfigRepository(repo_root, read_only=not persist)
    config = repo.load_stack()
    resolved_media_root = (media_root or config.paths.pool).resolve()
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()

    files = discover_video_files(resolved_media_root, config)
    original_languages = load_original_language_map(repo, config)
    print(
        f"[media-audit] scanning {len(files)} files under {resolved_media_root} "
        f"with {workers} workers",
        flush=True,
    )
    print(
        f"[media-audit] loaded {len(original_languages)} original-language keys "
        "from Arr metadata",
        flush=True,
    )

    audits: list[FileAudit] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(
                audit_file,
                path,
                category,
                config,
                original_languages,
                timeout,
            )
            for path, category in files
        ]
        for index, future in enumerate(as_completed(futures), start=1):
            audits.append(future.result())
            if index == len(futures) or index % 100 == 0:
                print(f"[media-audit] probed {index}/{len(futures)}", flush=True)

    audits.sort(key=lambda audit: audit.path.lower())
    elapsed = time.monotonic() - started
    summary = build_summary(audits, started_at, elapsed)

    out_dir = repo_root / "generated" / "media_audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    resolved_json_out = json_out or out_dir / f"media-audit-{stamp}.json"
    resolved_markdown_out = markdown_out or out_dir / f"media-audit-{stamp}.md"

    resolved_json_out.write_text(
        json.dumps(
            {
                "summary": summary,
                "files": audits,
            },
            default=dataclass_to_json,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_markdown(resolved_markdown_out, summary, audits, top)
    persist_latest_audit(
        repo,
        json_path=resolved_json_out,
        markdown_path=resolved_markdown_out,
        summary=summary,
    )

    print(f"[media-audit] wrote JSON: {resolved_json_out}", flush=True)
    print(f"[media-audit] wrote Markdown: {resolved_markdown_out}", flush=True)
    print(
        "[media-audit] severity counts: " + json.dumps(summary["severity_counts"]),
        flush=True,
    )
    print(
        "[media-audit] top issue counts: "
        + json.dumps(dict(list(summary["issue_counts"].items())[:12])),
        flush=True,
    )

    return {
        "summary": summary,
        "files": [dataclass_to_json(audit) for audit in audits],
        "json_path": str(resolved_json_out),
        "markdown_path": str(resolved_markdown_out),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--media-root", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--top", type=int, default=80)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_media_audit(
        repo_root=args.repo_root,
        media_root=args.media_root,
        workers=args.workers,
        timeout=args.timeout,
        top=args.top,
        json_out=args.json_out,
        markdown_out=args.markdown_out,
        persist=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
