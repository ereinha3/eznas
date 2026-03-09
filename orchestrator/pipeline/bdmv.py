"""BDMV (Blu-ray disc) detection, MPLS playlist parsing, and CLPI language
metadata extraction.

Raw Blu-ray disc dumps contain ``.m2ts`` transport streams whose language
tags are always ``und`` when probed with ffprobe.  However, the CLIPINF
(``.clpi``) files in the BDMV structure contain full ISO 639 language codes
for every stream.

Some Blu-rays use **seamless branching**: the movie is split across multiple
``.m2ts`` clips, stitched together by an MPLS playlist.  This module parses
MPLS files to find the correct clip sequence for the main feature.

This module:
1. Detects BDMV directory structures in downloaded torrents.
2. Parses ``.mpls`` playlist files to find the main feature clip sequence.
3. Falls back to largest single ``.m2ts`` for non-branching discs.
4. Parses ``.clpi`` files to extract per-stream language metadata.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# CLPI stream type bytes (Blu-ray standard)
_CLPI_TYPE_PGS_SUB = 0x90   # Presentation Graphics subtitles
_CLPI_TYPE_TEXT_SUB = 0x92   # Text subtitles


@dataclass
class ClpiStream:
    """A single stream entry extracted from a CLPI file."""
    stream_type: str   # "video", "audio", or "subtitle"
    language: str      # ISO 639 3-letter code (e.g. "eng", "fra")
    codec_id: int      # Raw codec/type byte
    index: int         # Position among streams of the same type


@dataclass
class BdmvInfo:
    """Information about a detected BDMV structure."""
    bdmv_root: Path           # Path to the BDMV directory
    main_m2ts: Path           # Path to the main feature .m2ts file
    main_clip_id: str         # e.g. "00000" from 00000.m2ts
    streams: List[ClpiStream] # Parsed stream info from CLPI


@dataclass
class MainFeature:
    """The main feature of a BDMV disc — may be a single file or a playlist.

    For single-file features, ``clips`` has one entry and ``is_playlist``
    is False.  For seamless branching discs, ``clips`` contains the ordered
    list of .m2ts files and ``is_playlist`` is True.
    """
    clips: List[Path]                  # Ordered list of .m2ts file paths
    clip_ids: List[str]                # Corresponding clip IDs (stems)
    is_playlist: bool = False          # True if from MPLS playlist
    playlist_name: Optional[str] = None  # e.g. "00002.mpls"
    total_size: int = 0                # Combined size in bytes

    @property
    def primary_clip(self) -> Path:
        """The first (or only) clip — used for CLPI language lookup."""
        return self.clips[0]

    @property
    def primary_clip_id(self) -> str:
        return self.clip_ids[0]


def parse_mpls(mpls_path: Path) -> List[str]:
    """Parse an MPLS playlist file and extract clip IDs.

    The MPLS binary format references clip names (5-char ASCII IDs like
    "00001", "05002") in the PlayList section.

    Returns a list of clip IDs in playback order, or empty on failure.
    """
    try:
        data = mpls_path.read_bytes()
    except OSError:
        return []

    if len(data) < 12:
        return []

    magic = data[:4].decode("ascii", errors="replace")
    if magic != "MPLS":
        return []

    # PlayList section offset at byte 8 (big-endian uint32)
    pl_offset = int.from_bytes(data[8:12], "big")
    if pl_offset + 10 > len(data):
        return []

    pos = pl_offset
    # PlayList: length (4), reserved (2), num_items (2), num_sub_paths (2)
    pos += 4  # length
    pos += 2  # reserved
    if pos + 2 > len(data):
        return []
    num_items = int.from_bytes(data[pos : pos + 2], "big")
    pos += 2
    pos += 2  # num_sub_paths

    clips: List[str] = []
    for _ in range(num_items):
        if pos + 2 > len(data):
            break
        item_length = int.from_bytes(data[pos : pos + 2], "big")
        item_start = pos + 2

        # Guard: minimum valid PlayItem is ~10 bytes; 0 would cause infinite loop
        if item_length < 5:
            break

        if item_start + 5 > len(data):
            break

        # Clip name: 5-byte ASCII at start of PlayItem
        clip_name = data[item_start : item_start + 5].decode(
            "ascii", errors="replace"
        )
        clips.append(clip_name)

        pos = item_start + item_length
        if pos > len(data):
            break

    return clips


def find_main_feature_extended(bdmv_root: Path) -> Optional[MainFeature]:
    """Find the main feature of a BDMV disc, handling seamless branching.

    Strategy:
    1. Parse all MPLS playlists in BDMV/PLAYLIST/.
    2. For each playlist, resolve clip IDs to .m2ts files, sum their sizes.
    3. Pick the playlist with the largest total size (likely the longest cut).
    4. If no valid playlists found, fall back to the single largest .m2ts.

    A playlist is considered valid only if:
    - It references at least 2 distinct clips.
    - All referenced clips exist as .m2ts files.
    - Total size is >= 1 GB (filters out menus/trailers).
    """
    stream_dir = bdmv_root / "STREAM"
    playlist_dir = bdmv_root / "PLAYLIST"

    if not stream_dir.is_dir():
        return None

    best_playlist: Optional[MainFeature] = None

    # --- Strategy 1: MPLS playlists ---
    if playlist_dir.is_dir():
        for mpls_file in sorted(playlist_dir.glob("*.mpls")):
            clip_ids = parse_mpls(mpls_file)
            if not clip_ids:
                continue

            # Deduplicate while preserving order
            seen = set()
            unique_ids: List[str] = []
            for cid in clip_ids:
                if cid not in seen:
                    seen.add(cid)
                    unique_ids.append(cid)

            # Must reference at least 2 distinct clips to be a branching playlist
            if len(unique_ids) < 2:
                continue

            # Resolve to .m2ts files
            clips: List[Path] = []
            valid = True
            total_size = 0
            for cid in unique_ids:
                m2ts = stream_dir / f"{cid}.m2ts"
                if not m2ts.exists():
                    valid = False
                    break
                clips.append(m2ts)
                total_size += m2ts.stat().st_size

            if not valid:
                continue

            # Filter out tiny playlists (menus, trailers)
            if total_size < 1024 * 1024 * 1024:  # < 1 GB
                continue

            if best_playlist is None or total_size > best_playlist.total_size:
                best_playlist = MainFeature(
                    clips=clips,
                    clip_ids=unique_ids,
                    is_playlist=True,
                    playlist_name=mpls_file.name,
                    total_size=total_size,
                )

    # --- Check if the playlist is actually multi-file ---
    # If the "best" playlist only has one real large clip and many tiny ones,
    # it's not really seamless branching — fall through to single-file mode.
    if best_playlist and len(best_playlist.clips) >= 2:
        # Verify the playlist is actually longer than the single largest clip
        single_largest = max(
            stream_dir.glob("*.m2ts"),
            key=lambda p: p.stat().st_size,
            default=None,
        )
        if single_largest:
            largest_size = single_largest.stat().st_size
            # Only use playlist if it's substantially larger (>20%) than
            # the single largest clip — otherwise it's not seamless branching,
            # just a playlist with one main clip and some extras
            if best_playlist.total_size > largest_size * 1.2:
                print(
                    f"[bdmv] Seamless branching detected: playlist "
                    f"{best_playlist.playlist_name} with "
                    f"{len(best_playlist.clips)} clips "
                    f"({best_playlist.total_size / (1024**3):.1f} GB total)"
                )
                return best_playlist

    # --- Strategy 2: Single largest .m2ts file (non-branching disc) ---
    result = find_main_feature(bdmv_root)
    if result is None:
        return None

    main_m2ts, clip_id = result
    return MainFeature(
        clips=[main_m2ts],
        clip_ids=[clip_id],
        is_playlist=False,
        total_size=main_m2ts.stat().st_size,
    )


def detect_bdmv(path: Path, *, search_children: bool = True) -> Optional[Path]:
    """Check if a path contains a BDMV structure.

    Looks for ``BDMV/STREAM/`` under the given path.
    Returns the BDMV root directory, or None if not a Blu-ray.

    Args:
        path: Directory to check.
        search_children: If True (default), also search one level of child
            directories.  Set to False when the caller is already iterating
            over a parent directory to avoid accidentally finding BDMV
            structures belonging to unrelated sibling directories.
    """
    # Direct BDMV directory
    if path.is_dir() and path.name == "BDMV":
        stream_dir = path / "STREAM"
        if stream_dir.is_dir():
            return path
        return None

    # Directory containing BDMV/
    if path.is_dir():
        bdmv = path / "BDMV"
        if bdmv.is_dir() and (bdmv / "STREAM").is_dir():
            return bdmv

        # Search one level deeper (e.g. torrent_dir/MovieName/BDMV/)
        # This is safe when called on a torrent's own content directory,
        # but MUST NOT be used on a shared parent like /downloads/complete/
        # because it would find BDMV structures from other torrents.
        if search_children:
            for child in path.iterdir():
                if child.is_dir():
                    bdmv = child / "BDMV"
                    if bdmv.is_dir() and (bdmv / "STREAM").is_dir():
                        return bdmv

    return None


def find_main_feature(bdmv_root: Path) -> Optional[Tuple[Path, str]]:
    """Find the main feature .m2ts file (largest by size).

    Returns (path_to_m2ts, clip_id) or None.
    The clip_id is the stem of the file (e.g. "00000" for "00000.m2ts").
    """
    stream_dir = bdmv_root / "STREAM"
    if not stream_dir.is_dir():
        return None

    m2ts_files = list(stream_dir.glob("*.m2ts"))
    if not m2ts_files:
        return None

    # Sort by size, largest first
    m2ts_files.sort(key=lambda p: p.stat().st_size, reverse=True)
    main = m2ts_files[0]
    return main, main.stem


def parse_clpi(clpi_path: Path) -> List[ClpiStream]:
    """Parse a CLPI file and extract stream language metadata.

    The CLPI binary format stores stream entries in the "Program Info"
    section.  Each stream entry contains a type/codec byte followed by
    stream attributes including a 3-byte ISO 639 language code.

    Returns a list of ClpiStream entries (video, audio, subtitle).
    Returns an empty list if parsing fails.
    """
    try:
        data = clpi_path.read_bytes()
    except OSError:
        return []

    if len(data) < 8:
        return []

    # CLPI files start with "HDMV" magic
    magic = data[:4].decode("ascii", errors="replace")
    if magic != "HDMV":
        return []

    streams: List[ClpiStream] = []
    audio_idx = 0
    sub_idx = 0

    # Strategy: scan for stream entry patterns in the binary data.
    # Each stream entry in the Program Info section follows the pattern:
    #   [length byte] [PID bytes] [coding_type] [format/rate] [language 3 bytes]
    #
    # We look for coding_type values we recognize and extract the language
    # code that follows at a known offset.
    i = 0
    while i < len(data) - 10:
        byte = data[i]

        # Audio stream entry: coding_type 0x81 (or 0x83, 0x84, 0x85, 0x86 for secondary)
        if byte in (0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0xA1, 0xA2):
            # Check if the next byte looks like audio format (4 bits format + 4 bits rate)
            if i + 4 < len(data):
                next_byte = data[i + 1]
                audio_format = (next_byte >> 4) & 0x0F
                # Valid audio formats: 1=mono, 3=stereo, 6=multi, 12=combo
                if audio_format in (1, 3, 6, 12):
                    # Language code is at offset +2, +3, +4
                    lang_bytes = data[i + 2: i + 5]
                    try:
                        lang = lang_bytes.decode("ascii")
                        if lang.isalpha() and len(lang) == 3:
                            if byte in (0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86):
                                streams.append(ClpiStream(
                                    stream_type="audio",
                                    language=lang.lower(),
                                    codec_id=byte,
                                    index=audio_idx,
                                ))
                                audio_idx += 1
                            i += 5
                            continue
                    except (UnicodeDecodeError, ValueError):
                        pass

        # PGS subtitle entry: coding_type 0x90
        if byte == _CLPI_TYPE_PGS_SUB:
            # Next byte: 4 bits unused + 4 bits unused, then 3 bytes language
            if i + 4 < len(data):
                lang_bytes = data[i + 2: i + 5]
                try:
                    lang = lang_bytes.decode("ascii")
                    if lang.isalpha() and len(lang) == 3:
                        streams.append(ClpiStream(
                            stream_type="subtitle",
                            language=lang.lower(),
                            codec_id=byte,
                            index=sub_idx,
                        ))
                        sub_idx += 1
                        i += 5
                        continue
                except (UnicodeDecodeError, ValueError):
                    pass

        # Text subtitle: 0x92
        if byte == _CLPI_TYPE_TEXT_SUB:
            if i + 4 < len(data):
                lang_bytes = data[i + 2: i + 5]
                try:
                    lang = lang_bytes.decode("ascii")
                    if lang.isalpha() and len(lang) == 3:
                        streams.append(ClpiStream(
                            stream_type="subtitle",
                            language=lang.lower(),
                            codec_id=byte,
                            index=sub_idx,
                        ))
                        sub_idx += 1
                        i += 5
                        continue
                except (UnicodeDecodeError, ValueError):
                    pass

        i += 1

    return streams


def parse_clpi_for_clip(bdmv_root: Path, clip_id: str) -> List[ClpiStream]:
    """Parse the CLPI file corresponding to a clip ID.

    Looks for ``BDMV/CLIPINF/{clip_id}.clpi``.
    """
    clpi_path = bdmv_root / "CLIPINF" / f"{clip_id}.clpi"
    if not clpi_path.exists():
        return []
    return parse_clpi(clpi_path)


def get_bdmv_stream_languages(
    bdmv_root: Path, clip_id: str
) -> Optional[List[Dict[str, str]]]:
    """Get per-stream language data for a BDMV clip in the format expected
    by ``build_ffmpeg_command(stream_languages=...)``.

    Returns a list of dicts with keys ``type``, ``lang``, ``index`` suitable
    for passing to ``build_ffmpeg_command(stream_languages=result)``.

    The ``index`` values here are *relative* indices within each stream type,
    but ``build_ffmpeg_command`` needs *absolute* ffprobe indices.  The caller
    must map CLPI stream indices to ffprobe stream indices after probing.

    Returns None if CLPI data cannot be extracted.
    """
    streams = parse_clpi_for_clip(bdmv_root, clip_id)
    if not streams:
        return None

    result: List[Dict[str, str]] = []
    for s in streams:
        if s.stream_type in ("audio", "subtitle"):
            result.append({
                "type": s.stream_type,
                "lang": s.language,
                # index will be remapped by the caller after ffprobe
                "clpi_index": str(s.index),
            })
    return result


def map_clpi_to_ffprobe_indices(
    source: Path,
    clpi_streams: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    """Map CLPI relative stream indices to ffprobe absolute indices.

    Probes the source file with ffprobe and matches audio/subtitle streams
    in order to the CLPI entries, producing the ``stream_languages`` format
    expected by ``build_ffmpeg_command()``.
    """
    import json
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                str(source),
            ],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        ffprobe_streams = data.get("streams", [])
    except Exception:
        return []

    # Separate ffprobe streams by type, preserving order
    audio_indices: List[str] = []
    sub_indices: List[str] = []
    for stream in ffprobe_streams:
        codec_type = stream.get("codec_type", "")
        idx = str(stream.get("index", ""))
        if codec_type == "audio":
            audio_indices.append(idx)
        elif codec_type == "subtitle":
            sub_indices.append(idx)

    # Separate CLPI entries by type, preserving order
    clpi_audio = [s for s in clpi_streams if s.get("type") == "audio"]
    clpi_subs = [s for s in clpi_streams if s.get("type") == "subtitle"]

    mapped: List[Dict[str, str]] = []

    # Map audio: CLPI audio[i] -> ffprobe audio_indices[i]
    for i, entry in enumerate(clpi_audio):
        if i < len(audio_indices):
            mapped.append({
                "type": "audio",
                "lang": entry.get("lang", "und"),
                "index": audio_indices[i],
            })

    # Map subtitles: CLPI sub[i] -> ffprobe sub_indices[i]
    for i, entry in enumerate(clpi_subs):
        if i < len(sub_indices):
            mapped.append({
                "type": "subtitle",
                "lang": entry.get("lang", "und"),
                "index": sub_indices[i],
            })

    return mapped
