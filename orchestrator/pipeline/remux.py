"""Utilities for building ffmpeg remux commands with language guardrails."""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


class RemuxPolicyError(RuntimeError):
    """Raised when a command cannot be built without violating the policy."""


@dataclass
class SubtitlePolicy:
    """Per-language subtitle limits (mirrors ``models.SubtitleRules``)."""

    max_per_language: int = 1
    prefer: Sequence[str] = ("text", "image")
    keep_forced: bool = True
    keep_sdh: bool = True


@dataclass
class TrackSelection:
    audio: Sequence[str]
    subtitles: Sequence[str]
    subtitle_policy: SubtitlePolicy = field(default_factory=SubtitlePolicy)
    container: str = "mkv"  # "mkv" or "mp4"


# Audio codecs that cannot be copy-muxed into MKV containers.
# These are Blu-ray specific codecs that need to be skipped or transcoded.
_MKV_INCOMPATIBLE_AUDIO_CODECS = {"pcm_bluray"}

# Subtitle codecs that cannot be copy-muxed into MKV containers.
# mov_text is an MP4-native subtitle format (MPEG-4 Part 17).
_MKV_INCOMPATIBLE_SUBTITLE_CODECS = {"mov_text"}

# Text subtitle codecs.  These can be copied into MKV and converted to
# mov_text for MP4.  Everything else (PGS, VobSub, DVB) is image-based and
# cannot be stored in MP4.
_TEXT_SUBTITLE_CODECS = {"subrip", "ass", "webvtt", "mov_text", "text"}

# Aliases accepted in ``SubtitlePolicy.prefer`` and seen in probe output.
_SUBTITLE_CODEC_ALIASES: Dict[str, str] = {
    "srt": "subrip",
    "ssa": "ass",
    "vtt": "webvtt",
    "pgs": "hdmv_pgs_subtitle",
    "vobsub": "dvd_subtitle",
}

# Every token accepted in ``prefer``: the two classes, aliases, codec names.
SUBTITLE_PREFER_TOKENS = frozenset(
    {"text", "image"}
    | set(_SUBTITLE_CODEC_ALIASES)
    | set(_SUBTITLE_CODEC_ALIASES.values())
    | _TEXT_SUBTITLE_CODECS
    | {"dvb_subtitle"}
)

_SUBTITLE_FORCED_KEYWORDS = {
    "forced", "foreign only", "foreign parts", "foreign audio",
    "signs only", "signs/songs", "signs & songs",
}
_SUBTITLE_SDH_KEYWORDS = {
    "sdh", "hearing impaired", "hearing-impaired", "hearing_impaired",
    "closed captions", "closed-caption", "captions", "cc",
}
_SUBTITLE_COMMENTARY_KEYWORDS = {"commentary", "comments"}

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
class VideoQuality:
    """Video quality metadata extracted from ffprobe."""

    width: int = 0
    height: int = 0
    codec: str = ""  # e.g. "hevc", "h264", "av1"
    hdr: bool = False
    bit_rate: int = 0
    score: int = 0  # Computed quality score for comparison

    @property
    def resolution_label(self) -> str:
        if self.height >= 2000:
            return "2160p"
        elif self.height >= 1400:
            return "1440p"
        elif self.height >= 900:
            return "1080p"
        elif self.height >= 600:
            return "720p"
        elif self.height >= 400:
            return "480p"
        return f"{self.height}p" if self.height > 0 else "unknown"


# Resolution scores (higher = better)
_RESOLUTION_SCORES = {
    "2160p": 400,
    "1440p": 300,
    "1080p": 200,
    "720p": 100,
    "480p": 50,
}

# Video codec scores
_VIDEO_CODEC_SCORES: Dict[str, int] = {
    "av1": 40,
    "hevc": 30,
    "h265": 30,
    "vp9": 25,
    "h264": 20,
    "mpeg4": 5,
    "mpeg2video": 5,
}


def probe_video_quality(source: Path) -> Optional[VideoQuality]:
    """Probe a media file to extract video quality metadata.

    Returns a VideoQuality with a numeric score for comparison.
    Higher score = better quality.  Scoring factors:
      - Resolution: 2160p=400, 1080p=200, 720p=100
      - Codec: AV1=40, HEVC=30, H.264=20
      - HDR: +50 bonus
      - Bitrate: up to +30 bonus
    """
    streams = probe_raw_streams(source)
    if not streams:
        return None

    # Find the REAL video stream — skip embedded thumbnails/cover art.
    # Thumbnails are typically MJPEG/PNG with small dimensions and the
    # "attached_pic" disposition flag set.
    _THUMBNAIL_CODECS = {"mjpeg", "png", "bmp", "gif"}
    best_vq: Optional[VideoQuality] = None

    for stream in streams:
        if stream.get("codec_type") != "video":
            continue

        codec = stream.get("codec_name", "").lower()
        width = int(stream.get("width", 0))
        height = int(stream.get("height", 0))

        # Skip attached picture / cover art streams
        disp = stream.get("disposition", {})
        if disp.get("attached_pic", 0):
            continue
        # Skip known thumbnail codecs with small dimensions
        if codec in _THUMBNAIL_CODECS and width < 1000 and height < 1000:
            continue

        bit_rate = int(stream.get("bit_rate", 0) or 0)

        # HDR detection via color transfer characteristics
        color_transfer = stream.get("color_transfer", "")
        hdr = color_transfer in ("smpte2084", "arib-std-b67")

        vq = VideoQuality(
            width=width, height=height, codec=codec,
            hdr=hdr, bit_rate=bit_rate,
        )

        # Compute score
        score = _RESOLUTION_SCORES.get(vq.resolution_label, 0)
        score += _VIDEO_CODEC_SCORES.get(codec, 10)
        if hdr:
            score += 50
        if bit_rate > 0:
            score += min(bit_rate // 1_000_000, 30)

        vq.score = score

        # Keep the highest-scoring video stream
        if best_vq is None or score > best_vq.score:
            best_vq = vq

    return best_vq


# Minimum scores for target resolutions (used for upgrade decisions)
RESOLUTION_TARGET_SCORES: Dict[str, int] = {
    "720p": 100,
    "1080p": 200,
    "1440p": 300,
    "2160p": 400,
}


# ---------------------------------------------------------------------------
# Subtitle selection policy
# ---------------------------------------------------------------------------


def _canonical_sub_codec(codec: str) -> str:
    codec = (codec or "").lower()
    return _SUBTITLE_CODEC_ALIASES.get(codec, codec)


def container_for_path(path: Path) -> str:
    """Return the container ("mkv" or "mp4") implied by a file extension."""
    return "mp4" if path.suffix.lower() in (".mp4", ".m4v") else "mkv"


def _keyword_in_text(text: str, keywords: Set[str]) -> bool:
    """True when ``text`` contains a keyword (short ones match whole tokens)."""
    haystack = text.lower()
    if any(kw in haystack for kw in keywords if len(kw) > 2):
        return True
    tokens = set(re.findall(r"[a-z0-9]+", haystack))
    return any(kw in tokens for kw in keywords if len(kw) <= 2)


@dataclass
class SubtitleTrack:
    """A subtitle stream parsed for policy selection.

    ``input_index`` is the ffmpeg input the stream comes from.  ``origin``
    breaks ties between otherwise equal tracks: lower wins, so the file
    being rewritten (origin 0) keeps its own track over a candidate's.
    """

    stream_index: str
    lang: str
    codec: str
    title: str
    kind: str  # "forced" | "normal" | "sdh"
    is_default: bool = False
    is_commentary: bool = False
    input_index: int = 0
    origin: int = 0

    @property
    def is_text(self) -> bool:
        return self.codec in _TEXT_SUBTITLE_CODECS

    def describe(self) -> str:
        kind = "" if self.kind == "normal" else f" {self.kind}"
        return f"#{self.stream_index} {self.lang} {self.codec}{kind}"


def parse_subtitle_tracks(
    streams: Iterable[dict],
    *,
    input_index: int = 0,
    origin: int = 0,
    lang_overrides: Optional[Dict[str, str]] = None,
) -> List[SubtitleTrack]:
    """Parse the subtitle streams of an ffprobe stream list."""
    tracks: List[SubtitleTrack] = []
    for stream in streams:
        if stream.get("codec_type") != "subtitle":
            continue
        idx = str(stream.get("index", ""))
        tags = stream.get("tags", {}) or {}
        raw_lang = (lang_overrides or {}).get(idx) or tags.get("language") or "und"
        title = (tags.get("title", "") or "").lower()
        disp = stream.get("disposition", {}) or {}
        if disp.get("forced", 0) == 1 or _keyword_in_text(title, _SUBTITLE_FORCED_KEYWORDS):
            kind = "forced"
        elif (
            disp.get("hearing_impaired", 0) == 1
            or disp.get("captions", 0) == 1
            or _keyword_in_text(title, _SUBTITLE_SDH_KEYWORDS)
        ):
            kind = "sdh"
        else:
            kind = "normal"
        tracks.append(SubtitleTrack(
            stream_index=idx,
            lang=_normalize_lang(raw_lang.lower()),
            codec=_canonical_sub_codec(stream.get("codec_name", "")),
            title=title,
            kind=kind,
            is_default=disp.get("default", 0) == 1,
            is_commentary=(
                disp.get("comment", 0) == 1
                or _keyword_in_text(title, _SUBTITLE_COMMENTARY_KEYWORDS)
            ),
            input_index=input_index,
            origin=origin,
        ))
    return tracks


@dataclass
class SubtitleSelection:
    selected: List[SubtitleTrack]
    dropped: List[SubtitleTrack]
    # Image tracks the policy would have kept in MKV but MP4 cannot hold.
    dropped_for_container: List[SubtitleTrack] = field(default_factory=list)


def _prefer_rank(track: SubtitleTrack, prefer: Sequence[str]) -> int:
    for rank, token in enumerate(prefer):
        token = _canonical_sub_codec(token.strip())
        if token == "text" and track.is_text:
            return rank
        if token == "image" and not track.is_text:
            return rank
        if token == track.codec:
            return rank
    return len(prefer)


def _choose_subtitles(
    tracks: Sequence[SubtitleTrack],
    selection: TrackSelection,
    *,
    container: str,
    allow_image: bool,
) -> List[SubtitleTrack]:
    policy = selection.subtitle_policy
    langs = [_normalize_lang(c) for c in _normalized(selection.subtitles)]
    any_forced = "forced" in langs  # legacy pseudo-language: forced of any language
    langs = [lang for lang in langs if lang != "forced"]
    allowed = set(langs)

    buckets: Dict[Tuple[str, str], List[SubtitleTrack]] = {}
    for t in tracks:
        if t.is_commentary:
            continue
        if container == "mkv" and t.codec in _MKV_INCOMPATIBLE_SUBTITLE_CODECS:
            continue
        if not allow_image and not t.is_text:
            continue
        lang_ok = t.lang in allowed
        if t.kind == "forced":
            if not ((lang_ok and policy.keep_forced) or any_forced):
                continue
        elif not lang_ok:
            continue
        buckets.setdefault((t.lang, t.kind), []).append(t)

    def rank(t: SubtitleTrack):
        try:
            idx = int(t.stream_index)
        except ValueError:
            idx = 0
        return (_prefer_rank(t, policy.prefer), not t.is_default, t.origin, t.input_index, idx)

    def best(pool: List[SubtitleTrack], limit: int) -> List[SubtitleTrack]:
        # Exact duplicates (same language + codec + kind) collapse to one.
        seen: Set[str] = set()
        out: List[SubtitleTrack] = []
        for t in sorted(pool, key=rank):
            if t.codec in seen:
                continue
            seen.add(t.codec)
            out.append(t)
        return out[:limit]

    order = {lang: i for i, lang in enumerate(langs)}
    present = sorted({lang for lang, _ in buckets}, key=lambda code: (order.get(code, len(order)), code))
    chosen: List[SubtitleTrack] = []
    for lang in present:
        normal_pool = buckets.get((lang, "normal"), [])
        sdh_pool = buckets.get((lang, "sdh"), [])
        if not policy.keep_sdh and not normal_pool:
            normal_pool = sdh_pool  # SDH is better than no subtitle at all
        chosen += best(buckets.get((lang, "forced"), []), 1)
        chosen += best(normal_pool, policy.max_per_language)
        if policy.keep_sdh:
            chosen += best(sdh_pool, 1)
    return chosen


def select_subtitles(
    tracks: Sequence[SubtitleTrack],
    selection: TrackSelection,
    *,
    container: Optional[str] = None,
) -> SubtitleSelection:
    """Apply the subtitle policy to parsed tracks.

    Per language in ``selection.subtitles``: at most one forced track (if
    ``keep_forced``), up to ``max_per_language`` normal tracks ranked by
    ``prefer``, and at most one SDH track (if ``keep_sdh``).  For MP4 output
    image-based tracks are excluded; any the MKV policy would have kept are
    reported in ``dropped_for_container``.
    """
    container = container or selection.container
    chosen = _choose_subtitles(
        tracks, selection, container=container, allow_image=container != "mp4",
    )
    lost: List[SubtitleTrack] = []
    if container == "mp4":
        would_keep = _choose_subtitles(
            tracks, selection, container=container, allow_image=True,
        )
        lost = [t for t in would_keep if not t.is_text]
        for t in lost:
            print(
                f"[remux] WARNING: dropping image subtitle {t.describe()}: "
                f"MP4 cannot hold image-based subtitles"
            )
    chosen_ids = {id(t) for t in chosen}
    dropped = [t for t in tracks if id(t) not in chosen_ids]
    return SubtitleSelection(selected=chosen, dropped=dropped, dropped_for_container=lost)


def select_subtitle_streams(
    streams: Iterable[dict],
    selection: TrackSelection,
    *,
    container: Optional[str] = None,
    lang_overrides: Optional[Dict[str, str]] = None,
) -> SubtitleSelection:
    """Convenience wrapper: parse an ffprobe stream list and select."""
    tracks = parse_subtitle_tracks(streams, lang_overrides=lang_overrides)
    return select_subtitles(tracks, selection, container=container)


def _kind_disposition(track: SubtitleTrack, container: str, out_idx: int) -> List[str]:
    """Explicit forced/SDH disposition for MP4 output.

    MKV keeps the source flags and titles on copy.  Converting to mov_text
    loses the title, so MP4 relies on the disposition to mark forced/SDH
    tracks (for players and for later policy checks).
    """
    if container != "mp4" or track.kind == "normal":
        return []
    flag = "forced" if track.kind == "forced" else "hearing_impaired"
    return [f"-disposition:s:{out_idx}", flag]


def _subtitle_codec_args(container: str, has_subtitles: bool) -> List[str]:
    """Codec arguments: copy everything, convert text subs for MP4."""
    args = ["-c", "copy"]
    if container == "mp4" and has_subtitles:
        args.extend(["-c:s", "mov_text"])
    return args


@dataclass
class StreamInfo:
    """Information about streams in a media file."""
    audio_languages: Set[str]
    subtitle_languages: Set[str]
    has_video: bool
    audio_count: int
    subtitle_count: int
    original_language: Optional[str] = None  # Deprecated: use API lookup instead


def stream_info_from_streams(streams: Iterable[dict]) -> StreamInfo:
    """Summarise an ffprobe stream list into a StreamInfo."""
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
        return stream_info_from_streams(data.get("streams", []))
    except Exception:
        return None


@dataclass
class RemuxNotes:
    """What the command builder decided, for logging and the plan record."""

    probe_failed: bool = False
    subtitles_kept: List[str] = field(default_factory=list)
    subtitles_dropped: List[str] = field(default_factory=list)
    dropped_image_subtitles: List[str] = field(default_factory=list)


@dataclass
class RemuxBuild:
    command: List[str]
    notes: RemuxNotes


def build_ffmpeg_command(
    source: Path,
    destination: Path,
    selection: TrackSelection,
    *,
    original_language: Optional[str] = None,
    stream_languages: Optional[List[Dict[str, str]]] = None,
) -> List[str]:
    """Return only the ffmpeg command from :func:`build_remux`."""
    return build_remux(
        source, destination, selection,
        original_language=original_language,
        stream_languages=stream_languages,
    ).command


def build_remux(
    source: Path,
    destination: Path,
    selection: TrackSelection,
    *,
    original_language: Optional[str] = None,
    stream_languages: Optional[List[Dict[str, str]]] = None,
) -> RemuxBuild:
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

    Subtitles follow ``selection.subtitle_policy`` and ``selection.container``.
    If the source cannot be probed, no subtitle streams are mapped (fail
    closed) and ``notes.probe_failed`` is set; audio still falls back to
    copying every stream so the import does not come out silent.
    """
    notes = RemuxNotes()
    container = selection.container
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

    # Always include original language audio when known
    if original_language:
        keep_audio_langs.add(_normalize_lang(original_language))

    # Probe the source to get stream layout
    streams: list = []
    sub_metadata: List[str] = []
    subs_mapped = 0
    maps_start = len(args)
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
            raise RuntimeError(f"ffprobe exited with {result.returncode}")

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

        # --- Subtitle filtering (policy-driven) ---
        tracks = parse_subtitle_tracks(streams, lang_overrides=clpi_map)
        sub_sel = select_subtitles(tracks, selection, container=container)
        for out_idx, sub in enumerate(sub_sel.selected):
            args.extend(["-map", f"0:{sub.stream_index}"])
            if sub.stream_index in clpi_map and sub.lang != "und":
                sub_metadata.extend([f"-metadata:s:s:{out_idx}", f"language={sub.lang}"])
            sub_metadata.extend(_kind_disposition(sub, container, out_idx))
        subs_mapped = len(sub_sel.selected)
        notes.subtitles_kept = [s.describe() for s in sub_sel.selected]
        notes.subtitles_dropped = [s.describe() for s in sub_sel.dropped]
        notes.dropped_image_subtitles = [
            s.describe() for s in sub_sel.dropped_for_container
        ]
        if sub_sel.dropped:
            print(
                f"[remux] subtitles: keeping {len(sub_sel.selected)}, "
                f"dropping {len(sub_sel.dropped)}"
            )

    except Exception as exc:
        # Fail closed: without a probe we cannot apply the subtitle policy,
        # so map no subtitle streams.  Audio keeps the copy-all fallback.
        print(
            f"[remux] WARNING: cannot probe {source.name} ({exc}); "
            f"copying all audio and NO subtitles"
        )
        del args[maps_start:]
        streams = []
        sub_metadata = []
        subs_mapped = 0
        notes.probe_failed = True
        args.extend(["-map", "0:a?"])

    # --- Metadata injection for BDMV streams ---
    # When CLPI data provides language codes for streams that ffprobe
    # reports as "und", inject the real language as metadata so the
    # output MKV has proper language tags.
    #
    # The metadata index (e.g. -metadata:s:a:0) refers to the Nth output
    # stream of that type.  We must track the output index separately —
    # incrementing for every mapped stream, not just those with non-und
    # language tags.
    #
    # Subtitle language metadata is emitted above in output order, because
    # policy selection can reorder subtitle streams.
    args.extend(sub_metadata)
    if stream_languages:
        audio_out_idx = 0
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

    args.extend(_subtitle_codec_args(container, subs_mapped > 0))
    args.append(str(destination))
    return RemuxBuild(command=args, notes=notes)


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


# ---------------------------------------------------------------------------
# Cross-mux: merge an audio track from a candidate file into a library file
# ---------------------------------------------------------------------------


def probe_raw_streams(source: Path) -> Optional[List[dict]]:
    """Return raw ffprobe stream dicts for a file, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams", "-show_format",
                str(source),
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        return data.get("streams", [])
    except Exception:
        return None


def find_best_audio_track_for_language(
    streams: List[dict],
    target_language: str,
) -> Optional[_AudioTrack]:
    """Find the best audio track in a specific language from raw streams.

    Parses all audio streams, filters to those matching target_language,
    and returns the highest-scored track.  Returns None if no matching
    track is found.
    """
    target_norm = _normalize_lang(target_language.lower())
    candidates: List[_AudioTrack] = []

    for stream in streams:
        track = _parse_audio_track(stream)
        if track is None:
            continue
        if track.is_commentary:
            continue
        if _normalize_lang(track.lang) != target_norm:
            continue
        track.score = _score_audio_track(track)
        candidates.append(track)

    if not candidates:
        return None

    return max(candidates, key=lambda t: t.score)


def find_all_enrichment_audio_tracks(
    streams: List[dict],
    target_languages: set[str],
    library_audio_languages: set[str],
) -> List[_AudioTrack]:
    """Find the best audio track for EACH missing target language.

    For each language in target_languages that is not in
    library_audio_languages, selects the best candidate audio track.
    Returns a list of tracks to cross-mux (may be empty).
    """
    missing = {_normalize_lang(l) for l in target_languages} - {
        _normalize_lang(l) for l in library_audio_languages
    }
    missing.discard("und")

    if not missing:
        return []

    # Parse + score all non-commentary audio tracks
    all_tracks: List[_AudioTrack] = []
    for stream in streams:
        track = _parse_audio_track(stream)
        if track is None or track.is_commentary:
            continue
        track.score = _score_audio_track(track)
        all_tracks.append(track)

    # For each missing language, pick the best track
    result: List[_AudioTrack] = []
    for lang in sorted(missing):
        candidates = [
            t for t in all_tracks
            if _normalize_lang(t.lang) == lang
        ]
        if candidates:
            result.append(max(candidates, key=lambda t: t.score))

    return result


@dataclass
class _SubtitleCandidate:
    """A subtitle stream from a candidate file worth cross-muxing."""

    stream_index: str  # Absolute ffprobe stream index
    lang: str  # ISO 639 code
    codec: str
    forced: bool = False
    title: str = ""


def find_useful_candidate_subtitles(
    cand_streams: List[dict],
    library_sub_languages: set[str],
    target_languages: set[str],
) -> List[_SubtitleCandidate]:
    """Find subtitle streams in a candidate that the library doesn't have.

    Returns subtitles in target languages that are absent from the library
    file.  Filters out MKV-incompatible codecs (e.g., mov_text).
    """
    missing = {_normalize_lang(l) for l in target_languages} - {
        _normalize_lang(l) for l in library_sub_languages
    }
    missing.discard("und")

    if not missing:
        return []

    result: List[_SubtitleCandidate] = []
    for stream in cand_streams:
        if stream.get("codec_type") != "subtitle":
            continue

        codec = stream.get("codec_name", "").lower()
        if codec in _MKV_INCOMPATIBLE_SUBTITLE_CODECS:
            continue

        lang = _normalize_lang(
            stream.get("tags", {}).get("language", "und")
        )
        if lang not in missing:
            continue

        disp = stream.get("disposition", {})
        forced = bool(disp.get("forced", 0))
        title = stream.get("tags", {}).get("title", "")

        result.append(_SubtitleCandidate(
            stream_index=str(stream.get("index", "")),
            lang=lang,
            codec=codec,
            forced=forced,
            title=title,
        ))

    return result


def _candidate_subtitle_tracks(
    subs: Optional[List[_SubtitleCandidate]],
    *,
    input_index: int,
) -> List[SubtitleTrack]:
    """Convert candidate subtitles into policy tracks (origin 1 = candidate)."""
    pseudo = [
        {
            "index": sub.stream_index,
            "codec_type": "subtitle",
            "codec_name": sub.codec,
            "tags": {"language": sub.lang, "title": sub.title},
            "disposition": {"forced": int(sub.forced)},
        }
        for sub in subs or []
    ]
    return parse_subtitle_tracks(pseudo, input_index=input_index, origin=1)


def _policy_subtitle_maps(
    library_streams: List[dict],
    library_input: int,
    candidate_subs: Optional[List[_SubtitleCandidate]],
    candidate_input: int,
    selection: TrackSelection,
    container: str,
) -> Tuple[List[str], List[str], bool]:
    """Select library + candidate subtitles together under the policy.

    Returns (map args, metadata args, any subtitles mapped).  Metadata is
    set only for candidate tracks, at their position in the output.
    """
    tracks = parse_subtitle_tracks(library_streams, input_index=library_input)
    tracks += _candidate_subtitle_tracks(candidate_subs, input_index=candidate_input)
    sub_sel = select_subtitles(tracks, selection, container=container)
    if sub_sel.dropped:
        print(
            f"[remux] subtitles: keeping {len(sub_sel.selected)}, "
            f"dropping {len(sub_sel.dropped)}"
        )
    maps: List[str] = []
    metadata: List[str] = []
    for out_idx, t in enumerate(sub_sel.selected):
        maps.extend(["-map", f"{t.input_index}:{t.stream_index}"])
        if t.origin == 1:
            metadata.extend([f"-metadata:s:s:{out_idx}", f"language={t.lang}"])
        if container == "mp4":
            metadata.extend(_kind_disposition(t, container, out_idx))
        elif t.origin == 1 and t.kind == "forced":
            metadata.extend([f"-disposition:s:{out_idx}", "forced"])
    return maps, metadata, bool(sub_sel.selected)


def _probe_library_or_refuse(library: Path) -> List[dict]:
    streams = probe_raw_streams(library)
    if streams is None:
        raise RemuxPolicyError(
            f"cannot probe {library.name}; refusing to rewrite its "
            f"subtitle tracks without knowing what they are"
        )
    return streams


def build_crossmux_command(
    library: Path,
    candidate: Path,
    audio_tracks: List[_AudioTrack],
    subtitle_tracks: Optional[List[_SubtitleCandidate]],
    offset_seconds: float,
    destination: Path,
    *,
    selection: TrackSelection,
) -> List[str]:
    """Build an ffmpeg command that merges audio (and optionally subtitle)
    tracks from a candidate file into an existing library file.

    The library file provides video and all existing audio.  Subtitles from
    the library and the candidate are pooled and re-selected under the
    subtitle policy, so a bloated library file shrinks back to the policy.
    The ``offset_seconds`` value (from chromaprint correlation) is applied
    via ``-itsoffset`` to align the candidate streams with the library video.

    Args:
        library: Path to the existing library file (input 0).
        candidate: Path to the candidate file (input 1).
        audio_tracks: Audio tracks to take from the candidate (from
            ``find_all_enrichment_audio_tracks`` or single-track list).
        subtitle_tracks: Subtitle tracks offered by the candidate (from
            ``find_useful_candidate_subtitles``).  May be None or empty.
        offset_seconds: Time offset from chromaprint alignment.
        destination: Output path; its extension decides the container.
        selection: Media policy (subtitle rules are applied).

    Raises:
        RemuxPolicyError: the library file cannot be probed.
    """
    lib_streams = _probe_library_or_refuse(library)
    container = container_for_path(destination)

    args = ["ffmpeg", "-y", "-hide_banner"]

    # Input 0: library file (video + existing audio + subtitles)
    args.extend(["-i", str(library)])

    # Input 1: candidate file with offset applied
    if abs(offset_seconds) > 0.001:
        args.extend(["-itsoffset", f"{offset_seconds:.3f}"])
    args.extend(["-i", str(candidate)])

    # Map from library (input 0) — use 0:v:0 to skip thumbnail streams
    args.extend(["-map", "0:v:0"])  # First real video stream only
    args.extend(["-map", "0:a"])    # All existing audio streams

    # Map audio tracks from candidate (input 1)
    for track in audio_tracks:
        args.extend(["-map", f"1:{track.stream_index}"])

    sub_maps, sub_metadata, has_subs = _policy_subtitle_maps(
        lib_streams, 0, subtitle_tracks, 1, selection, container,
    )
    args.extend(sub_maps)

    # Copy all streams — no transcoding (text subs -> mov_text for MP4)
    args.extend(_subtitle_codec_args(container, has_subs))

    lib_audio_count = sum(
        1 for s in lib_streams if s.get("codec_type") == "audio"
    )

    # Set language + title metadata on each new audio track
    for i, track in enumerate(audio_tracks):
        idx = lib_audio_count + i
        lang_norm = _normalize_lang(track.lang)
        args.extend([
            f"-metadata:s:a:{idx}", f"language={lang_norm}",
        ])
        args.extend([
            f"-metadata:s:a:{idx}", f"title=Enrichment ({lang_norm.upper()})",
        ])

    args.extend(sub_metadata)
    args.append(str(destination))
    return args


def build_video_upgrade_command(
    library: Path,
    candidate: Path,
    offset_seconds: float,
    destination: Path,
    *,
    selection: TrackSelection,
    extra_audio: Optional[List[_AudioTrack]] = None,
    extra_subs: Optional[List[_SubtitleCandidate]] = None,
) -> List[str]:
    """Build an ffmpeg command that upgrades video while preserving library audio.

    Takes video from the candidate (higher quality) and ALL audio from the
    library (including previously enriched tracks).  Library subtitles and
    the candidate's extra subtitles are re-selected under the subtitle
    policy.  Optionally also grabs additional audio tracks from the
    candidate that the library doesn't have.

    This is the inverse of ``build_crossmux_command``:
      - crossmux: library video + candidate audio
      - upgrade:  candidate video + library audio

    Args:
        library: Existing library file (source of audio + subs).
        candidate: Higher-quality file (source of video).
        offset_seconds: Chromaprint alignment offset.
        destination: Output path; its extension decides the container.
        selection: Media policy (subtitle rules are applied).
        extra_audio: Additional audio tracks from candidate to include
            (languages the library is missing).
        extra_subs: Additional subtitle tracks offered by the candidate.

    Raises:
        RemuxPolicyError: the library file cannot be probed.
    """
    lib_streams = _probe_library_or_refuse(library)
    container = container_for_path(destination)

    args = ["ffmpeg", "-y", "-hide_banner"]

    # Input 0: candidate file (provides video)
    args.extend(["-i", str(candidate)])

    # Input 1: library file with offset (provides audio + subs)
    # The offset aligns the library's audio with the candidate's video.
    if abs(offset_seconds) > 0.001:
        args.extend(["-itsoffset", f"{-offset_seconds:.3f}"])
    args.extend(["-i", str(library)])

    # Map video from candidate (input 0) — first real video only
    args.extend(["-map", "0:v:0"])

    # Map ALL audio from library (input 1) — preserves enriched tracks
    args.extend(["-map", "1:a"])

    # Map extra audio tracks from candidate (input 0) if any
    if extra_audio:
        for track in extra_audio:
            args.extend(["-map", f"0:{track.stream_index}"])

    sub_maps, sub_metadata, has_subs = _policy_subtitle_maps(
        lib_streams, 1, extra_subs, 0, selection, container,
    )
    args.extend(sub_maps)

    # Copy all streams — no transcoding (text subs -> mov_text for MP4)
    args.extend(_subtitle_codec_args(container, has_subs))

    # Set metadata on extra audio tracks from candidate
    if extra_audio:
        lib_audio_count = sum(
            1 for s in lib_streams if s.get("codec_type") == "audio"
        )
        for i, track in enumerate(extra_audio):
            idx = lib_audio_count + i
            lang_norm = _normalize_lang(track.lang)
            args.extend([f"-metadata:s:a:{idx}", f"language={lang_norm}"])

    args.extend(sub_metadata)
    args.append(str(destination))
    return args
