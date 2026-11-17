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
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import httpx

from ..models import EnrichmentConfig, StackConfig, VPN_ROUTED_SERVICES
from ..storage import ConfigRepository
from .backfill import ProwlarrResult
from .subtitle_align import parse_srt, probe_video_duration
from .chromaprint import validate_and_align
from .languages import arr_language_to_iso
from .remux import (
    RESOLUTION_TARGET_SCORES,
    RemuxPolicyError,
    SubtitleIdentity,
    _normalize_lang,
    build_crossmux_command,
    build_video_upgrade_command,
    find_all_enrichment_audio_tracks,
    find_best_audio_track_for_language,
    find_useful_candidate_subtitles,
    probe_raw_streams,
    probe_streams,
    probe_video_quality,
    subtitle_identity_from_mkvmerge_track,
    subtitle_identity_from_srt_filename,
)
from .worker import VIDEO_EXTENSIONS, parse_tv_episode, selection_for_policy


def effective_target_resolution(config: StackConfig, enrich_cfg: EnrichmentConfig) -> str:
    """The central ``quality.target_resolution`` wins over the enrichment setting."""
    target = config.quality.target_resolution
    return target.value if target else enrich_cfg.target_resolution

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
    # Retry tracking (set by _grab_from_queue on failure)
    last_fail_ts: float = 0


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


def _compact_failed_torrents(failed_torrents: dict) -> None:
    """Evict expired entries from the torrent blacklist."""
    now = time.time()
    expired = [
        k for k, v in failed_torrents.items()
        if now - v.get("last_failed", 0) > 30 * 86400  # 30 days
    ]
    for k in expired:
        del failed_torrents[k]


def _compact_search_misses(search_misses: dict) -> None:
    """Evict expired entries from the search miss tracker."""
    now = time.time()
    expired = [
        k for k, v in search_misses.items()
        if now - v.get("last_miss", 0) > 14 * 86400  # 14 days
    ]
    for k in expired:
        del search_misses[k]


# ---------------------------------------------------------------------------
# Enrichment Engine
# ---------------------------------------------------------------------------


class EnrichmentEngine:
    """Always-on media enrichment engine.

    Two entry points, both called from the pipeline runner's ``_tick()``
    every 60 seconds:

    - ``process_completed(config)``: Checks the qBittorrent "enrichment"
      category for completed downloads, then fingerprints, cross-muxes,
      verifies, and atomically replaces the library file.

    - ``maybe_grab_next(config)``: Checks if there's a free download slot
      and grabs the next candidate from the queue. The queue is rebuilt
      from a full library scan every ``queue_rescan_hours``.

    This creates a continuous enrichment loop:
      grab → download → process → grab → download → process → ...
    constrained by ``max_concurrent_downloads`` (default 1).
    """

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo
        self._queue: List[EnrichmentCandidate] = []
        self._last_scan: float = 0.0
        self._progress_snapshots: Dict[str, Tuple[float, float]] = {}  # grab_key → (progress, observed_at)
        self._force_rescan: bool = False
        self._tmp_cleanup_done: bool = False
        # Background gap scan thread state
        self._scan_thread: Optional[threading.Thread] = None
        self._scan_started: float = 0.0
        self._pending_queue: Optional[List[EnrichmentCandidate]] = None

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
    # Stale grab cleanup
    # ------------------------------------------------------------------

    def _get_enrichment_torrents(
        self, host: str, creds: dict, qb_cfg: Any,
    ) -> Dict[str, dict]:
        """Fetch all torrents in the enrichment category from qBittorrent.

        Returns a dict mapping torrent name → torrent info dict.
        """
        client = httpx.Client(
            timeout=httpx.Timeout(15.0, connect=5.0),
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
                return {}
            resp = client.get(
                f"http://{host}:8080/api/v2/torrents/info",
                params={"category": "enrichment"},
            )
            if resp.status_code != 200:
                return {}
            return {t.get("name", ""): t for t in resp.json() or []}
        except Exception:
            return {}
        finally:
            client.close()

    def _cleanup_stale_grabs(
        self,
        config: StackConfig,
        enrich_cfg: EnrichmentConfig,
        state: dict,
    ) -> bool:
        """Evict grabbed entries whose torrents are stale, vanished, or stalled.

        Returns True if any entries were cleaned up (caller should save state).
        """
        grabbed = state.get("grabbed", {})
        if not grabbed:
            return False

        secrets = self.repo.load_secrets()
        qb_host = self._resolve_host("qbittorrent", config)
        qb_creds = secrets.get("qbittorrent", {})
        qb_cfg = config.services.qbittorrent

        # Fetch all enrichment torrents from qBT
        qbt_torrents = self._get_enrichment_torrents(qb_host, qb_creds, qb_cfg)

        now = time.time()
        ttl_seconds = enrich_cfg.grab_ttl_hours * 3600
        stall_seconds = enrich_cfg.stall_timeout_hours * 3600
        stale_keys: List[Tuple[str, str]] = []  # (grab_key, reason)

        for grab_key, info in list(grabbed.items()):
            torrent_name = info.get("torrent_name", "")
            grab_ts = info.get("timestamp", 0)
            age_hours = (now - grab_ts) / 3600 if grab_ts else 0

            # Check 1: TTL expired
            if grab_ts and now - grab_ts > ttl_seconds:
                stale_keys.append((grab_key, f"TTL expired ({age_hours:.0f}h old)"))
                continue

            # Check 2: Torrent vanished from qBT
            qbt_info = qbt_torrents.get(torrent_name)
            if qbt_info is None:
                # Also try matching by hash if available
                grab_hash = info.get("torrent_hash", "")
                if grab_hash:
                    for t in qbt_torrents.values():
                        if t.get("hash", "") == grab_hash:
                            qbt_info = t
                            break
                if qbt_info is None:
                    stale_keys.append((grab_key, "torrent not found in qBittorrent"))
                    continue

            # Check 3: Download stalled (no progress for stall_timeout_hours)
            progress = qbt_info.get("progress", 1.0)
            if progress < 1.0:  # Still downloading
                snapshot = self._progress_snapshots.get(grab_key)
                if snapshot is None:
                    # First observation — record and move on
                    self._progress_snapshots[grab_key] = (progress, now)
                else:
                    prev_progress, observed_at = snapshot
                    if progress > prev_progress:
                        # Making progress — update snapshot
                        self._progress_snapshots[grab_key] = (progress, now)
                    elif now - observed_at > stall_seconds:
                        # No progress for stall_timeout_hours
                        stale_keys.append(
                            (grab_key, f"download stalled at {progress*100:.1f}% for {(now - observed_at)/3600:.1f}h"),
                        )

        # Also clean up qBT enrichment torrents with no matching grabbed entry.
        # These are orphans — downloads that lost their grab metadata (e.g., due
        # to a previous dev container cleanup or state corruption).
        grabbed_hashes = {info.get("torrent_hash") for info in grabbed.values() if info.get("torrent_hash")}
        grabbed_names = {info.get("torrent_name") for info in grabbed.values()}
        orphan_cleaned = 0
        for tname, torrent in qbt_torrents.items():
            if tname in grabbed_names or torrent.get("hash") in grabbed_hashes:
                continue
            added_on = torrent.get("added_on", 0)
            if added_on > 0 and now - added_on > 3600:
                log.info("enrichment: removing orphaned qBT torrent (no grab entry): %s", tname[:60])
                self._cleanup_torrent(qb_host, qb_creds, qb_cfg, torrent.get("hash", ""))
                orphan_cleaned += 1

        if not stale_keys and not orphan_cleaned:
            return False

        # Evict stale grabbed entries
        for grab_key, reason in stale_keys:
            info = grabbed.get(grab_key, {})
            target = info.get("target_path", grab_key)
            torrent_name = info.get("torrent_name", "")

            log.info(
                "enrichment: evicting stale grab '%s' — %s (target: %s)",
                grab_key[:60], reason, Path(target).name,
            )

            # Mark as failed so 7-day cooldown prevents immediate re-grab
            self._mark_failed(state, grab_key, f"stale grab: {reason}")

            # Torrent-level blacklist: exponential backoff prevents the same
            # torrent from being re-grabbed for other episodes of the same show.
            # Key by ``grab_key`` (prowlarr title[:120]) so the filter at
            # line ~923 can match — see comment at process_completed blacklist.
            failed_torrents = state.setdefault("failed_torrents", {})
            ft_entry = failed_torrents.get(grab_key, {"count": 0})
            ft_entry["count"] = ft_entry.get("count", 0) + 1
            ft_entry["last_failed"] = time.time()
            # Exponential backoff: 1h → 4h → 16h → 64h → 256h, cap 30d
            ft_entry["cooldown"] = min(3600 * (4 ** ft_entry["count"]), 30 * 86400)
            failed_torrents[grab_key] = ft_entry
            log.info(
                "enrichment: blacklisted torrent '%s' (attempt %d, cooldown %dh)",
                grab_key[:50],
                ft_entry["count"],
                ft_entry["cooldown"] // 3600,
            )

            # Delete torrent from qBT if it still exists
            qbt_info = qbt_torrents.get(torrent_name)
            if qbt_info is None and info.get("torrent_hash"):
                for t in qbt_torrents.values():
                    if t.get("hash", "") == info["torrent_hash"]:
                        qbt_info = t
                        break
            if qbt_info:
                self._cleanup_torrent(
                    qb_host, qb_creds, qb_cfg, qbt_info.get("hash", ""),
                )

            # Remove from grabbed dict and progress snapshots
            grabbed.pop(grab_key, None)
            self._progress_snapshots.pop(grab_key, None)

        if stale_keys or orphan_cleaned:
            state["grabbed"] = grabbed
            self._force_rescan = True

        log.info("enrichment: cleaned up %d stale grab(s), %d orphaned torrent(s)", len(stale_keys), orphan_cleaned)
        self.repo.emit_event(
            "enrichment", "info",
            f"Cleaned up {len(stale_keys)} stale grab(s), {orphan_cleaned} orphaned torrent(s)",
            {"evicted": [k for k, _ in stale_keys], "orphans_removed": orphan_cleaned},
        )
        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def process_completed(self, config: StackConfig) -> None:
        """Check enrichment downloads and process any that have completed."""
        enrich_cfg = config.services.pipeline.enrichment
        if not enrich_cfg.enabled:
            return

        # One-time cleanup of orphaned .enrichment.tmp.mkv files from previous
        # buggy runs where the atomic replace left temp files on disk.
        if not self._tmp_cleanup_done:
            self._tmp_cleanup_done = True
            self._cleanup_orphaned_staging_files(config)

        state = self.repo.load_enrichment_state()
        grabbed = state.get("grabbed", {})

        # Clean up stale grabs before processing completed torrents
        stale_cleaned = self._cleanup_stale_grabs(config, enrich_cfg, state)
        grabbed = state.get("grabbed", {})  # re-read after cleanup

        if not grabbed:
            if stale_cleaned:
                self.repo.save_enrichment_state(state)
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
            if stale_cleaned:
                self.repo.save_enrichment_state(state)
            return

        processed_any = False
        for torrent in completed:
            name = torrent.get("name", "")
            torrent_hash = torrent.get("hash", "")
            content_path = torrent.get("content_path", "")

            # Find the grab metadata for this torrent
            grab_key = self._find_grab_key(name, grabbed, torrent_hash)
            if grab_key is None:
                # Orphaned enrichment torrent — no grab metadata.
                # Clean up if it's been completed for over 1 hour.
                completion_ts = torrent.get("completion_on", 0)
                if completion_ts > 0 and time.time() - completion_ts > 3600:
                    log.info(
                        "enrichment: cleaning up orphaned torrent (no grab metadata, completed >1h ago): %s",
                        name[:60],
                    )
                    self._cleanup_torrent(qb_host, qb_creds, qb_cfg, torrent_hash)
                    self.repo.emit_event(
                        "enrichment", "info",
                        f"Cleaned up orphaned enrichment torrent: {name[:50]}",
                    )
                else:
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
                config,
                enrich_cfg,
                state,
                Path(content_path),
                grab_info,
            )

            if not pack_targets:
                # Fall back to single target
                pack_targets = [(target_path, target_lang, candidate_path)]

            succeeded = 0
            failed_count = 0
            for lib_path, lang, cand_path in pack_targets:
                log.info(
                    "enrichment: processing %s → target=%s lang=%s%s",
                    cand_path.name[:60],
                    lib_path.name[:60],
                    lang,
                    " [VIDEO UPGRADE]" if is_video_upgrade else "",
                )

                ok = self._process_candidate(
                    config,
                    enrich_cfg,
                    state,
                    lib_path,
                    cand_path,
                    lang,
                    grab_key,
                    grab_info,
                )

                if ok:
                    succeeded += 1
                    log.info(
                        "enrichment: SUCCESS — added %s audio to %s",
                        lang,
                        lib_path.name,
                    )
                else:
                    failed_count += 1

            log.info(
                "enrichment: pack complete — %d/%d episodes enriched",
                succeeded,
                succeeded + failed_count,
            )

            # Blacklist on full failure: when nothing succeeded but we tried,
            # the torrent is fundamentally unsuitable (chromaprint mismatch,
            # wrong cut, bad episode mapping).  These are *deterministic*
            # outcomes — a chromaprint correlation below 0.70 won't suddenly
            # rise to 0.71 the next time we try, because neither the library
            # file nor the source torrent change.  So the cooldown here is
            # much longer than the transient-failure cooldown in
            # ``_cleanup_stale_grabs`` (which handles network/seeder issues
            # that genuinely can resolve within hours).
            #
            # Key by ``grab_key`` (the prowlarr title[:120]) so the filter at
            # line ~923 can match — that filter uses ``r.title[:120]`` because
            # ProwlarrResult does not carry a BitTorrent info_hash field.
            if succeeded == 0 and failed_count > 0:
                failed_torrents = state.setdefault("failed_torrents", {})
                ft_entry = failed_torrents.get(grab_key, {"count": 0})
                ft_entry["count"] = ft_entry.get("count", 0) + 1
                ft_entry["last_failed"] = time.time()
                # 1st → 30d, 2nd → 90d, 3rd+ → 365d (effectively permanent;
                # user can clear failed_torrents in pipeline.json to retry).
                if ft_entry["count"] == 1:
                    ft_entry["cooldown"] = 30 * 86400
                elif ft_entry["count"] == 2:
                    ft_entry["cooldown"] = 90 * 86400
                else:
                    ft_entry["cooldown"] = 365 * 86400
                failed_torrents[grab_key] = ft_entry
                log.info(
                    "enrichment: blacklisted torrent '%s' — 0/%d enriched (attempt %d, cooldown %dd)",
                    grab_key[:50], failed_count, ft_entry["count"], ft_entry["cooldown"] // 86400,
                )

            # Clean up torrent after processing ALL episodes
            self._cleanup_torrent(qb_host, qb_creds, qb_cfg, torrent_hash)
            del grabbed[grab_key]
            processed_any = True

        if processed_any:
            state["grabbed"] = grabbed
            _compact_processed(state.get("processed", {}))
            _compact_failed_torrents(state.get("failed_torrents", {}))
            _compact_search_misses(state.get("search_misses", {}))
            self.repo.save_enrichment_state(state)

    _SCAN_WATCHDOG_HOURS = 2

    def maybe_grab_next(self, config: StackConfig) -> None:
        """Grab the next enrichment candidate if a download slot is free.

        Called every tick (~60s). The library gap scan runs in a background
        thread to avoid blocking the main tick loop. The grab logic proceeds
        with whatever queue exists (even stale) while the scan runs.
        """
        enrich_cfg = config.services.pipeline.enrichment
        if not enrich_cfg.enabled:
            return

        # Check if we have capacity for another download
        active = self._count_active_enrichment_downloads(config)
        if active >= enrich_cfg.max_concurrent_downloads:
            return  # At capacity — wait for current download(s) to finish

        # ── Collect finished background scan result ──────────────────
        if self._pending_queue is not None:
            self._queue = self._pending_queue
            self._pending_queue = None
            self._last_scan = time.time()
            log.info("enrichment: adopted background scan — %d candidates", len(self._queue))
            self.repo.emit_event(
                "enrichment", "info",
                f"Gap scan: {len(self._queue)} files need enrichment",
                {"queue_size": len(self._queue)},
            )
            with self.repo._section_lock("pipeline"):
                data = self.repo._load_section("pipeline") or {}
                enr = data.setdefault("enrichment", {})
                enr["queue_size"] = len(self._queue)
                self.repo._save_section("pipeline", data)

        # ── Watchdog: kill stuck scan thread ─────────────────────────
        if self._scan_thread is not None and self._scan_thread.is_alive():
            age_h = (time.time() - self._scan_started) / 3600
            if age_h > self._SCAN_WATCHDOG_HOURS:
                log.warning(
                    "enrichment: gap scan thread exceeded %.1fh — allowing restart",
                    age_h,
                )
                self._scan_thread = None
                self._scan_started = 0.0

        # ── Launch background scan if needed ─────────────────────────
        rescan_interval = enrich_cfg.queue_rescan_hours * 3600
        needs_scan = (
            not self._queue
            or (time.time() - self._last_scan > rescan_interval)
            or self._force_rescan
        )
        if needs_scan and (self._scan_thread is None or not self._scan_thread.is_alive()):
            self._force_rescan = False
            log.info("enrichment: launching background gap scan")
            self._scan_started = time.time()
            self._scan_thread = threading.Thread(
                target=self._run_gap_scan,
                args=(config, enrich_cfg),
                daemon=True,
                name="enrichment-gap-scan",
            )
            self._scan_thread.start()

        # ── Grab from whatever queue we have (even stale) ────────────
        if not self._queue:
            return

        state = self.repo.load_enrichment_state()
        grabbed_count = self._grab_from_queue(config, enrich_cfg, state)
        self.repo.save_enrichment_state(state)

        if grabbed_count:
            log.info("enrichment: grabbed %d candidate(s)", grabbed_count)

    def prioritize_file(
        self,
        file_path: Path,
        missing_lang: str,
        title: str = "",
        year: Optional[int] = None,
        arr_id: int = 0,
        service: str = "",
        category: str = "",
    ) -> None:
        """Add a newly imported file to the front of the enrichment queue.

        Called by the pipeline runner after importing a file that's missing
        a target audio language. This ensures new imports are enriched ASAP
        rather than waiting for the next queue rescan.
        """
        candidate = EnrichmentCandidate(
            path=file_path,
            title=title or file_path.stem,
            year=year,
            original_language="",
            missing_languages=[missing_lang],
            arr_id=arr_id,
            service=service,
            category=category,
        )
        self._queue.insert(0, candidate)
        log.info("enrichment: prioritized %s for %s enrichment", file_path.name, missing_lang)

    def _count_active_enrichment_downloads(self, config: StackConfig) -> int:
        """Count enrichment torrents currently downloading in qBittorrent."""
        secrets = self.repo.load_secrets()
        qb_host = self._resolve_host("qbittorrent", config)
        qb_creds = secrets.get("qbittorrent", {})
        qb_cfg = config.services.qbittorrent

        try:
            client = httpx.Client(
                timeout=httpx.Timeout(15.0, connect=5.0),
                headers={"Host": "localhost:8080"},
            )
            username = qb_creds.get("username") or qb_cfg.username
            password = qb_creds.get("password") or qb_cfg.password
            resp = client.post(
                f"http://{qb_host}:8080/api/v2/auth/login",
                data={"username": username, "password": password},
            )
            if resp.text.strip() != "Ok.":
                return 0

            resp = client.get(
                f"http://{qb_host}:8080/api/v2/torrents/info",
                params={"category": "enrichment"},
            )
            if resp.status_code != 200:
                return 0

            torrents = resp.json()
            # Count torrents that are still downloading (not completed)
            active = sum(
                1 for t in torrents
                if t.get("progress", 1.0) < 1.0
                and t.get("state") not in ("pausedDL", "error", "missingFiles")
            )
            return active
        except Exception:
            return 0

    def _grab_from_queue(
        self,
        config: StackConfig,
        enrich_cfg: EnrichmentConfig,
        state: dict,
    ) -> int:
        """Pop the next candidate from the queue, search Prowlarr, grab it.

        Reuses the existing _run_search_cycle logic but operates on one
        candidate at a time from the pre-built queue instead of scanning
        the library fresh.
        """
        secrets = self.repo.load_secrets()
        prowlarr_key = secrets.get("prowlarr", {}).get("api_key", "")
        prowlarr_host = self._resolve_host("prowlarr", config)
        qb_host = self._resolve_host("qbittorrent", config)
        qb_creds = secrets.get("qbittorrent", {})
        qb_cfg = config.services.qbittorrent

        if not prowlarr_key:
            log.warning("enrichment: no Prowlarr API key — skipping search")
            return 0

        existing_names = self._get_qbt_names(qb_host, qb_creds, qb_cfg)
        grabbed = state.setdefault("grabbed", {})
        processed = state.get("processed", {})
        grabbed_count = 0

        # Try candidates until we grab one or exhaust the queue
        attempts = 0
        max_attempts = min(len(self._queue), 20)  # Don't spin forever

        GRAB_COOLDOWN = 300  # 5 minutes between retries of the same candidate

        while self._queue and grabbed_count < 1 and attempts < max_attempts:
            candidate = self._queue.pop(0)
            attempts += 1
            path_str = str(candidate.path)

            # Cooldown: if this candidate recently failed a grab, push it back
            last_fail = getattr(candidate, 'last_fail_ts', 0)
            if last_fail and time.time() - last_fail < GRAB_COOLDOWN:
                self._queue.append(candidate)  # Put it back at the end
                continue

            # Skip already processed successfully, OR recently failed.  The
            # 7-day cooldown for failed targets is also enforced in
            # ``scan_library_gaps`` (line ~1108) but the queue here is built
            # at scan time and reused across many ticks — without re-checking
            # ``processed``, a target that fails chromaprint mid-cycle keeps
            # getting fed alt-title releases on every subsequent tick (saw
            # this with Hunter x Hunter: [Judas] failed at 10:02, then a
            # different ()-format release of the same content was grabbed at
            # 10:06 because the queue wasn't aware of the new failure).
            proc_entry = processed.get(path_str)
            if proc_entry:
                status = proc_entry.get("status", "ok")
                if status == "ok":
                    continue
                failed_at = proc_entry.get("timestamp", 0)
                if time.time() - failed_at < 7 * 86400:
                    continue  # In failed-target cooldown

            # Skip already grabbed
            active_targets = {g.get("target_path") for g in grabbed.values()}
            if path_str in active_targets:
                continue

            # Search miss cooldown: skip candidates that repeatedly yield no results
            search_misses = state.get("search_misses", {})
            miss_key = f"{candidate.title}:{','.join(sorted(candidate.missing_languages))}"
            miss_entry = search_misses.get(miss_key)
            if miss_entry:
                miss_count = miss_entry.get("count", 0)
                # Backoff: 1 miss → 24h, 2 → 72h, 3+ → 7 days
                if miss_count >= 3:
                    cooldown = 7 * 86400
                elif miss_count == 2:
                    cooldown = 3 * 86400
                else:
                    cooldown = 86400
                if time.time() - miss_entry.get("last_miss", 0) < cooldown:
                    continue

            # Search Prowlarr
            queries = self._build_queries(candidate, enrich_cfg, config)
            results = self._search_prowlarr(prowlarr_host, prowlarr_key, queries)
            if not results:
                log.debug("enrichment: no viable results for '%s'", candidate.title)
                # Record the miss for backoff
                sm = state.setdefault("search_misses", {})
                entry = sm.get(miss_key, {"count": 0})
                entry["count"] = entry.get("count", 0) + 1
                entry["last_miss"] = time.time()
                sm[miss_key] = entry
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

            # Filter out blacklisted torrents (exponential backoff after failures)
            failed_torrents = state.get("failed_torrents", {})
            now = time.time()
            viable = []
            for r, s in scored:
                torrent_id = getattr(r, "info_hash", None) or r.title[:120]
                ft_entry = failed_torrents.get(torrent_id)
                if ft_entry:
                    cooldown_remaining = ft_entry.get("cooldown", 0) - (now - ft_entry.get("last_failed", 0))
                    if cooldown_remaining > 0:
                        log.debug(
                            "enrichment: skipping blacklisted torrent '%s' (%dh cooldown remaining)",
                            r.title[:40], cooldown_remaining / 3600,
                        )
                        continue
                viable.append((r, s))

            if not viable:
                log.debug("enrichment: all results blacklisted for '%s'", candidate.title[:40])
                continue

            best, best_score = viable[0]

            # Skip if already in qBT
            if best.title in existing_names:
                log.debug("enrichment: already in qBT: %s", best.title[:60])
                continue

            log.info(
                "enrichment: grabbing '%s' (score=%d, seeders=%d) for '%s' [%s]",
                best.title[:60],
                best_score,
                best.seeders,
                candidate.title[:40],
                ", ".join(candidate.missing_languages),
            )

            torrent_hash = self._add_to_qbt(
                qb_host, qb_creds, qb_cfg, best, "enrichment",
            )
            if torrent_hash is None:
                # FIFO rotation: push failed candidate to back of queue
                # so other candidates get a chance first
                candidate.last_fail_ts = time.time()
                self._queue.append(candidate)
                log.info(
                    "enrichment: grab failed for '%s', rotated to back of queue (%d remaining)",
                    candidate.title[:40], len(self._queue),
                )
                break  # Stop for this tick — try next candidate on next tick

            # Record grab metadata
            grab_key = best.title[:120]
            grabbed[grab_key] = {
                "torrent_name": best.title,
                "torrent_hash": torrent_hash,
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
            self.repo.emit_event(
                "enrichment", "info",
                f"Grabbed '{best.title[:50]}' for {candidate.title[:40]} [{', '.join(candidate.missing_languages)}]",
                {"torrent": best.title, "target": str(candidate.path),
                 "languages": candidate.missing_languages, "score": best_score,
                 "seeders": best.seeders, "hash": torrent_hash},
            )

        return grabbed_count

    # Backward compat — old code may call maybe_run
    def maybe_run(self, config: StackConfig) -> None:
        """Deprecated — redirects to maybe_grab_next."""
        self.maybe_grab_next(config)

    # ------------------------------------------------------------------
    # Gap scanning
    # ------------------------------------------------------------------

    def _resolve_target_languages(
        self,
        config: StackConfig,
        enrich_cfg: EnrichmentConfig,
    ) -> List[str]:
        """Derive enrichment target languages.

        If enrichment.target_languages is explicitly set, use those.
        Otherwise, derive from media_policy.keep_audio + "original"
        so the enrichment engine searches for all user-preferred languages
        plus the original language of each media item.
        """
        if enrich_cfg.target_languages:
            return enrich_cfg.target_languages

        # Derive from media policy
        policy_langs = set()
        for category_entry in [config.media_policy.movies]:
            for lang in category_entry.keep_audio:
                if lang.lower() != "und":
                    policy_langs.add(lang)

        # Always include "original" so we search for the native language
        # (e.g., Japanese for anime that only has an English dub)
        targets = sorted(policy_langs) + ["original"]

        log.info(
            "enrichment: target_languages derived from media_policy: %s",
            targets,
        )
        return targets

    def scan_library_gaps(
        self,
        config: StackConfig,
        enrich_cfg: EnrichmentConfig,
    ) -> List[EnrichmentCandidate]:
        """Identify library files missing desired audio languages.

        The ``target_languages`` supports a special ``"original"``
        sentinel which resolves to the actual original language of each
        media item (looked up from Radarr/Sonarr API).  For example,
        ``["eng", "original"]`` means: every file should have both English
        AND its original language audio.  A Japanese anime with only an
        English dub would be flagged as missing Japanese (the original).
        """
        pool = self._resolve_pool_root(config)
        candidates: List[EnrichmentCandidate] = []

        # Resolve target languages (explicit config or derived from media policy)
        target_langs = self._resolve_target_languages(config, enrich_cfg)

        # Separate the "original" sentinel from concrete language codes
        has_original_target = "original" in [
            lang.lower() for lang in target_langs
        ]
        concrete_targets = {
            _normalize_lang(lang)
            for lang in target_langs
            if lang.lower() != "original"
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
                        # Re-evaluate entries from older pipeline versions
                        # (e.g., the _normalize_lang bug created false "ok" entries)
                        if proc_entry.get("pipeline_version", 0) >= 2:
                            continue  # Current version — skip
                        # Old version: fall through to re-probe the file
                    else:
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
                file_audio = {
                    _normalize_lang(lang) for lang in info.audio_languages
                }
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
                            effective_target_resolution(config, enrich_cfg),
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

    def _run_gap_scan(self, config: StackConfig, enrich_cfg: EnrichmentConfig) -> None:
        """Background thread target: run scan_library_gaps and stage result."""
        try:
            result = self.scan_library_gaps(config, enrich_cfg)
            self._pending_queue = result  # atomic ref swap (CPython GIL)
            log.info("enrichment: background scan complete — %d candidates", len(result))
        except Exception as exc:
            log.error("enrichment: background scan failed: %s", exc)
            self._pending_queue = []

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
            queries = self._build_queries(candidate, enrich_cfg, config)

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

            t_hash = self._add_to_qbt(
                qb_host,
                qb_creds,
                qb_cfg,
                best,
                "enrichment",
            )
            if t_hash is None:
                continue

            # Record grab metadata (store ALL missing languages + video info)
            grab_key = best.title[:120]
            grabbed[grab_key] = {
                "torrent_name": best.title,
                "torrent_hash": t_hash,
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
        config: StackConfig,
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
            target = effective_target_resolution(config, enrich_cfg)
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
        grab_info: Optional[dict] = None,
    ) -> bool:
        """Fingerprint, cross-mux, verify, and replace.  Returns True on success.

        Extracts ALL matching target audio languages from the candidate
        (not just the primary one), plus any useful subtitle tracks that
        the library is missing.
        """
        import subprocess

        # For pack episodes, library_path may differ from the grab's original
        # target. Record failures under the actual episode path so each one
        # gets its own cooldown entry in the processed dict.
        _fail_target = str(library_path)

        # Clean up any leftover staging file from a previous interrupted run
        # Check both old (visible) and new (dot-prefix hidden) naming patterns
        for _lf in (
            library_path.parent / f".{library_path.stem}.enrichment.tmp.mkv",
            library_path.parent / f".{library_path.stem}.enrichment.tmp{library_path.suffix}",
            library_path.with_suffix(".enrichment.tmp.mkv"),
        ):
            if _lf.exists():
                try:
                    _lf.unlink()
                except OSError:
                    pass

        # 1. Probe both files
        cand_streams = probe_raw_streams(candidate_path)
        if cand_streams is None:
            log.error("enrichment: failed to probe candidate %s", candidate_path.name)
            self._mark_failed(state, grab_key, "probe failed", target_override=_fail_target)
            return False

        lib_info = probe_streams(library_path)
        if lib_info is None:
            log.error("enrichment: failed to probe library file %s", library_path.name)
            self._mark_failed(state, grab_key, "library probe failed", target_override=_fail_target)
            return False

        # 2. Find ALL matching audio tracks from candidate
        # Build the full target set (same logic as gap scanner)
        target_langs = {
            _normalize_lang(lang)
            for lang in enrich_cfg.target_languages
            if lang.lower() != "original"
        }
        # Resolve "original" if configured
        if "original" in [lang.lower() for lang in enrich_cfg.target_languages]:
            orig_langs = self._load_arr_original_languages(config)
            orig_info = self._resolve_arr_info(library_path, orig_langs, config)
            if orig_info[0]:
                target_langs.add(_normalize_lang(orig_info[0]))
        target_langs.discard("und")

        lib_audio_langs = {
            _normalize_lang(lang) for lang in lib_info.audio_languages
        }

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
                self._mark_failed(state, grab_key, "no target audio in candidate", target_override=_fail_target)
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
        # Use media policy keep_subs (subtitle languages), NOT audio target_langs
        # MediaPolicy only has movies config; use it for both tv and movies
        sub_target_langs = {
            _normalize_lang(lang) for lang in config.media_policy.movies.keep_subs
        }

        lib_sub_langs = {
            _normalize_lang(lang) for lang in lib_info.subtitle_languages
        }
        sub_tracks = find_useful_candidate_subtitles(
            cand_streams,
            lib_sub_langs,
            sub_target_langs,
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
                self._mark_failed(state, grab_key, f"duration mismatch ({delta:.0f}s)", target_override=_fail_target)
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
            self._mark_failed(state, grab_key, "chromaprint correlation too low", target_override=_fail_target)
            return False

        log.info(
            "enrichment: chromaprint PASSED — score=%.4f offset=%.2fs",
            alignment.score,
            alignment.offset_seconds,
        )

        # 5. Determine operation type and build ffmpeg command
        grab_info = state.get("grabbed", {}).get(grab_key) or {}
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

        # Dot-prefix hides from media scanners (Radarr/Sonarr) so they
        # don't index the temp file and mark the movie as deleted.
        # Stage in the library file's own container: the staged file
        # replaces it in place, so an MP4 must stay an MP4.
        staging = (
            library_path.parent
            / f".{library_path.stem}.enrichment.tmp{library_path.suffix}"
        )
        # Library + candidate subtitles are re-selected under the media
        # policy (same movies policy as the subtitle targets above).
        selection = selection_for_policy(config.media_policy.movies)

        try:
            if is_video_upgrade:
                # Video upgrade: take video from candidate, audio from library
                # Also grab any extra audio/subs from candidate we don't have
                cmd = build_video_upgrade_command(
                    library=library_path,
                    candidate=candidate_path,
                    offset_seconds=alignment.offset_seconds,
                    destination=staging,
                    selection=selection,
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
                    self._mark_failed(state, grab_key, "nothing to enrich", target_override=_fail_target)
                    return False
                cmd = build_crossmux_command(
                    library=library_path,
                    candidate=candidate_path,
                    audio_tracks=audio_tracks,
                    subtitle_tracks=sub_tracks or None,
                    offset_seconds=alignment.offset_seconds,
                    destination=staging,
                    selection=selection,
                )
                operation = "audio cross-mux"
        except RemuxPolicyError as exc:
            log.error("enrichment: %s", exc)
            self._mark_failed(state, grab_key, "library probe failed", target_override=_fail_target)
            return False

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
                self._mark_failed(state, grab_key, "ffmpeg failed", target_override=_fail_target)
                return False
        except subprocess.TimeoutExpired:
            log.error("enrichment: ffmpeg timed out after 1 hour")
            self._cleanup_staging(staging)
            self._mark_failed(state, grab_key, "ffmpeg timeout", target_override=_fail_target)
            return False

        # 7. Verify output
        if not staging.exists():
            log.error("enrichment: staging file not created")
            self._mark_failed(state, grab_key, "staging file missing", target_override=_fail_target)
            return False

        out_info = probe_streams(staging)
        if out_info is None or not out_info.has_video:
            log.error("enrichment: output file has no video")
            self._cleanup_staging(staging)
            self._mark_failed(state, grab_key, "output has no video", target_override=_fail_target)
            return False

        # 7.5. Scan for orphaned .srt subtitle files and remux valid ones
        # into the staging file.  Bazarr downloads external .srt files
        # that sit next to the video but are never embedded.  This step
        # finds them, validates timing via chromaprint offset, and
        # remuxes them into the MKV container.
        staging = self._remux_orphaned_srt_files(
            library_path,
            staging,
            alignment.offset_seconds,
        )

        # Re-probe after subtitle remux to get updated stream counts
        out_info = probe_streams(staging)
        if out_info is None or not out_info.has_video:
            log.error("enrichment: output file corrupted after subtitle remux")
            self._cleanup_staging(staging)
            self._mark_failed(state, grab_key, "subtitle remux corrupted output", target_override=_fail_target)
            return False

        # Verification depends on operation type
        out_audio = {_normalize_lang(lang) for lang in out_info.audio_languages}

        if is_video_upgrade:
            # For video upgrades: verify library audio tracks are preserved
            for lib_lang in lib_info.audio_languages:
                if _normalize_lang(lib_lang) not in out_audio and lib_lang != "und":
                    log.error(
                        "enrichment: library audio language %s lost in upgrade output",
                        lib_lang,
                    )
                    self._cleanup_staging(staging)
                    self._mark_failed(state, grab_key, f"lost audio {lib_lang}", target_override=_fail_target)
                    return False

            # Audio count should be at least as many as library (preserved)
            if out_info.audio_count < lib_info.audio_count:
                log.error(
                    "enrichment: output lost audio tracks (%d → %d)",
                    lib_info.audio_count,
                    out_info.audio_count,
                )
                self._cleanup_staging(staging)
                self._mark_failed(state, grab_key, "lost audio tracks in upgrade", target_override=_fail_target)
                return False
        else:
            # For audio enrichment: verify new languages are present
            added_langs = [lang for lang in languages_adding if lang in out_audio]
            if not added_langs:
                log.error(
                    "enrichment: none of target languages %s found in output (got: %s)",
                    languages_adding,
                    out_audio,
                )
                self._cleanup_staging(staging)
                self._mark_failed(state, grab_key, "target languages not in output", target_override=_fail_target)
                return False

            # Audio count should increase
            if out_info.audio_count <= lib_info.audio_count:
                log.error(
                    "enrichment: output has %d audio tracks, expected more than %d",
                    out_info.audio_count,
                    lib_info.audio_count,
                )
                self._cleanup_staging(staging)
                self._mark_failed(state, grab_key, "audio count did not increase", target_override=_fail_target)
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
            self._mark_failed(state, grab_key, "output too small", target_override=_fail_target)
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
            self._mark_failed(state, grab_key, f"replace failed: {exc}", target_override=_fail_target)
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
            "pipeline_version": 2,  # Bump when enrichment logic changes
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

        # Clear torrent from blacklist on success — it works after all
        grab_info_bl = state.get("grabbed", {}).get(grab_key, {})
        torrent_id_bl = grab_info_bl.get("torrent_hash") or grab_info_bl.get("torrent_name") or grab_key
        state.get("failed_torrents", {}).pop(torrent_id_bl, None)

        self.repo.emit_event(
            "enrichment", "info",
            f"Enriched {library_path.name} ({operation})",
            {"file": library_path.name, "operation": operation,
             "score": round(alignment.score, 4),
             "offset": round(alignment.offset_seconds, 3),
             "languages": languages_adding},
        )
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

    @staticmethod
    def _extract_hash_from_magnet(magnet: str) -> str:
        """Extract the info hash from a magnet URI, or return empty string."""
        m = re.search(r"btih:([0-9a-fA-F]{40})", magnet)
        if m:
            return m.group(1).lower()
        # Base32 encoded hash (shorter magnet links)
        m = re.search(r"btih:([A-Za-z2-7]{32})", magnet)
        if m:
            import base64
            try:
                return base64.b32decode(m.group(1).upper()).hex()
            except Exception:
                pass
        return ""

    def _add_to_qbt(
        self,
        host: str,
        creds: dict,
        qb_cfg: Any,
        result: ProwlarrResult,
        category: str,
    ) -> Optional[str]:
        """Add a torrent to qBittorrent. Returns torrent hash on success, None on failure."""
        client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={"Host": "localhost:8080"},
        )
        extracted_hash = ""
        try:
            username = creds.get("username") or qb_cfg.username
            password = creds.get("password") or qb_cfg.password
            resp = client.post(
                f"http://{host}:8080/api/v2/auth/login",
                data={"username": username, "password": password},
            )
            if resp.text.strip() != "Ok.":
                log.error("enrichment: qBT auth failed")
                return None

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

            # Collect magnet URI from either field
            log.debug("enrichment: add_to_qbt urls: magnet_url=%s download_url=%s",
                       result.magnet_url, (result.download_url or "")[:80])
            magnet = result.magnet_url
            if not magnet and result.download_url and result.download_url.startswith("magnet:"):
                magnet = result.download_url

            # Try magnet URI first
            if magnet:
                extracted_hash = self._extract_hash_from_magnet(magnet)
                resp = client.post(
                    f"http://{host}:8080/api/v2/torrents/add",
                    data={**add_data, "urls": magnet},
                )
                if resp.status_code == 200:
                    return extracted_hash or self._lookup_torrent_hash(client, host, result.title)
                log.warning("enrichment: qBT rejected magnet (status=%d)", resp.status_code)

            # Fall back to HTTP download URL (may be .torrent or magnet redirect)
            if result.download_url and not result.download_url.startswith("magnet:"):
                try:
                    dl = httpx.get(
                        result.download_url, timeout=30, follow_redirects=False,
                    )
                    # Handle magnet redirects (Prowlarr proxy often does this)
                    if dl.status_code in (301, 302, 303, 307, 308):
                        location = dl.headers.get("location", "")
                        if location.startswith("magnet:"):
                            extracted_hash = self._extract_hash_from_magnet(location)
                            resp = client.post(
                                f"http://{host}:8080/api/v2/torrents/add",
                                data={**add_data, "urls": location},
                            )
                            if resp.status_code == 200:
                                return extracted_hash or self._lookup_torrent_hash(client, host, result.title)
                            log.warning("enrichment: qBT rejected redirected magnet (status=%d)", resp.status_code)

                    # Direct .torrent file download
                    if dl.status_code == 200 and len(dl.content) > 100:
                        resp = client.post(
                            f"http://{host}:8080/api/v2/torrents/add",
                            data=add_data,
                            files={"torrents": ("enrichment.torrent", dl.content, "application/x-bittorrent")},
                        )
                        if resp.status_code == 200:
                            return self._lookup_torrent_hash(client, host, result.title)
                except Exception as exc:
                    log.debug("enrichment: download URL fallback failed: %s", exc)

            log.warning("enrichment: could not add torrent to qBT: %s", result.title[:60])
            return None
        except Exception as exc:
            log.error("enrichment: failed to add to qBT: %s", exc)
            return None
        finally:
            client.close()

    def _lookup_torrent_hash(self, client: httpx.Client, host: str, name: str) -> str:
        """Look up a torrent's hash by name in qBT. Returns hash or empty string."""
        try:
            resp = client.get(
                f"http://{host}:8080/api/v2/torrents/info",
                params={"category": "enrichment"},
            )
            if resp.status_code == 200:
                for t in resp.json() or []:
                    if t.get("name", "") == name:
                        return t.get("hash", "")
        except Exception:
            pass
        return ""

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

    def _find_grab_key(
        self, torrent_name: str, grabbed: dict, torrent_hash: str = "",
    ) -> Optional[str]:
        """Find the grab key for a torrent by matching hash, then name.

        Match passes (most reliable first):
        0. Torrent hash (exact, most reliable)
        1. Exact match on stored torrent_name
        2. Stored name starts with qBT name (indexer adds description suffix)
        3. qBT name starts with stored key (key truncated) — requires min 40 chars
        """
        # Pass 0: Match by torrent hash (most reliable)
        if torrent_hash:
            for key, info in grabbed.items():
                if info.get("torrent_hash") and info["torrent_hash"] == torrent_hash:
                    return key
        # Pass 1: Exact match on stored torrent_name
        for key, info in grabbed.items():
            if info.get("torrent_name") == torrent_name:
                return key
        # Pass 2: Stored name starts with qBT name (indexer added suffix)
        for key, info in grabbed.items():
            stored = info.get("torrent_name", "")
            if stored and stored.startswith(torrent_name):
                return key
        # Pass 3: qBT name starts with stored key (key is truncated)
        # Require minimum 40 chars to prevent short-key false matches
        for key in grabbed:
            prefix = key[:100]
            if len(prefix) >= 40 and torrent_name.startswith(prefix):
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

        # Pack matching only makes sense for TV. A "movie pack" (e.g. a
        # trilogy collection torrent) must not fan out across the library —
        # each movie grab targets exactly one library file.
        if category != "tv":
            return []

        # Find the show directory in the library
        original_target = Path(grab_info.get("target_path", ""))
        if not original_target.exists():
            return []

        # Typical TV layout: /data/tv/Show Name/Season 1/Show - S01E01.mkv
        # → show_dir is parent.parent. But some anime/specials sit directly
        # under the show dir without a Season subfolder, so parent.parent
        # would resolve to /data/tv and iterate every show on disk. Detect
        # that case and fall back to parent. Then validate by requiring
        # show_dir.parent to be a category root (tv/movies) — otherwise we
        # couldn't resolve a real show directory and pack matching is unsafe.
        _LIB_ROOTS = ("tv", "movies")
        show_dir = original_target.parent.parent
        if show_dir.name in _LIB_ROOTS:
            show_dir = original_target.parent
        if not show_dir.is_dir() or show_dir.parent.name not in _LIB_ROOTS:
            log.warning(
                "enrichment: cannot resolve show dir for %s, skipping pack matching",
                original_target.name,
            )
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
                len(pack_files),
                len(results),
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

    def _cleanup_orphaned_staging_files(self, config: StackConfig) -> None:
        """One-time cleanup of .enrichment.tmp.mkv files left from buggy runs.

        Walks the media library and removes any `*.enrichment.tmp.mkv` files
        that coexist with their originals (the original `.mkv` file exists).
        If the original is missing, the tmp file is the only copy — leave it.
        """
        pool_root = self._resolve_pool_root(config)
        removed = 0
        for cat in ("movies", "tv"):
            cat_dir = pool_root / cat
            if not cat_dir.exists():
                continue
            for root, _dirs, files in os.walk(cat_dir):
                for name in files:
                    # Match both old (visible) and new (dot-prefix) patterns
                    if name.endswith(".enrichment.tmp.mkv"):
                        if name.startswith("."):
                            # New pattern: .MovieName.enrichment.tmp.mkv → MovieName.mkv
                            orig_name = name[1:].replace(".enrichment.tmp.mkv", ".mkv")
                        else:
                            # Old pattern: MovieName.enrichment.tmp.mkv → MovieName.mkv
                            orig_name = name.replace(".enrichment.tmp.mkv", ".mkv")
                    else:
                        continue
                    tmp_path = Path(root) / name
                    original = Path(root) / orig_name
                    if original.exists():
                        try:
                            tmp_path.unlink()
                            removed += 1
                        except OSError:
                            pass
        if removed:
            log.info("enrichment: cleaned up %d orphaned .enrichment.tmp.mkv file(s)", removed)

    def _detect_srt_language(self, srt_path: Path, video_stem: str) -> str:
        """Detect subtitle language from SRT filename.

        Searches from the end of the filename parts (closest to .srt extension)
        and validates against known language codes to avoid matching random words.

        Examples:
            "Show - S01E01.en.srt" → "en"
            "Show - S01E01.en.hi.srt" → "en" (skips "hi" which is hearing impaired)
            "Show - S01E01.srt" → "und" (no language tag)
        """
        return subtitle_identity_from_srt_filename(srt_path, video_stem).lang

    def _existing_subrip_identities(self, video_path: Path) -> Set[SubtitleIdentity]:
        """Return existing embedded SubRip language/kind identities."""
        import json as _json
        import subprocess as _sp

        try:
            result = _sp.run(
                ["mkvmerge", "-J", str(video_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode not in (0, 1):
                return set()
            data = _json.loads(result.stdout)
        except (_sp.TimeoutExpired, _json.JSONDecodeError, OSError):
            return set()

        identities: Set[SubtitleIdentity] = set()
        for track in data.get("tracks", []):
            if track.get("type") != "subtitles":
                continue
            if track.get("codec") != "SubRip/SRT":
                continue
            identity = subtitle_identity_from_mkvmerge_track(track)
            if identity:
                identities.add(identity)
        return identities

    def _remux_orphaned_srt_files(
        self,
        library_path: Path,
        staging: Path,
        offset_seconds: float,
    ) -> Path:
        """Scan for external .srt files next to the library file and remux
        valid ones into the staging MKV.

        Bazarr downloads external .srt files that sit alongside the video
        (e.g., ``Show - S01E01.en.srt``).  These are never embedded by the
        pipeline.  This step finds them, validates timing against the video,
        and remuxes them into the MKV container so Jellyfin can serve them
        without external file dependencies.

        After successful remux, the source .srt file is deleted since its
        content is now embedded in the MKV.

        The chromaprint offset is NOT applied to these SRTs — they were
        downloaded by Bazarr for the library file directly, not for the
        enrichment candidate. The offset only applies to subtitles extracted
        from the candidate (a different video release).

        Args:
            library_path: Original library file (used to locate sibling .srt).
            staging: Current staging file to remux subtitles into.
            offset_seconds: Chromaprint alignment offset (NOT used for Bazarr SRTs).

        Returns:
            Path to the (possibly new) staging file after remux.
        """
        from .subtitle_align import (
            remux_subtitle_into_mkv,
            validate_timing,
        )

        srt_dir = library_path.parent
        video_base = library_path.stem

        # Find all .srt files that match the video base name
        # Patterns: Show - S01E01.en.srt, Show - S01E01.en.hi.srt
        srt_files = list(srt_dir.glob(f"{video_base}*.srt"))
        if not srt_files:
            return staging

        # Probe video duration for validation
        video_duration = probe_video_duration(staging)
        if video_duration is None:
            log.warning(
                "enrichment: cannot probe staging duration for %s — skipping subtitle remux",
                staging.name,
            )
            return staging

        # remux_subtitle_into_mkv only writes MKV; never put MKV content
        # into a staged MP4 that will replace an MP4 library file.
        if staging.suffix.lower() != ".mkv":
            log.info(
                "enrichment: %s is not MKV — leaving external subtitles alone",
                staging.name,
            )
            return staging

        current_output = staging
        remuxed_count = 0
        deleted_srts: list[Path] = []
        existing_identities = self._existing_subrip_identities(current_output)

        for srt_path in sorted(srt_files):
            if srt_path.stat().st_size == 0:
                continue

            # Parse and validate the SRT
            entries = parse_srt(srt_path)
            if not entries:
                log.debug("enrichment: skipping empty/unparseable %s", srt_path.name)
                continue

            identity = subtitle_identity_from_srt_filename(srt_path, video_base)
            if identity in existing_identities:
                deleted_srts.append(srt_path)
                log.info(
                    "enrichment: skipping duplicate orphaned subtitle %s (%s/%s)",
                    srt_path.name,
                    identity.lang,
                    identity.kind,
                )
                continue

            # Validate timing
            validation = validate_timing(entries, video_duration)
            if not validation.is_valid:
                log.warning(
                    "enrichment: subtitle %s failed validation: %s",
                    srt_path.name,
                    ", ".join(validation.issues),
                )
                continue

            # Reject signs-only / sparse subtitles (< 3 cues per minute)
            _MIN_CUES_PER_MINUTE = 3
            cues_per_min = len(entries) / max(video_duration / 60, 1)
            if cues_per_min < _MIN_CUES_PER_MINUTE:
                log.debug(
                    "enrichment: skipping sparse subtitle %s (%.1f cues/min)",
                    srt_path.name, cues_per_min,
                )
                continue

            # NOTE: Do NOT apply the chromaprint offset to Bazarr SRTs.
            # The offset corrects for timing differences between the library
            # file and the enrichment candidate. Bazarr SRTs are authored for
            # the library file directly — applying the offset would misalign them.
            srt_to_remux = srt_path

            # Create a new staging file with the subtitle remuxed
            new_staging = current_output.with_suffix(
                f".subremux{remuxed_count}.tmp.mkv"
            )

            ok = remux_subtitle_into_mkv(
                video_path=current_output,
                srt_path=srt_to_remux,
                output_path=new_staging,
                language=identity.lang,
                forced=identity.kind == "forced",
                hearing_impaired=identity.kind == "hearing_impaired",
            )

            if ok and new_staging.exists():
                # Clean up old staging
                if current_output != staging:
                    self._cleanup_staging(current_output)
                current_output = new_staging
                remuxed_count += 1
                existing_identities.add(identity)
                deleted_srts.append(srt_path)
                log.info(
                    "enrichment: remuxed orphaned subtitle %s (%s/%s) into staging",
                    srt_path.name,
                    identity.lang,
                    identity.kind,
                )
            else:
                log.warning("enrichment: failed to remux subtitle %s", srt_path.name)
                self._cleanup_staging(new_staging)

        # Delete source SRT files after all remuxes succeed
        for srt_path in deleted_srts:
            try:
                srt_path.unlink()
                log.debug("enrichment: deleted embedded SRT: %s", srt_path.name)
            except OSError as exc:
                log.warning("enrichment: failed to delete %s: %s", srt_path.name, exc)

        if remuxed_count > 0:
            log.info(
                "enrichment: remuxed %d orphaned subtitle(s) into staging, "
                "deleted %d source SRT(s)",
                remuxed_count,
                len(deleted_srts),
            )

        return current_output

    def _mark_failed(
        self, state: dict, grab_key: str, reason: str, *,
        target_override: Optional[str] = None,
    ) -> None:
        """Record a failed enrichment attempt.

        When processing pack episodes, ``target_override`` should be the
        actual episode path so each episode's failure is recorded independently
        (not under the grab's original target path).
        """
        processed = state.setdefault("processed", {})
        if target_override:
            target = target_override
        else:
            grab_info = state.get("grabbed", {}).get(grab_key, {})
            target = grab_info.get("target_path", grab_key)
        processed[target] = {
            "status": "failed",
            "reason": reason,
            "timestamp": int(time.time()),
        }
        self.repo.emit_event(
            "enrichment", "warn",
            f"Enrichment failed: {Path(target).name} — {reason}",
            {"target": target, "reason": reason},
        )

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
