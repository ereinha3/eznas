"""Utilities for building ffmpeg remux commands with language guardrails."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence


@dataclass
class TrackSelection:
    audio: Sequence[str]
    subtitles: Sequence[str]


def build_ffmpeg_command(
    source: Path,
    destination: Path,
    selection: TrackSelection,
) -> List[str]:
    """Construct an ffmpeg command that remuxes while stripping unwanted tracks.

    This uses `-c copy` to avoid transcoding while removing tracks that are not
    included in the provided language allowlists. The caller is responsible for
    probing available streams and ensuring at least one audio track remains.
    """
    args: List[str] = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(source),
    ]

    # Always include the main video track.
    args.extend(["-map", "0:v:0?"])

    # Copy audio streams that match the allowed language codes.
    for lang in _normalized(selection.audio):
        args.extend(["-map", f"0:a:m:language:{lang}?"])
    # Copy subtitle streams matching the allowlist.
    for lang in _normalized(selection.subtitles):
        args.extend(["-map", f"0:s:m:language:{lang}?"])

    # Fallback to the default audio/subtitle track if language tags are missing.
    args.extend(
        [
            "-map",
            "0:a:m:language:und?",
            "-map",
            "0:s:m:language:und?",
            "-map",
            "0:a:0?",
        ]
    )

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


