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

        for stream in streams:
            codec_type = stream.get("codec_type", "")
            lang = stream.get("tags", {}).get("language", "und").lower()

            if codec_type == "video":
                has_video = True
            elif codec_type == "audio":
                audio_count += 1
                audio_langs.add(lang)
            elif codec_type == "subtitle":
                subtitle_count += 1
                subtitle_langs.add(lang)

        return StreamInfo(
            audio_languages=audio_langs,
            subtitle_languages=subtitle_langs,
            has_video=has_video,
            audio_count=audio_count,
            subtitle_count=subtitle_count,
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

    # Always include the main video track.
    args.extend(["-map", "0:v:0?"])

    if stream_info:
        # We know what streams exist, so map only those that match our selection
        wanted_audio = set(_normalized(selection.audio))
        wanted_subs = set(_normalized(selection.subtitles))

        # Always include 'und' (undefined) as fallback
        wanted_audio.add("und")
        wanted_subs.add("und")

        # Map audio streams that exist and match our selection
        matched_audio = wanted_audio & stream_info.audio_languages
        for lang in sorted(matched_audio):
            args.extend(["-map", f"0:a:m:language:{lang}"])

        # If no language-matched audio, include the first audio track as fallback
        if not matched_audio and stream_info.audio_count > 0:
            args.extend(["-map", "0:a:0"])

        # Map subtitle streams that exist and match our selection
        matched_subs = wanted_subs & stream_info.subtitle_languages
        for lang in sorted(matched_subs):
            args.extend(["-map", f"0:s:m:language:{lang}"])
    else:
        # Fallback: couldn't probe, copy all streams
        args.extend(["-map", "0:a", "-map", "0:s?"])

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


