"""Utilities for building ffmpeg remux commands with language guardrails."""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set


@dataclass
class TrackSelection:
    audio: Sequence[str]
    subtitles: Sequence[str]


# Audio codecs that cannot be copy-muxed into MKV containers.
# These are Blu-ray specific codecs that need to be skipped or transcoded.
_MKV_INCOMPATIBLE_AUDIO_CODECS = {"pcm_bluray"}

# Subtitle codecs that cannot be copy-muxed into MKV containers.
# mov_text is an MP4-native subtitle format (MPEG-4 Part 17).
_MKV_INCOMPATIBLE_SUBTITLE_CODECS = {"mov_text"}

# ISO 639-2 has 20 languages with two 3-letter codes: a "bibliographic" (B)
# code derived from the English name and a "terminological" (T) code derived
# from the native name.  Radarr uses a mix of B and T codes, MKV/ffprobe tags
# also vary by muxer.  We normalise to the B code (which ffmpeg prefers) so
# that "fre" and "fra" are treated as the same language.
# Source: Library of Congress https://www.loc.gov/standards/iso639-2/php/code_list.php
_ISO639_BT_PAIRS: Dict[str, str] = {
    # T -> B  (terminological -> bibliographic)
    "sqi": "alb",   # Albanian
    "hye": "arm",   # Armenian
    "eus": "baq",   # Basque
    "mya": "bur",   # Burmese
    "zho": "chi",   # Chinese
    "ces": "cze",   # Czech
    "nld": "dut",   # Dutch / Flemish
    "fra": "fre",   # French
    "kat": "geo",   # Georgian
    "deu": "ger",   # German
    "ell": "gre",   # Greek (Modern)
    "isl": "ice",   # Icelandic
    "mkd": "mac",   # Macedonian
    "msa": "may",   # Malay
    "mri": "mao",   # Maori
    "fas": "per",   # Persian
    "ron": "rum",   # Romanian
    "slk": "slo",   # Slovak
    "bod": "tib",   # Tibetan
    "cym": "wel",   # Welsh
}


def _normalize_lang(code: str) -> str:
    """Normalise an ISO 639-2 language code to its bibliographic form.

    This ensures that e.g. "fra" and "fre" both resolve to "fre", so
    language comparisons work regardless of which variant the source uses.
    """
    code = code.lower()
    return _ISO639_BT_PAIRS.get(code, code)

# Audio codec quality ranking (higher = better).
# Within the same channel count, prefer lossless > lossy high-bitrate > lossy.
_AUDIO_CODEC_RANK: Dict[str, int] = {
    "truehd":       100,   # Dolby TrueHD (often with Atmos)
    "dts":           40,   # DTS (base) — ranked below DTS-HD variants via profile
    "eac3":          35,   # Dolby Digital Plus (E-AC-3)
    "ac3":           30,   # Dolby Digital (AC-3)
    "aac":           20,
    "mp3":           10,
    "mp2":            5,
    "vorbis":        15,
    "opus":          25,
    "flac":          90,
    "pcm_s16le":     80,
    "pcm_s24le":     85,
}

# DTS profile strings that indicate lossless variants.
_DTS_HD_PROFILES = {"DTS-HD MA", "DTS-HD Master Audio"}
_DTS_HD_HRA_PROFILES = {"DTS-HD HRA", "DTS-HD High Resolution Audio"}

# Title substrings that indicate commentary or descriptive audio.
_COMMENTARY_KEYWORDS = {
    "commentary", "director", "filmmaker", "isolated",
    "descriptive", "audio description", "ad track",
    "visually impaired",
}


@dataclass
class _AudioTrack:
    """Parsed audio track metadata for ranking."""
    stream_index: str
    lang: str
    codec: str
    profile: str
    channels: int
    bit_rate: int
    title: str
    is_default: bool
    is_commentary: bool
    score: int = 0


def _score_audio_track(track: _AudioTrack) -> int:
    """Score an audio track for quality ranking. Higher = better."""
    # Base codec score
    score = _AUDIO_CODEC_RANK.get(track.codec, 1)

    # DTS profiles: DTS-HD MA is lossless (~95), DTS-HD HRA is lossy (~50)
    if track.codec == "dts":
        if any(p in track.profile for p in _DTS_HD_PROFILES):
            score = 95
        elif any(p in track.profile for p in _DTS_HD_HRA_PROFILES):
            score = 50

    # TrueHD with Atmos object metadata gets a bonus
    if track.codec == "truehd" and "atmos" in track.title.lower():
        score += 5

    # Channel count as a major factor (multiply by 10 so 7.1 >> stereo)
    score += track.channels * 10

    # Tie-breaker: higher bitrate is better
    if track.bit_rate > 0:
        score += min(track.bit_rate // 100_000, 20)  # cap at +20

    # Default disposition gets a small bonus (release group's preference)
    if track.is_default:
        score += 2

    return score


def _parse_audio_track(stream: dict, lang_override: Optional[str] = None) -> Optional[_AudioTrack]:
    """Parse an ffprobe stream dict into an _AudioTrack."""
    if stream.get("codec_type") != "audio":
        return None

    codec = stream.get("codec_name", "").lower()
    if codec in _MKV_INCOMPATIBLE_AUDIO_CODECS:
        return None

    idx = str(stream.get("index", ""))
    raw_lang = lang_override or stream.get("tags", {}).get("language", "und").lower()
    lang = _normalize_lang(raw_lang)
    profile = stream.get("profile", "") or ""
    channels = int(stream.get("channels", 0))
    bit_rate = int(stream.get("bit_rate", 0) or 0)
    title = (stream.get("tags", {}).get("title", "") or "").lower()
    disposition = stream.get("disposition", {})
    is_default = disposition.get("default", 0) == 1
    is_commentary = (
        disposition.get("comment", 0) == 1
        or disposition.get("visual_impaired", 0) == 1
        or disposition.get("hearing_impaired", 0) == 1
        or any(kw in title for kw in _COMMENTARY_KEYWORDS)
    )

    track = _AudioTrack(
        stream_index=idx,
        lang=lang,
        codec=codec,
        profile=profile,
        channels=channels,
        bit_rate=bit_rate,
        title=title,
        is_default=is_default,
        is_commentary=is_commentary,
    )
    track.score = _score_audio_track(track)
    return track


def _select_best_audio(
    tracks: List[_AudioTrack],
    original_language: Optional[str],
    keep_audio_langs: Set[str],
) -> List[_AudioTrack]:
    """Select the best audio tracks: max 2 (original + English dub if foreign).

    Strategy:
    - Filter out commentary/descriptive tracks
    - If original language is English (or unknown): keep best English track
    - If original language is non-English: keep best original-language track
      + best English track (for dub)
    - "und" tracks are treated as original language only when no other track
      explicitly matches the original language
    - Max 2 tracks total

    Returns tracks in their original stream order.
    """
    # Separate commentary from main tracks
    main_tracks = [t for t in tracks if not t.is_commentary]
    if not main_tracks:
        # All tracks are commentary — fall back to all tracks
        main_tracks = tracks

    # Filter to allowed languages (normalise all codes to bibliographic form)
    allowed = {_normalize_lang(l) for l in keep_audio_langs}
    if original_language:
        allowed.add(_normalize_lang(original_language))
    allowed.add("und")

    eligible = [t for t in main_tracks if t.lang in allowed]
    if not eligible:
        # Nothing matches — safety: return all non-commentary tracks
        return main_tracks

    orig = _normalize_lang(original_language) if original_language else "eng"
    is_english_original = orig == "eng"

    # Check if any track explicitly matches the original language
    has_explicit_original = any(
        t.lang == orig for t in eligible if t.lang != "und"
    )

    # Group by language, picking the best in each group
    best_by_lang: Dict[str, _AudioTrack] = {}
    for track in eligible:
        # Treat "und" as original language only if no track explicitly matches it;
        # otherwise a mislabeled dub could override the real original audio.
        if track.lang == "und" and not has_explicit_original:
            effective_lang = orig
        else:
            effective_lang = track.lang
        if effective_lang not in best_by_lang or track.score > best_by_lang[effective_lang].score:
            best_by_lang[effective_lang] = track

    selected: List[_AudioTrack] = []

    if is_english_original:
        # English original: just the best English/und track
        if "eng" in best_by_lang:
            selected.append(best_by_lang["eng"])
    else:
        # Foreign original: best original language track + best English dub
        if orig in best_by_lang:
            selected.append(best_by_lang[orig])
        if "eng" in best_by_lang:
            eng = best_by_lang["eng"]
            # Don't add the same track twice
            if not selected or eng.stream_index != selected[0].stream_index:
                selected.append(eng)

    if not selected:
        # Fallback: pick the single best track overall
        eligible.sort(key=lambda t: t.score, reverse=True)
        selected.append(eligible[0])

    # Return in original stream order
    order = {t.stream_index: i for i, t in enumerate(tracks)}
    selected.sort(key=lambda t: order.get(t.stream_index, 0))
    return selected


@dataclass
class StreamInfo:
    """Information about streams in a media file."""
    audio_languages: Set[str]
    subtitle_languages: Set[str]
    has_video: bool
    audio_count: int
    subtitle_count: int
    original_language: Optional[str] = None  # Deprecated: use API lookup instead


def probe_streams(source: Path) -> Optional[StreamInfo]:
    """Probe a media file to discover available streams and their languages.

    Returns None if probing fails.

    NOTE: The ``original_language`` field on StreamInfo is populated from the
    first audio track, which is *unreliable* for release groups that put dubs
    first (e.g. Russian groups).  Callers should prefer the Radarr/Sonarr API
    lookup via ``languages.arr_language_to_iso()`` and pass the result as
    ``original_language`` to ``build_ffmpeg_command()``.
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
        first_audio_lang: Optional[str] = None

        for stream in streams:
            codec_type = stream.get("codec_type", "")
            lang = stream.get("tags", {}).get("language", "und").lower()

            if codec_type == "video":
                has_video = True
            elif codec_type == "audio":
                audio_count += 1
                audio_langs.add(lang)
                if first_audio_lang is None:
                    first_audio_lang = lang
            elif codec_type == "subtitle":
                subtitle_count += 1
                subtitle_langs.add(lang)

        return StreamInfo(
            audio_languages=audio_langs,
            subtitle_languages=subtitle_langs,
            has_video=has_video,
            audio_count=audio_count,
            subtitle_count=subtitle_count,
            original_language=first_audio_lang,
        )
    except Exception:
        return None


def build_ffmpeg_command(
    source: Path,
    destination: Path,
    selection: TrackSelection,
    *,
    original_language: Optional[str] = None,
    stream_languages: Optional[List[Dict[str, str]]] = None,
) -> List[str]:
    """Construct an ffmpeg command that remuxes while stripping unwanted tracks.

    This uses ``-c copy`` to avoid transcoding while removing tracks whose
    language is not in the provided allowlists.

    Args:
        source: Input media file.
        destination: Output file path.
        selection: Language allowlists from user's media policy.
        original_language: ISO 639 code for the content's original language
            (from Radarr/Sonarr API).  Always kept in audio.
        stream_languages: Optional per-stream language override, used for
            BDMV sources where ffprobe reports ``und`` for all tracks but
            CLPI files contain the real language codes.  Each entry is a dict
            with keys ``type`` (``"audio"`` or ``"subtitle"``), ``lang``
            (ISO 639 code), and ``index`` (ffmpeg stream index as str).
    """
    args: List[str] = [
        "ffmpeg",
        "-hide_banner",
        "-y",
    ]

    # Transport stream containers (.m2ts, .ts) may not store codec parameters
    # in headers. Increase probe size so ffmpeg reads deeper into the file to
    # determine sample rate, channels, etc. before writing the MKV header.
    if source.suffix.lower() in (".m2ts", ".ts"):
        args.extend(["-analyzeduration", "10M", "-probesize", "10M"])

    # AVI containers often have broken timestamps that cause MKV muxing to
    # fail with "Invalid argument".  Regenerate PTS from DTS to fix this.
    if source.suffix.lower() == ".avi":
        args.extend(["-fflags", "+genpts"])

    args.extend(["-i", str(source)])

    # Always include the main video track
    args.extend(["-map", "0:v:0?"])

    # Build language allowlists (normalise to bibliographic ISO 639-2 codes)
    keep_audio_langs = {_normalize_lang(c) for c in _normalized(selection.audio)}
    keep_sub_langs = {_normalize_lang(c) for c in _normalized(selection.subtitles)}

    # Check if "forced" is specified for subtitles (it's a special flag, not a language)
    include_forced_subs = "forced" in keep_sub_langs
    if include_forced_subs:
        keep_sub_langs.discard("forced")

    # Always include original language audio when known
    if original_language:
        keep_audio_langs.add(_normalize_lang(original_language))

    # Probe the source to get stream layout
    streams: list = []
    try:
        probe_cmd = ["ffprobe", "-v", "quiet"]
        if source.suffix.lower() in (".m2ts", ".ts"):
            probe_cmd.extend(["-analyzeduration", "10M", "-probesize", "10M"])
        probe_cmd.extend(["-print_format", "json", "-show_streams", str(source)])
        result = subprocess.run(
            probe_cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            # Probe failed — copy everything as fallback
            args.extend(["-map", "0:a?", "-map", "0:s?"])
            args.extend(["-c", "copy", str(destination)])
            return args

        data = json.loads(result.stdout)
        streams = data.get("streams", [])

        # Build a per-stream language map, optionally overridden by CLPI data
        clpi_map: Dict[str, str] = {}  # stream_index -> lang
        if stream_languages:
            for entry in stream_languages:
                clpi_map[str(entry.get("index", ""))] = entry.get("lang", "und")

        # --- Audio filtering ---
        audio_maps: List[str] = []
        total_audio = 0
        codec_filtered_indices: set = set()

        # Parse all audio tracks with metadata
        all_audio_tracks: List[_AudioTrack] = []
        for stream in streams:
            if stream.get("codec_type") != "audio":
                continue
            total_audio += 1
            idx = str(stream.get("index", ""))

            # Prefer CLPI language data if available
            lang_override = clpi_map.get(idx)
            if lang_override:
                lang_override = lang_override.lower()

            track = _parse_audio_track(stream, lang_override=lang_override)
            if track is None:
                codec_name = stream.get("codec_name", "").lower()
                print(
                    f"[remux] skipping stream {idx}: codec '{codec_name}' "
                    f"is not supported in MKV containers"
                )
                codec_filtered_indices.add(idx)
                continue
            all_audio_tracks.append(track)

        if all_audio_tracks:
            selected = _select_best_audio(
                all_audio_tracks, original_language, keep_audio_langs,
            )
            audio_maps = [f"0:{t.stream_index}" for t in selected]

            # Log what we're keeping
            for t in selected:
                print(
                    f"[remux] keeping audio stream {t.stream_index}: "
                    f"{t.codec} {t.channels}ch {t.lang} "
                    f"(score={t.score})"
                )
            dropped = [t for t in all_audio_tracks if t not in selected]
            for t in dropped:
                reason = "commentary" if t.is_commentary else "lower quality/unwanted lang"
                print(
                    f"[remux] dropping audio stream {t.stream_index}: "
                    f"{t.codec} {t.channels}ch {t.lang} "
                    f"(score={t.score}, {reason})"
                )

        # SAFETY GUARD: if filtering would remove ALL audio, keep everything
        # that wasn't rejected for codec incompatibility
        if total_audio > 0 and not audio_maps:
            print(
                f"[remux] WARNING: all {total_audio} audio tracks would be "
                f"removed — keeping language-filtered audio to avoid silent output"
            )
            for stream in streams:
                if stream.get("codec_type") == "audio":
                    idx = str(stream.get("index", ""))
                    if idx not in codec_filtered_indices:
                        audio_maps.append(f"0:{idx}")

        for mapping in audio_maps:
            args.extend(["-map", mapping])

        # --- Subtitle filtering ---
        for stream in streams:
            if stream.get("codec_type") != "subtitle":
                continue

            sub_codec = stream.get("codec_name", "").lower()
            if sub_codec in _MKV_INCOMPATIBLE_SUBTITLE_CODECS:
                continue  # e.g. mov_text from MP4 can't go in MKV

            idx = str(stream.get("index", ""))

            if idx in clpi_map:
                lang = _normalize_lang(clpi_map[idx])
            else:
                lang = _normalize_lang(
                    stream.get("tags", {}).get("language", "und")
                )

            disposition = stream.get("disposition", {})
            is_forced = disposition.get("forced", 0) == 1

            if lang in keep_sub_langs or (is_forced and include_forced_subs):
                args.extend(["-map", f"0:{idx}"])

    except Exception:
        # Fallback: copy all streams if filtering fails entirely
        args.extend(["-map", "0:a?", "-map", "0:s?"])

    # --- Metadata injection for BDMV streams ---
    # When CLPI data provides language codes for streams that ffprobe
    # reports as "und", inject the real language as metadata so the
    # output MKV has proper language tags.
    #
    # The metadata index (e.g. -metadata:s:a:0) refers to the Nth output
    # stream of that type.  We must track the output index separately —
    # incrementing for every mapped stream, not just those with non-und
    # language tags.
    if stream_languages:
        audio_out_idx = 0
        sub_out_idx = 0
        mapped_indices = {a.split(":")[-1] for a in args if a.startswith("0:")}
        for entry in stream_languages:
            lang = entry.get("lang", "und")
            stype = entry.get("type", "")
            stream_index = str(entry.get("index", ""))

            # Only inject metadata if the stream was actually mapped
            if stream_index not in mapped_indices:
                continue

            if stype == "audio":
                if lang != "und":
                    args.extend([
                        f"-metadata:s:a:{audio_out_idx}",
                        f"language={lang}",
                    ])
                audio_out_idx += 1
            elif stype == "subtitle":
                if lang != "und":
                    args.extend([
                        f"-metadata:s:s:{sub_out_idx}",
                        f"language={lang}",
                    ])
                sub_out_idx += 1

    # --- Metadata injection for non-BDMV "und" streams ---
    # Standard MKV/MP4 files (not BDMVs) sometimes have audio tracks
    # tagged "und" instead of the correct language.  When we know the
    # original language from the Radarr/Sonarr API, inject it so the
    # output MKV has proper tags for player language selection.
    if not stream_languages and original_language:
        mapped_indices = {a.split(":")[-1] for a in args if a.startswith("0:")}
        audio_out_idx = 0
        injected = False
        for stream in streams:
            if stream.get("codec_type") != "audio":
                continue
            idx = str(stream.get("index", ""))
            if idx not in mapped_indices:
                continue
            lang = stream.get("tags", {}).get("language", "und").lower()
            if lang == "und":
                args.extend([
                    f"-metadata:s:a:{audio_out_idx}",
                    f"language={original_language.lower()}",
                ])
                injected = True
            audio_out_idx += 1
        if injected:
            print(
                f"[remux] injected language '{original_language}' for "
                f"'und'-tagged audio tracks"
            )

    args.extend(["-c", "copy", str(destination)])
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


