"""Utilities for building ffmpeg remux commands with language guardrails."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


@dataclass
class TrackSelection:
    audio: Sequence[str]
    subtitles: Sequence[str]


@dataclass
class StreamInfo:
    """Information about streams in a media file."""
    audio_languages: Set[str]
    subtitle_languages: Set[str]
    has_video: bool
    audio_count: int
    subtitle_count: int
    original_language: Optional[str] = None  # Language of first audio track


def probe_streams(source: Path) -> Optional[StreamInfo]:
    """Probe a media file to discover available streams and their languages.

    Returns None if probing fails.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None

        data = json.loads(result.stdout)
        streams = data.get("streams", [])

        audio_langs: Set[str] = set()
        subtitle_langs: Set[str] = set()
        has_video = False
        audio_count = 0
        subtitle_count = 0
        original_language: Optional[str] = None

        for stream in streams:
            codec_type = stream.get("codec_type", "")
            lang = stream.get("tags", {}).get("language", "und").lower()

            if codec_type == "video":
                has_video = True
            elif codec_type == "audio":
                audio_count += 1
                audio_langs.add(lang)
                # Capture the first audio track's language as the original
                if original_language is None:
                    original_language = lang
            elif codec_type == "subtitle":
                subtitle_count += 1
                subtitle_langs.add(lang)

        return StreamInfo(
            audio_languages=audio_langs,
            subtitle_languages=subtitle_langs,
            has_video=has_video,
            audio_count=audio_count,
            subtitle_count=subtitle_count,
            original_language=original_language,
        )
    except Exception:
        return None


def build_ffmpeg_command(
    source: Path,
    destination: Path,
    selection: TrackSelection,
) -> List[str]:
    """Construct an ffmpeg command that remuxes while stripping unwanted tracks.

    This uses `-c copy` to avoid transcoding while removing tracks that are not
    included in the provided language allowlists. The function probes the source
    file first to only map tracks that actually exist, avoiding FFmpeg errors
    with metadata-based stream specifiers.
    """
    args: List[str] = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(source),
    ]

    # Probe the source to find available streams
    stream_info = probe_streams(source)

    # Always include the main video track
    args.extend(["-map", "0:v:0?"])

    # Build language filters
    keep_audio_langs = set(_normalized(selection.audio))
    keep_sub_langs = set(_normalized(selection.subtitles))

    # Check if "forced" is specified for subtitles (it's a special flag, not a language)
    include_forced_subs = "forced" in keep_sub_langs
    if include_forced_subs:
        keep_sub_langs.discard("forced")  # Remove it from language list

    # Always include original language audio if available
    if stream_info and stream_info.original_language:
        keep_audio_langs.add(stream_info.original_language.lower())

    # Get detailed stream info to filter by index
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            streams = data.get("streams", [])

            # Map audio streams by index
            for stream in streams:
                if stream.get("codec_type") == "audio":
                    lang = stream.get("tags", {}).get("language", "und").lower()
                    if lang in keep_audio_langs or lang == "und":
                        idx = stream.get("index")
                        args.extend(["-map", f"0:{idx}"])

            # Map subtitle streams by index
            for stream in streams:
                if stream.get("codec_type") == "subtitle":
                    lang = stream.get("tags", {}).get("language", "und").lower()
                    disposition = stream.get("disposition", {})
                    is_forced = disposition.get("forced", 0) == 1

                    # Include if: language matches OR (forced flag is set AND user wants forced subs)
                    if lang in keep_sub_langs or (is_forced and include_forced_subs):
                        idx = stream.get("index")
                        args.extend(["-map", f"0:{idx}"])
        else:
            # Fallback: copy all streams if probing fails
            args.extend(["-map", "0:a?", "-map", "0:s?"])
    except Exception:
        # Fallback: copy all streams if filtering fails
        args.extend(["-map", "0:a?", "-map", "0:s?"])

    args.extend(
        [
            "-c",
            "copy",
            str(destination),
        ]
    )
    return args


def _normalized(languages: Iterable[str]) -> List[str]:
    """Return ISO language codes in lowercase without duplicates."""
    seen = set()
    result: List[str] = []
    for entry in languages:
        code = entry.lower().strip()
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(code)
    return result


