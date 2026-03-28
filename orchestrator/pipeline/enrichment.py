"""Media enrichment engine — cross-mux missing audio tracks into library files.

Scans the media library for files missing desired audio languages (e.g.,
English dub for Japanese anime), searches Prowlarr for alternate releases
containing the missing audio, downloads candidates, uses chromaprint
acoustic fingerprinting to compute precise audio alignment, and cross-muxes
the missing track into the library file with atomic replacement.

Phases:
  - **Gap scan**: Identify library files missing target audio languages.
  - **Search**: Query Prowlarr for dual-audio or dubbed releases.
  - **Download**: Add best candidate to qBittorrent "enrichment" category.
  - **Process**: Fingerprint, correlate, cross-mux, verify, atomically replace.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx

from ..models import EnrichmentConfig, StackConfig, VPN_ROUTED_SERVICES
from ..storage import ConfigRepository
from .backfill import ProwlarrResult
from .chromaprint import validate_and_align
from .languages import arr_language_to_iso
from .remux import (
    RESOLUTION_TARGET_SCORES,
    VideoQuality,
    _normalize_lang,
    build_crossmux_command,
    build_video_upgrade_command,
    find_all_enrichment_audio_tracks,
    find_best_audio_track_for_language,
    find_useful_candidate_subtitles,
    probe_raw_streams,
    probe_streams,
    probe_video_quality,
)
from .worker import VIDEO_EXTENSIONS, parse_tv_episode

log = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class EnrichmentCandidate:
    """A library file that needs audio enrichment and/or video upgrade."""

    path: Path
    title: str
    year: Optional[int]
    original_language: str
    missing_languages: List[str]
    arr_id: int
    service: str  # "radarr" or "sonarr"
    category: str  # "movies" or "tv"
    # Video quality fields
    video_below_target: bool = False
    current_resolution: str = ""
    current_codec: str = ""
    current_video_score: int = 0


@dataclass
class EnrichmentGrab:
    """Metadata for an enrichment download in progress."""

    torrent_name: str
    target_path: str  # Library file to enrich
    target_language: str  # Audio language we want from this download
    arr_id: int
    service: str
    timestamp: int


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

_DUAL_AUDIO_RE = re.compile(
    r"\b(dual[\s._-]?audio|multi[\s._-]?audio|multi)\b",
    re.IGNORECASE,
)
_QUALITY_PATTERNS = {
    "2160p": 25,
    "4k": 25,
    "uhd": 20,
    "1080p": 20,
    "720p": 5,
}
_GOOD_CODEC = re.compile(r"\b(x265|hevc|h\.?265)\b", re.IGNORECASE)
_BAD_QUALITY = re.compile(r"\b(cam|ts|telesync|hdcam|hdts)\b", re.IGNORECASE)


def _score_enrichment_result(
    result: ProwlarrResult,
    title: str,
    year: Optional[int],
    min_seeders: int,
    *,
    video_upgrade: bool = False,
) -> int:
    """Score a Prowlarr result for enrichment.

    When ``video_upgrade`` is True, prioritizes resolution/codec over
    dual-audio.  Otherwise prioritizes dual-audio/dub indicators.
    """
    score = 0
    title_lower = result.title.lower()
    size_gb = result.size / (1024**3)

    # --- Hard filters ---
    if result.seeders < min_seeders:
        return -9999
    if size_gb > 80:
        return -9999
    if _BAD_QUALITY.search(result.title):
        return -9999

    # --- Title relevance ---
    target_words = set(re.findall(r"[a-z0-9]+", title.lower()))
    result_words = set(re.findall(r"[a-z0-9]+", title_lower))
    if target_words:
        overlap = len(target_words & result_words) / len(target_words)
        if overlap < 0.4:
            return -9999

    # --- Year match ---
    if year:
        year_matches = re.findall(r"\b((?:19|20)\d{2})\b", result.title)
        for ym in year_matches:
            yr = int(ym)
            if abs(yr - year) <= 1:
                score += 30
                break
            elif abs(yr - year) > 3:
                return -9999

    # --- Dual audio bonus (higher when audio-only, lower when video upgrade) ---
    if _DUAL_AUDIO_RE.search(result.title):
        score += 30 if video_upgrade else 80

    # --- Language indicators ---
    if not video_upgrade:
        for lang_kw in ("english", "eng", "dubbed", "dub"):
            if lang_kw in title_lower:
                score += 20
                break

    # --- Quality (higher weight when upgrading video) ---
    quality_multiplier = 3 if video_upgrade else 1
    for pattern, pts in _QUALITY_PATTERNS.items():
        if pattern in title_lower:
            score += pts * quality_multiplier
            break

    if _GOOD_CODEC.search(result.title):
        score += 30 if video_upgrade else 10

    # --- Video upgrade: bonus for remux/bluray indicators ---
    if video_upgrade:
        for kw in ("remux", "bluray", "blu-ray", "bdremux"):
            if kw in title_lower:
                score += 40
                break
        # HDR bonus
        if re.search(r"\b(hdr|hdr10|dolby.?vision|dv)\b", title_lower):
            score += 25

    # --- Seeders ---
    if result.seeders >= 20:
        score += 20
    elif result.seeders >= 10:
        score += 10
    elif result.seeders >= 5:
        score += 5

    # --- Reasonable size ---
    if 1 <= size_gb <= 60:
        score += 5

    return score


# Maximum number of entries in the enrichment processed state.
# When exceeded, the oldest failed entries are evicted first.
_MAX_PROCESSED_ENTRIES = 5000


def _compact_processed(processed: dict) -> None:
    """Evict oldest failed entries when processed dict exceeds max size."""
    if len(processed) <= _MAX_PROCESSED_ENTRIES:
        return

    # Separate successful and failed entries
    failed_keys = [
        (k, v.get("timestamp", 0))
        for k, v in processed.items()
        if isinstance(v, dict) and v.get("status") == "failed"
    ]
    # Sort by timestamp ascending (oldest first)
    failed_keys.sort(key=lambda x: x[1])

    # Evict oldest failed entries until under limit
    to_remove = len(processed) - _MAX_PROCESSED_ENTRIES
    for key, _ in failed_keys[:to_remove]:
        del processed[key]


# ---------------------------------------------------------------------------
# Enrichment Engine
# ---------------------------------------------------------------------------


class EnrichmentEngine:
    """Orchestrates the media enrichment pipeline.

    Two entry points, both called from the pipeline runner's ``_tick()``:

    - ``process_completed(config)``: Runs every tick.  Checks the qBittorrent
      "enrichment" category for completed downloads, then fingerprints,
      cross-muxes, verifies, and atomically replaces the library file.

    - ``maybe_run(config)``: Runs on a configurable interval (default 24h).
      Scans the library for files missing desired audio, searches Prowlarr
      for candidates, and adds the best to qBittorrent.
    """

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    # ------------------------------------------------------------------
    # Path resolution (container-aware)
    # ------------------------------------------------------------------

    def _resolve_pool_root(self, config: StackConfig) -> Path:
        """Resolve the pool root path, handling container vs host path differences.

        The config stores host paths (e.g. /mnt/pool/media), but inside the
        container the media is mounted at /data.  This method checks both.
        """
        for candidate in (Path("/data"), Path(config.paths.pool)):
            if candidate.exists():
                return candidate
        return Path(config.paths.pool)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_completed(self, config: StackConfig) -> None:
        """Check enrichment downloads and process any that have completed."""
        enrich_cfg = config.services.pipeline.enrichment
        if not enrich_cfg.enabled:
            return

        state = self.repo.load_enrichment_state()
        grabbed = state.get("grabbed", {})
        if not grabbed:
            return

        secrets = self.repo.load_secrets()
        qb_host = self._resolve_host("qbittorrent", config)
        qb_creds = secrets.get("qbittorrent", {})
        qb_cfg = config.services.qbittorrent

        # Get completed torrents in enrichment category
        completed = self._get_completed_enrichment_torrents(
            qb_host,
            qb_creds,
            qb_cfg,
        )
        if not completed:
            return

        processed_any = False
        for torrent in completed:
            name = torrent.get("name", "")
            torrent_hash = torrent.get("hash", "")
            content_path = torrent.get("content_path", "")

            # Find the grab metadata for this torrent
            grab_key = self._find_grab_key(name, grabbed)
            if grab_key is None:
                log.debug(
                    "enrichment: no grab metadata for completed torrent: %s", name[:60]
                )
                continue

            grab_info = grabbed[grab_key]
            target_path = Path(grab_info["target_path"])
            target_lang = grab_info["target_language"]

            if not target_path.exists():
                log.warning(
                    "enrichment: target file no longer exists: %s",
                    target_path.name,
                )
                self._cleanup_torrent(qb_host, qb_creds, qb_cfg, torrent_hash)
                del grabbed[grab_key]
                processed_any = True
                continue

            # Find the actual media file in the torrent (with episode matching for TV)
            candidate_path = self._find_media_file(
                Path(content_path),
                target_path=target_path,
            )
            if candidate_path is None:
                log.warning("enrichment: no media file found in %s", content_path)
                self._mark_failed(state, grab_key, "no media file in download")
                self._cleanup_torrent(qb_host, qb_creds, qb_cfg, torrent_hash)
                del grabbed[grab_key]
                processed_any = True
                continue

            is_video_upgrade = grab_info.get("video_upgrade", False)

            # ── Batch processing for TV season packs ──────────────────
            # When a season pack has multiple episodes, process ALL
            # library files that need enrichment — not just the original
            # target.  This avoids downloading the same pack repeatedly.
            pack_targets = self._find_all_pack_targets(
                config, enrich_cfg, state,
                Path(content_path), grab_info,
            )

            if not pack_targets:
                # Fall back to single target
                pack_targets = [(target_path, target_lang, candidate_path)]

            succeeded = 0
            failed_count = 0
            for lib_path, lang, cand_path in pack_targets:
                log.info(
                    "enrichment: processing %s → target=%s lang=%s%s",
                    cand_path.name[:60], lib_path.name[:60], lang,
                    " [VIDEO UPGRADE]" if is_video_upgrade else "",
                )

                ok = self._process_candidate(
                    config, enrich_cfg, state,
                    lib_path, cand_path, lang, grab_key,
                )

                if ok:
                    succeeded += 1
                    log.info(
                        "enrichment: SUCCESS — added %s audio to %s",
                        lang, lib_path.name,
                    )
                else:
                    failed_count += 1

            log.info(
                "enrichment: pack complete — %d/%d episodes enriched",
                succeeded, succeeded + failed_count,
            )

            # Clean up torrent after processing ALL episodes
            self._cleanup_torrent(qb_host, qb_creds, qb_cfg, torrent_hash)
            del grabbed[grab_key]
            processed_any = True

        if processed_any:
            state["grabbed"] = grabbed
            _compact_processed(state.get("processed", {}))
            self.repo.save_enrichment_state(state)

    def maybe_run(self, config: StackConfig) -> None:
        """Search for enrichment candidates on a configurable interval."""
        enrich_cfg = config.services.pipeline.enrichment
        if not enrich_cfg.enabled or not enrich_cfg.search_interval_hours:
            return

        state = self.repo.load_enrichment_state()
        last_run = state.get("last_search", 0)
        interval = enrich_cfg.search_interval_hours * 3600

        if time.time() - last_run < interval:
            return

        log.info("enrichment: starting search cycle")
        grabbed_count = self._run_search_cycle(config, enrich_cfg, state)
        state["last_search"] = int(time.time())
        self.repo.save_enrichment_state(state)

        if grabbed_count:
            log.info("enrichment: grabbed %d candidates", grabbed_count)

    # ------------------------------------------------------------------
    # Gap scanning
    # ------------------------------------------------------------------

    def scan_library_gaps(
        self,
        config: StackConfig,
        enrich_cfg: EnrichmentConfig,
    ) -> List[EnrichmentCandidate]:
        """Identify library files missing desired audio languages.

        The ``target_languages`` config supports a special ``"original"``
        sentinel which resolves to the actual original language of each
        media item (looked up from Radarr/Sonarr API).  For example,
        ``["eng", "original"]`` means: every file should have both English
        AND its original language audio.  A Japanese anime with only an
        English dub would be flagged as missing Japanese (the original).
        """
        pool = self._resolve_pool_root(config)
        candidates: List[EnrichmentCandidate] = []

        # Separate the "original" sentinel from concrete language codes
        has_original_target = "original" in [
            l.lower() for l in enrich_cfg.target_languages
        ]
        concrete_targets = {
            _normalize_lang(l)
            for l in enrich_cfg.target_languages
            if l.lower() != "original"
        }

        # Load original languages from arr APIs
        orig_langs = self._load_arr_original_languages(config)

        state = self.repo.load_enrichment_state()
        processed = state.get("processed", {})
        grabbed = state.get("grabbed", {})

        # Paths already being downloaded for
        active_targets = {g["target_path"] for g in grabbed.values()}

        for category, subdir in [("movies", "movies"), ("tv", "tv")]:
            cat_dir = pool / subdir
            if not cat_dir.exists():
                continue

            for path in self._walk_video_files(cat_dir):
                path_str = str(path)

                # Skip actively downloading
                if path_str in active_targets:
                    continue

                # Skip already processed — but allow retry for failed items
                # after a 7-day cooldown (transient failures like network
                # hiccups, fpcalc timeouts, etc. should not permanently
                # block enrichment).
                proc_entry = processed.get(path_str)
                if proc_entry:
                    status = proc_entry.get("status", "ok")
                    if status == "ok":
                        continue  # Successfully enriched — skip forever
                    # Failed entries: retry after 7 days
                    failed_at = proc_entry.get("timestamp", 0)
                    if time.time() - failed_at < 7 * 86400:
                        continue  # Still in cooldown

                # Probe the file
                info = probe_streams(path)
                if info is None or not info.has_video:
                    continue

                # Resolve original language and title from arr data
                orig_lang, title, year, arr_id, service = self._resolve_arr_info(
                    path,
                    orig_langs,
                    config,
                )

                # Build the per-file target set: concrete languages + resolved original
                file_targets = set(concrete_targets)
                if has_original_target and orig_lang:
                    file_targets.add(_normalize_lang(orig_lang))

                # Remove "und" — can't meaningfully search for undetermined
                file_targets.discard("und")

                # Check which target languages are missing from this file
                file_audio = {_normalize_lang(l) for l in info.audio_languages}
                missing = file_targets - file_audio

                # Check video quality gap
                video_below = False
                current_res = ""
                current_codec = ""
                current_score = 0

                if enrich_cfg.upgrade_video:
                    vq = probe_video_quality(path)
                    if vq:
                        current_res = vq.resolution_label
                        current_codec = vq.codec
                        current_score = vq.score
                        target_min = RESOLUTION_TARGET_SCORES.get(
                            enrich_cfg.target_resolution,
                            200,
                        )
                        if vq.score < target_min:
                            video_below = True

                # Skip if no audio gap AND no video gap
                if not missing and not video_below:
                    continue

                candidates.append(
                    EnrichmentCandidate(
                        path=path,
                        title=title or path.parent.name,
                        year=year,
                        original_language=orig_lang or "und",
                        missing_languages=sorted(missing),
                        arr_id=arr_id or 0,
                        service=service or category,
                        category=category,
                        video_below_target=video_below,
                        current_resolution=current_res,
                        current_codec=current_codec,
                        current_video_score=current_score,
                    )
                )

        log.info(
            "enrichment: scanned library — %d files need audio enrichment",
            len(candidates),
        )
        return candidates

    # ------------------------------------------------------------------
    # Search & download
    # ------------------------------------------------------------------

    def _run_search_cycle(
        self,
        config: StackConfig,
        enrich_cfg: EnrichmentConfig,
        state: dict,
    ) -> int:
        """Search Prowlarr for enrichment candidates and add to qBT."""
        candidates = self.scan_library_gaps(config, enrich_cfg)
        if not candidates:
            return 0

        secrets = self.repo.load_secrets()
        prowlarr_key = secrets.get("prowlarr", {}).get("api_key", "")
        prowlarr_host = self._resolve_host("prowlarr", config)
        qb_host = self._resolve_host("qbittorrent", config)
        qb_creds = secrets.get("qbittorrent", {})
        qb_cfg = config.services.qbittorrent

        if not prowlarr_key:
            log.warning("enrichment: no Prowlarr API key — skipping search")
            return 0

        # Get existing qBT torrent names to avoid duplicates
        existing_names = self._get_qbt_names(qb_host, qb_creds, qb_cfg)

        grabbed = state.setdefault("grabbed", {})
        grabbed_count = 0

        # Shuffle to avoid alphabetical bias — without this, files starting
        # with 'A' are always checked first and files at the end of the
        # alphabet never get enriched.
        import random
        random.shuffle(candidates)

        for candidate in candidates[: enrich_cfg.max_grabs_per_cycle * 3]:
            if grabbed_count >= enrich_cfg.max_grabs_per_cycle:
                break

            # Build search queries
            queries = self._build_queries(candidate, enrich_cfg)

            # Search Prowlarr
            results = self._search_prowlarr(
                prowlarr_host,
                prowlarr_key,
                queries,
            )
            if not results:
                continue

            # Score and pick best
            scored = [
                (
                    r,
                    _score_enrichment_result(
                        r,
                        candidate.title,
                        candidate.year,
                        enrich_cfg.min_seeders,
                        video_upgrade=candidate.video_below_target,
                    ),
                )
                for r in results
            ]
            scored = [(r, s) for r, s in scored if s > 0]
            scored.sort(key=lambda x: x[1], reverse=True)

            if not scored:
                log.debug(
                    "enrichment: no viable results for '%s'",
                    candidate.title[:40],
                )
                continue

            best, best_score = scored[0]

            # Check if already in qBT
            if best.title in existing_names:
                log.debug("enrichment: already in qBT: %s", best.title[:60])
                continue

            # Add to qBittorrent
            log.info(
                "enrichment: grabbing '%s' (score=%d, seeders=%d) for '%s' [%s]",
                best.title[:60],
                best_score,
                best.seeders,
                candidate.title[:40],
                ", ".join(candidate.missing_languages),
            )

            added = self._add_to_qbt(
                qb_host,
                qb_creds,
                qb_cfg,
                best,
                "enrichment",
            )
            if not added:
                continue

            # Record grab metadata (store ALL missing languages + video info)
            grab_key = best.title[:120]
            grabbed[grab_key] = {
                "torrent_name": best.title,
                "target_path": str(candidate.path),
                "target_language": candidate.missing_languages[0]
                if candidate.missing_languages
                else "",
                "all_missing_languages": candidate.missing_languages,
                "video_upgrade": candidate.video_below_target,
                "arr_id": candidate.arr_id,
                "service": candidate.service,
                "category": candidate.category,
                "timestamp": int(time.time()),
            }
            grabbed_count += 1
            existing_names.add(best.title)

        return grabbed_count

    def _build_queries(
        self,
        candidate: EnrichmentCandidate,
        enrich_cfg: EnrichmentConfig,
    ) -> List[str]:
        """Build Prowlarr search queries for an enrichment candidate.

        Prioritizes queries based on what the candidate needs:
        - Video upgrade: search for higher resolution / better codec
        - Audio only: search for dual-audio / dub releases
        - Both: combine quality + audio keywords
        """
        base = candidate.title
        if candidate.year:
            base = f"{base} {candidate.year}"

        queries = []

        if candidate.video_below_target:
            # Video upgrade queries — prioritize quality
            target = enrich_cfg.target_resolution
            if target == "2160p":
                queries.append(f"{base} 2160p remux")
                queries.append(f"{base} 4K")
            elif target == "1080p":
                queries.append(f"{base} 1080p bluray")
                queries.append(f"{base} 1080p remux")
            else:
                queries.append(f"{base} {target}")

            # If also missing audio, combine
            if candidate.missing_languages:
                queries.append(f"{base} {target} dual audio")
        else:
            # Audio-only enrichment queries
            for suffix in enrich_cfg.search_queries:
                queries.append(f"{base} {suffix}")

        return queries[:3]  # Max 3 queries per candidate

    # ------------------------------------------------------------------
    # Process completed downloads
    # ------------------------------------------------------------------

    def _process_candidate(
        self,
        config: StackConfig,
        enrich_cfg: EnrichmentConfig,
        state: dict,
        library_path: Path,
        candidate_path: Path,
        target_language: str,
        grab_key: str,
    ) -> bool:
        """Fingerprint, cross-mux, verify, and replace.  Returns True on success.

        Extracts ALL matching target audio languages from the candidate
        (not just the primary one), plus any useful subtitle tracks that
        the library is missing.
        """
        import subprocess

        # 1. Probe both files
        cand_streams = probe_raw_streams(candidate_path)
        if cand_streams is None:
            log.error("enrichment: failed to probe candidate %s", candidate_path.name)
            self._mark_failed(state, grab_key, "probe failed")
            return False

        lib_info = probe_streams(library_path)
        if lib_info is None:
            log.error("enrichment: failed to probe library file %s", library_path.name)
            self._mark_failed(state, grab_key, "library probe failed")
            return False

        # 2. Find ALL matching audio tracks from candidate
        # Build the full target set (same logic as gap scanner)
        target_langs = {
            _normalize_lang(l)
            for l in enrich_cfg.target_languages
            if l.lower() != "original"
        }
        # Resolve "original" if configured
        if "original" in [l.lower() for l in enrich_cfg.target_languages]:
            orig_langs = self._load_arr_original_languages(config)
            orig_info = self._resolve_arr_info(library_path, orig_langs, config)
            if orig_info[0]:
                target_langs.add(_normalize_lang(orig_info[0]))
        target_langs.discard("und")

        lib_audio_langs = {_normalize_lang(l) for l in lib_info.audio_languages}

        audio_tracks = find_all_enrichment_audio_tracks(
            cand_streams,
            target_langs,
            lib_audio_langs,
        )

        if not audio_tracks:
            # Fall back to single-language search for the primary target
            best_track = find_best_audio_track_for_language(
                cand_streams,
                target_language,
            )
            if best_track is None:
                log.warning(
                    "enrichment: no target audio tracks in candidate %s",
                    candidate_path.name,
                )
                self._mark_failed(state, grab_key, "no target audio in candidate")
                return False
            audio_tracks = [best_track]

        languages_adding = [_normalize_lang(t.lang) for t in audio_tracks]
        log.info(
            "enrichment: found %d audio tracks to add [%s] from %s",
            len(audio_tracks),
            ", ".join(f"{t.lang}({t.codec}/{t.channels}ch)" for t in audio_tracks),
            candidate_path.name[:40],
        )

        # 3. Find useful subtitle tracks from candidate
        lib_sub_langs = {_normalize_lang(l) for l in lib_info.subtitle_languages}
        sub_tracks = find_useful_candidate_subtitles(
            cand_streams,
            lib_sub_langs,
            target_langs,
        )
        if sub_tracks:
            log.info(
                "enrichment: also extracting %d subtitle tracks [%s]",
                len(sub_tracks),
                ", ".join(
                    f"{s.lang}({'forced' if s.forced else s.codec})" for s in sub_tracks
                ),
            )

        # 3.5. Duration guard — reject fundamentally different cuts before
        # expensive chromaprint fingerprinting.  60-second tolerance handles
        # minor intro/outro differences; larger deltas indicate wrong content.
        _DURATION_GUARD_SECONDS = 60
        lib_duration = self._get_duration(library_path)
        cand_duration = self._get_duration(candidate_path)
        if lib_duration and cand_duration:
            delta = abs(lib_duration - cand_duration)
            if delta > _DURATION_GUARD_SECONDS:
                log.warning(
                    "enrichment: duration guard REJECTED — library=%.0fs "
                    "candidate=%.0fs (delta=%.0fs > %ds) for %s",
                    lib_duration,
                    cand_duration,
                    delta,
                    _DURATION_GUARD_SECONDS,
                    library_path.name,
                )
                self._mark_failed(state, grab_key, f"duration mismatch ({delta:.0f}s)")
                return False

        # 4. Chromaprint fingerprint + correlation
        alignment = validate_and_align(
            library_path,
            candidate_path,
            threshold=enrich_cfg.correlation_threshold,
            duration=enrich_cfg.fingerprint_duration_seconds,
        )
        if alignment is None:
            log.warning(
                "enrichment: chromaprint rejected — correlation below %.2f for %s",
                enrich_cfg.correlation_threshold,
                library_path.name,
            )
            self._mark_failed(state, grab_key, "chromaprint correlation too low")
            return False

        log.info(
            "enrichment: chromaprint PASSED — score=%.4f offset=%.2fs",
            alignment.score,
            alignment.offset_seconds,
        )

        # 5. Determine operation type and build ffmpeg command
        grab_info = state.get("grabbed", {}).get(grab_key, {})
        is_video_upgrade = grab_info.get("video_upgrade", False)

        # For video upgrades: verify candidate actually has better video
        if is_video_upgrade:
            lib_vq = probe_video_quality(library_path)
            cand_vq = probe_video_quality(candidate_path)
            if lib_vq and cand_vq:
                if cand_vq.score <= lib_vq.score:
                    log.warning(
                        "enrichment: candidate video (%s, score=%d) is not "
                        "better than library (%s, score=%d) — skipping upgrade",
                        cand_vq.resolution_label,
                        cand_vq.score,
                        lib_vq.resolution_label,
                        lib_vq.score,
                    )
                    is_video_upgrade = False  # Fall back to audio-only
                else:
                    log.info(
                        "enrichment: VIDEO UPGRADE %s(%s/%d) → %s(%s/%d)",
                        library_path.name[:30],
                        lib_vq.resolution_label,
                        lib_vq.score,
                        candidate_path.name[:30],
                        cand_vq.resolution_label,
                        cand_vq.score,
                    )

        staging = library_path.with_suffix(".enrichment.tmp.mkv")

        if is_video_upgrade:
            # Video upgrade: take video from candidate, audio/subs from library
            # Also grab any extra audio/subs from candidate we don't have
            cmd = build_video_upgrade_command(
                library=library_path,
                candidate=candidate_path,
                offset_seconds=alignment.offset_seconds,
                destination=staging,
                extra_audio=audio_tracks if audio_tracks else None,
                extra_subs=sub_tracks if sub_tracks else None,
            )
            operation = "video upgrade"
        else:
            # Audio-only enrichment: keep library video, add candidate audio
            if not audio_tracks:
                log.warning(
                    "enrichment: no audio tracks to add and no video upgrade — nothing to do",
                )
                self._mark_failed(state, grab_key, "nothing to enrich")
                return False
            cmd = build_crossmux_command(
                library=library_path,
                candidate=candidate_path,
                audio_tracks=audio_tracks,
                subtitle_tracks=sub_tracks or None,
                offset_seconds=alignment.offset_seconds,
                destination=staging,
            )
            operation = "audio cross-mux"

        log.info("enrichment: running %s ffmpeg command", operation)
        log.debug("enrichment: cmd=%s", " ".join(cmd))

        # 6. Execute ffmpeg
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour max
            )
            if result.returncode != 0:
                log.error(
                    "enrichment: ffmpeg failed (rc=%d): %s",
                    result.returncode,
                    result.stderr[-500:] if result.stderr else "no output",
                )
                self._cleanup_staging(staging)
                self._mark_failed(state, grab_key, "ffmpeg failed")
                return False
        except subprocess.TimeoutExpired:
            log.error("enrichment: ffmpeg timed out after 1 hour")
            self._cleanup_staging(staging)
            self._mark_failed(state, grab_key, "ffmpeg timeout")
            return False

        # 7. Verify output
        if not staging.exists():
            log.error("enrichment: staging file not created")
            self._mark_failed(state, grab_key, "staging file missing")
            return False

        out_info = probe_streams(staging)
        if out_info is None or not out_info.has_video:
            log.error("enrichment: output file has no video")
            self._cleanup_staging(staging)
            self._mark_failed(state, grab_key, "output has no video")
            return False

        # Verification depends on operation type
        out_audio = {_normalize_lang(l) for l in out_info.audio_languages}

        if is_video_upgrade:
            # For video upgrades: verify library audio tracks are preserved
            for lib_lang in lib_info.audio_languages:
                if _normalize_lang(lib_lang) not in out_audio and lib_lang != "und":
                    log.error(
                        "enrichment: library audio language %s lost in upgrade output",
                        lib_lang,
                    )
                    self._cleanup_staging(staging)
                    self._mark_failed(state, grab_key, f"lost audio {lib_lang}")
                    return False

            # Audio count should be at least as many as library (preserved)
            if out_info.audio_count < lib_info.audio_count:
                log.error(
                    "enrichment: output lost audio tracks (%d → %d)",
                    lib_info.audio_count,
                    out_info.audio_count,
                )
                self._cleanup_staging(staging)
                self._mark_failed(state, grab_key, "lost audio tracks in upgrade")
                return False
        else:
            # For audio enrichment: verify new languages are present
            added_langs = [l for l in languages_adding if l in out_audio]
            if not added_langs:
                log.error(
                    "enrichment: none of target languages %s found in output (got: %s)",
                    languages_adding,
                    out_audio,
                )
                self._cleanup_staging(staging)
                self._mark_failed(state, grab_key, "target languages not in output")
                return False

            # Audio count should increase
            if out_info.audio_count <= lib_info.audio_count:
                log.error(
                    "enrichment: output has %d audio tracks, expected more than %d",
                    out_info.audio_count,
                    lib_info.audio_count,
                )
                self._cleanup_staging(staging)
                self._mark_failed(state, grab_key, "audio count did not increase")
                return False

        # Sanity: for video upgrades the file may be much larger (that's the
        # point), so only reject if dramatically smaller.  For audio-only
        # the output should be at least ~90% of the original.
        lib_size = library_path.stat().st_size
        out_size = staging.stat().st_size
        min_ratio = 0.5 if is_video_upgrade else 0.9
        if out_size < lib_size * min_ratio:
            log.error(
                "enrichment: output (%.2f GB) is significantly smaller than "
                "library file (%.2f GB) — aborting",
                out_size / (1024**3),
                lib_size / (1024**3),
            )
            self._cleanup_staging(staging)
            self._mark_failed(state, grab_key, "output too small")
            return False

        # 8. Atomic replacement
        new_audio = out_info.audio_count - lib_info.audio_count
        new_subs = out_info.subtitle_count - lib_info.subtitle_count

        if is_video_upgrade:
            staging_vq = probe_video_quality(staging)
            vq_label = (
                f"{staging_vq.resolution_label}/{staging_vq.codec}"
                if staging_vq
                else "unknown"
            )
            log.info(
                "enrichment: VIDEO UPGRADE replacing %s "
                "(%.2f GB → %.2f GB, %s, +%d audio, +%d subs)",
                library_path.name,
                lib_size / (1024**3),
                out_size / (1024**3),
                vq_label,
                max(0, new_audio),
                max(0, new_subs),
            )
        else:
            log.info(
                "enrichment: replacing %s (%.2f GB → %.2f GB, +%d audio, +%d subs)",
                library_path.name,
                lib_size / (1024**3),
                out_size / (1024**3),
                new_audio,
                max(0, new_subs),
            )

        try:
            os.replace(str(staging), str(library_path))
        except OSError as exc:
            log.error("enrichment: atomic replace failed: %s", exc)
            self._cleanup_staging(staging)
            self._mark_failed(state, grab_key, f"replace failed: {exc}")
            return False

        # 9. Notify Radarr/Sonarr to rescan
        grab_info_final = state.get("grabbed", {}).get(grab_key, {})
        self._refresh_arr_item(
            config,
            service=grab_info_final.get("service", ""),
            arr_id=grab_info_final.get("arr_id"),
        )

        # 10. Record success
        processed = state.setdefault("processed", {})
        success_record: Dict[str, Any] = {
            "status": "ok",
            "operation": operation,
            "chromaprint_score": round(alignment.score, 4),
            "offset_seconds": round(alignment.offset_seconds, 3),
            "timestamp": int(time.time()),
        }
        if languages_adding:
            success_record["languages_added"] = languages_adding
        if sub_tracks:
            success_record["subtitles_added"] = [s.lang for s in sub_tracks]
        if is_video_upgrade:
            final_vq = probe_video_quality(library_path)
            if final_vq:
                success_record["video_upgraded_to"] = final_vq.resolution_label
        processed[str(library_path)] = success_record
        return True

    # ------------------------------------------------------------------
    # Prowlarr search
    # ------------------------------------------------------------------

    def _search_prowlarr(
        self,
        host: str,
        api_key: str,
        queries: List[str],
    ) -> List[ProwlarrResult]:
        """Search Prowlarr and return deduplicated results."""
        headers = {"X-Api-Key": api_key}
        timeout = httpx.Timeout(180.0, connect=10.0)
        all_results: List[ProwlarrResult] = []

        for query in queries[:3]:
            try:
                resp = httpx.get(
                    f"http://{host}:9696/api/v1/search",
                    params={"query": query, "type": "search"},
                    headers=headers,
                    timeout=timeout,
                )
                resp.raise_for_status()
                for item in resp.json():
                    guid = item.get("guid", "")
                    magnet_url = item.get("magnetUrl")
                    effective_magnet = None
                    if guid.startswith("magnet:"):
                        effective_magnet = guid
                    elif magnet_url and magnet_url.startswith("magnet:"):
                        effective_magnet = magnet_url

                    pr = ProwlarrResult(
                        title=item.get("title", ""),
                        guid=guid,
                        download_url=item.get("downloadUrl"),
                        magnet_url=effective_magnet,
                        seeders=item.get("seeders", 0),
                        size=item.get("size", 0),
                        indexer=item.get("indexer", ""),
                    )
                    if pr.download_url or pr.magnet_url:
                        all_results.append(pr)
            except httpx.TimeoutException:
                log.warning("enrichment: search timed out for: %s", query)
            except Exception as exc:
                log.warning("enrichment: search error for '%s': %s", query, exc)

        # Deduplicate by guid
        seen: Set[str] = set()
        unique: List[ProwlarrResult] = []
        for r in all_results:
            if r.guid not in seen:
                seen.add(r.guid)
                unique.append(r)
        return unique

    # ------------------------------------------------------------------
    # qBittorrent helpers
    # ------------------------------------------------------------------

    def _get_completed_enrichment_torrents(
        self,
        host: str,
        creds: dict,
        qb_cfg: Any,
    ) -> List[dict]:
        """Get completed torrents in the 'enrichment' qBT category."""
        client = httpx.Client(
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers={"Host": "localhost:8080"},
        )
        try:
            username = creds.get("username") or qb_cfg.username
            password = creds.get("password") or qb_cfg.password
            resp = client.post(
                f"http://{host}:8080/api/v2/auth/login",
                data={"username": username, "password": password},
            )
            if resp.text.strip() != "Ok.":
                return []

            resp = client.get(
                f"http://{host}:8080/api/v2/torrents/info",
                params={"category": "enrichment"},
            )
            resp.raise_for_status()
            torrents = resp.json() or []

            # Filter to completed (progress == 1.0 or state in completed states)
            completed = []
            for t in torrents:
                state_str = t.get("state", "")
                progress = t.get("progress", 0)
                if progress >= 1.0 or state_str in (
                    "uploading",
                    "pausedUP",
                    "stalledUP",
                    "queuedUP",
                    "forcedUP",
                    "checkingUP",
                ):
                    completed.append(t)
            return completed
        except Exception as exc:
            log.debug("enrichment: failed to check qBT: %s", exc)
            return []
        finally:
            client.close()

    def _get_qbt_names(self, host: str, creds: dict, qb_cfg: Any) -> Set[str]:
        """Get names of all torrents in qBittorrent."""
        client = httpx.Client(
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers={"Host": "localhost:8080"},
        )
        try:
            username = creds.get("username") or qb_cfg.username
            password = creds.get("password") or qb_cfg.password
            resp = client.post(
                f"http://{host}:8080/api/v2/auth/login",
                data={"username": username, "password": password},
            )
            if resp.text.strip() != "Ok.":
                return set()
            resp = client.get(f"http://{host}:8080/api/v2/torrents/info")
            resp.raise_for_status()
            return {t.get("name", "") for t in resp.json() or []}
        except Exception:
            return set()
        finally:
            client.close()

    def _add_to_qbt(
        self,
        host: str,
        creds: dict,
        qb_cfg: Any,
        result: ProwlarrResult,
        category: str,
    ) -> bool:
        """Add a torrent to qBittorrent."""
        client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"Host": "localhost:8080"},
        )
        try:
            username = creds.get("username") or qb_cfg.username
            password = creds.get("password") or qb_cfg.password
            resp = client.post(
                f"http://{host}:8080/api/v2/auth/login",
                data={"username": username, "password": password},
            )
            if resp.text.strip() != "Ok.":
                log.error("enrichment: qBT auth failed")
                return False

            # Resolve category save path
            try:
                cats_resp = client.get(f"http://{host}:8080/api/v2/torrents/categories")
                cat_info = cats_resp.json().get(category, {})
                savepath = cat_info.get("savePath", "")
            except Exception:
                savepath = ""

            add_data: Dict[str, str] = {"category": category}
            if savepath:
                add_data["savepath"] = savepath

            # Prefer magnet URI
            if result.magnet_url:
                resp = client.post(
                    f"http://{host}:8080/api/v2/torrents/add",
                    data={**add_data, "urls": result.magnet_url},
                )
                if resp.status_code == 200:
                    return True

            # Fall back to download URL
            if result.download_url:
                try:
                    dl = httpx.get(
                        result.download_url, timeout=30, follow_redirects=True
                    )
                    if dl.status_code == 200 and dl.content:
                        resp = client.post(
                            f"http://{host}:8080/api/v2/torrents/add",
                            data=add_data,
                            files={"torrents": ("file.torrent", dl.content)},
                        )
                        if resp.status_code == 200:
                            return True
                except Exception as exc:
                    log.debug("enrichment: download URL fallback failed: %s", exc)

            return False
        except Exception as exc:
            log.error("enrichment: failed to add to qBT: %s", exc)
            return False
        finally:
            client.close()

    def _cleanup_torrent(
        self,
        host: str,
        creds: dict,
        qb_cfg: Any,
        torrent_hash: str,
    ) -> None:
        """Remove a torrent and its files from qBittorrent."""
        client = httpx.Client(
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers={"Host": "localhost:8080"},
        )
        try:
            username = creds.get("username") or qb_cfg.username
            password = creds.get("password") or qb_cfg.password
            resp = client.post(
                f"http://{host}:8080/api/v2/auth/login",
                data={"username": username, "password": password},
            )
            if resp.text.strip() != "Ok.":
                return
            client.post(
                f"http://{host}:8080/api/v2/torrents/delete",
                data={"hashes": torrent_hash, "deleteFiles": "true"},
            )
        except Exception:
            pass
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Arr helpers
    # ------------------------------------------------------------------

    def _load_arr_original_languages(
        self,
        config: StackConfig,
    ) -> Dict[str, dict]:
        """Load movie/series metadata from Radarr/Sonarr APIs.

        Returns a dict mapping lowercase folder name → metadata dict with
        keys: title, year, original_language, arr_id, service.
        """
        secrets = self.repo.load_secrets()
        result: Dict[str, dict] = {}

        # Radarr
        radarr_key = secrets.get("radarr", {}).get("api_key", "")
        if radarr_key and config.services.radarr.enabled:
            radarr_host = self._resolve_host("radarr", config)
            try:
                resp = httpx.get(
                    f"http://{radarr_host}:{config.services.radarr.port}/api/v3/movie",
                    headers={"X-Api-Key": radarr_key},
                    timeout=30,
                )
                resp.raise_for_status()
                for movie in resp.json():
                    folder = movie.get("folderName") or movie.get("path", "")
                    folder_name = Path(folder).name.lower() if folder else ""
                    title = movie.get("title", "")
                    year = movie.get("year")
                    orig = movie.get("originalLanguage", {})
                    orig_lang = arr_language_to_iso(orig.get("name", "")) or "und"

                    entry = {
                        "title": title,
                        "year": year,
                        "original_language": orig_lang,
                        "arr_id": movie.get("id"),
                        "service": "radarr",
                    }
                    if folder_name:
                        result[folder_name] = entry
                    if title:
                        result[title.lower()] = entry
            except Exception as exc:
                log.warning("enrichment: failed to load Radarr movies: %s", exc)

        # Sonarr
        sonarr_key = secrets.get("sonarr", {}).get("api_key", "")
        if sonarr_key and config.services.sonarr.enabled:
            sonarr_host = self._resolve_host("sonarr", config)
            try:
                resp = httpx.get(
                    f"http://{sonarr_host}:{config.services.sonarr.port}/api/v3/series",
                    headers={"X-Api-Key": sonarr_key},
                    timeout=30,
                )
                resp.raise_for_status()
                for series in resp.json():
                    folder = series.get("path", "")
                    folder_name = Path(folder).name.lower() if folder else ""
                    title = series.get("title", "")
                    year = series.get("year")
                    orig = series.get("originalLanguage", {})
                    orig_lang = arr_language_to_iso(orig.get("name", "")) or "und"

                    entry = {
                        "title": title,
                        "year": year,
                        "original_language": orig_lang,
                        "arr_id": series.get("id"),
                        "service": "sonarr",
                    }
                    if folder_name:
                        result[folder_name] = entry
                    if title:
                        result[title.lower()] = entry
            except Exception as exc:
                log.warning("enrichment: failed to load Sonarr series: %s", exc)

        return result

    def _resolve_arr_info(
        self,
        path: Path,
        orig_langs: Dict[str, dict],
        config: StackConfig,
    ) -> tuple:
        """Resolve arr metadata for a library file path.

        Returns (original_language, title, year, arr_id, service).
        """
        # Try matching by parent folder name (most reliable)
        folder = path.parent.name.lower()
        entry = orig_langs.get(folder)
        if entry:
            return (
                entry["original_language"],
                entry["title"],
                entry.get("year"),
                entry.get("arr_id"),
                entry.get("service"),
            )

        # Try grandparent (for TV: /tv/Show Name/Season 01/file.mkv)
        grandparent = path.parent.parent.name.lower()
        entry = orig_langs.get(grandparent)
        if entry:
            return (
                entry["original_language"],
                entry["title"],
                entry.get("year"),
                entry.get("arr_id"),
                entry.get("service"),
            )

        return (None, None, None, None, None)

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _resolve_host(self, service: str, config: StackConfig) -> str:
        """Resolve a service hostname (handles VPN-routed services via Gluetun)."""
        if config.services.gluetun.enabled and service in VPN_ROUTED_SERVICES:
            return "gluetun"
        return service

    def _find_media_file(
        self,
        content_path: Path,
        *,
        target_path: Optional[Path] = None,
    ) -> Optional[Path]:
        """Find the best matching video file in a torrent's content path.

        For movies: returns the largest video file (same as before).
        For TV: if ``target_path`` contains episode info (S01E01), tries
        to match an episode from the download by parsing filenames.  Falls
        back to largest file if no episode match is found.
        """
        if content_path.is_file():
            if content_path.suffix.lower() in VIDEO_EXTENSIONS:
                return content_path
            return None

        if not content_path.is_dir():
            return None

        # Collect all video files
        video_files: List[tuple[Path, int]] = []
        for root, _dirs, files in os.walk(content_path):
            for name in files:
                p = Path(root) / name
                if p.suffix.lower() in VIDEO_EXTENSIONS:
                    try:
                        video_files.append((p, p.stat().st_size))
                    except OSError:
                        pass

        if not video_files:
            return None

        # TV episode matching: parse target episode and find match in download
        if target_path:
            target_ep = parse_tv_episode(target_path.stem)
            if target_ep:
                _, target_season, target_episode, _ = target_ep
                for vf, _ in video_files:
                    ep_info = parse_tv_episode(vf.stem)
                    if ep_info:
                        _, season, episode, end_ep = ep_info
                        if season == target_season:
                            # Exact episode match
                            if episode == target_episode:
                                return vf
                            # Multi-episode file covering the target
                            if end_ep and episode <= target_episode <= end_ep:
                                return vf

        # Fallback: largest file
        video_files.sort(key=lambda x: x[1], reverse=True)
        return video_files[0][0]

    def _find_grab_key(self, torrent_name: str, grabbed: dict) -> Optional[str]:
        """Find the grab key for a torrent by matching name prefix."""
        # Exact match on stored torrent_name
        for key, info in grabbed.items():
            if info.get("torrent_name") == torrent_name:
                return key
        # Fallback: key is title[:120]
        for key in grabbed:
            if torrent_name.startswith(key[:100]):
                return key
        return None

    def _walk_video_files(self, directory: Path):
        """Yield all video files under directory, recursively."""
        for root, _dirs, files in os.walk(directory):
            for name in sorted(files):
                if name.startswith("."):
                    continue
                if Path(name).suffix.lower() in VIDEO_EXTENSIONS:
                    yield Path(root) / name

    def _find_all_pack_targets(
        self,
        config: StackConfig,
        enrich_cfg: EnrichmentConfig,
        state: dict,
        content_path: Path,
        grab_info: dict,
    ) -> List[tuple]:
        """Find all library files that can be enriched from a season pack.

        When a torrent is a TV season pack with multiple episodes, this
        method scans the library show directory for files that need the
        same type of enrichment (missing audio language or video upgrade),
        matches each to an episode in the pack, and returns a list of
        (library_path, target_language, candidate_path) tuples.

        Returns an empty list if the torrent isn't a multi-file TV pack,
        or if no additional matches are found beyond the original target.
        """
        if not content_path.is_dir():
            return []

        # Collect video files in the pack
        pack_files = []
        for root, _dirs, files in os.walk(content_path):
            for name in files:
                p = Path(root) / name
                if p.suffix.lower() in VIDEO_EXTENSIONS:
                    pack_files.append(p)

        if len(pack_files) < 2:
            return []  # Not a season pack

        target_lang = grab_info.get("target_language", "eng")
        category = grab_info.get("category", "tv")
        service = grab_info.get("service", "sonarr")
        arr_id = grab_info.get("arr_id")

        # Find the show directory in the library
        original_target = Path(grab_info.get("target_path", ""))
        if not original_target.exists():
            return []

        # The show directory is typically 2 levels up from the episode file:
        # /data/tv/Show Name/Season 1/Show - S01E01.mkv
        show_dir = original_target.parent.parent
        if not show_dir.is_dir():
            return []

        processed = state.get("processed", {})

        # Find all library episodes that need the same enrichment
        results = []
        for season_dir in sorted(show_dir.iterdir()):
            if not season_dir.is_dir():
                continue
            for lib_file in sorted(season_dir.iterdir()):
                if lib_file.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue
                if lib_file.name.startswith("."):
                    continue

                lib_str = str(lib_file)

                # Skip already processed (successfully)
                proc = processed.get(lib_str)
                if proc and proc.get("status") == "ok":
                    continue

                # Try to find a matching episode in the pack
                cand = self._find_media_file(content_path, target_path=lib_file)
                if cand is None:
                    continue

                # Verify the candidate is actually a different file for this episode
                # (not just the largest file fallback for every episode)
                lib_ep = parse_tv_episode(lib_file.stem)
                cand_ep = parse_tv_episode(cand.stem)
                if lib_ep and cand_ep:
                    # Must match season+episode
                    _, lib_s, lib_e, _ = lib_ep
                    _, cand_s, cand_e, cand_end = cand_ep
                    if cand_end:
                        if not (cand_s == lib_s and cand_e <= lib_e <= cand_end):
                            continue
                    elif not (cand_s == lib_s and cand_e == lib_e):
                        continue
                elif lib_ep and not cand_ep:
                    continue  # Can't match without episode info

                results.append((lib_file, target_lang, cand))

        if results:
            log.info(
                "enrichment: season pack has %d episodes, matched %d library files for enrichment",
                len(pack_files), len(results),
            )

        return results

    def _get_duration(self, path: Path) -> Optional[float]:
        """Get duration of a media file in seconds via ffprobe."""
        import subprocess as _sp

        try:
            result = _sp.run(
                [
                    "ffprobe",
                    "-v",
                    "quiet",
                    "-print_format",
                    "json",
                    "-show_format",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                import json as _json

                data = _json.loads(result.stdout)
                return float(data.get("format", {}).get("duration", 0))
        except Exception:
            pass
        return None

    def _cleanup_staging(self, staging: Path) -> None:
        """Remove a staging file, ignoring errors."""
        try:
            if staging.exists():
                staging.unlink()
        except OSError:
            pass

    def _mark_failed(self, state: dict, grab_key: str, reason: str) -> None:
        """Record a failed enrichment attempt."""
        processed = state.setdefault("processed", {})
        grab_info = state.get("grabbed", {}).get(grab_key, {})
        target = grab_info.get("target_path", grab_key)
        processed[target] = {
            "status": "failed",
            "reason": reason,
            "timestamp": int(time.time()),
        }

    def _refresh_arr_item(
        self,
        config: StackConfig,
        service: str,
        arr_id: Optional[int],
    ) -> None:
        """Notify Radarr/Sonarr to rescan a specific item after enrichment.

        Sends RefreshMovie/RefreshSeries + RescanMovie/RescanSeries so the
        arr service sees the newly enriched file with its additional tracks.
        """
        if not service or arr_id is None:
            return

        secrets = self.repo.load_secrets()
        api_key = secrets.get(service, {}).get("api_key")
        if not api_key:
            return

        if service == "radarr":
            port = config.services.radarr.port or 7878
            refresh_cmd = "RefreshMovie"
            rescan_cmd = "RescanMovie"
            id_field = "movieIds"
        elif service == "sonarr":
            port = config.services.sonarr.port or 8989
            refresh_cmd = "RefreshSeries"
            rescan_cmd = "RescanSeries"
            id_field = "seriesIds"
        else:
            return

        host = self._resolve_host(service, config)
        headers = {"X-Api-Key": api_key}
        base = f"http://{host}:{port}/api/v3/command"
        timeout = httpx.Timeout(10.0, connect=5.0)

        try:
            httpx.post(
                base,
                json={"name": refresh_cmd, id_field: [arr_id]},
                headers=headers,
                timeout=timeout,
            )
            httpx.post(
                base,
                json={"name": rescan_cmd, id_field: [arr_id]},
                headers=headers,
                timeout=timeout,
            )
            log.info(
                "enrichment: notified %s to rescan item %s",
                service,
                arr_id,
            )
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            log.warning(
                "enrichment: %s rescan notification failed (id=%s): %s",
                service,
                arr_id,
                exc,
            )
