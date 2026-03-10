"""Pipeline worker loop to remux completed torrents."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Set

import httpx

from ..models import StackConfig
from ..storage import ConfigRepository
from .languages import arr_language_to_iso
from .worker import PipelineWorker, TorrentInfo, parse_movie_name

log = logging.getLogger("pipeline")

if not log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)


@dataclass
class TorrentRecord:
    hash: str
    name: str
    category: str
    save_path: Path
    content_path: Path
    size: int = 0  # total size in bytes
    completion_on: int = 0  # unix timestamp when torrent finished downloading


@dataclass
class ArrMetadata:
    """Metadata fetched from Radarr/Sonarr for a matched torrent."""
    original_language: Optional[str] = None
    library_path: Optional[Path] = None
    media_id: Optional[int] = None       # Radarr movieId or Sonarr seriesId
    service_name: Optional[str] = None   # "radarr" or "sonarr"


def _match_torrent_to_arr(torrent_name: str, items: list) -> Optional[dict]:
    """Match a torrent name to a Radarr/Sonarr library entry.

    Uses word-boundary regex instead of naive substring matching to prevent
    false positives like "Ray" matching "BluRay".  Scores candidates by
    title length, year match, and primary-title bonus.

    Short titles (≤4 chars) require a year confirmation to match, preventing
    "Her" from matching "Ot*her*" or "Up" from matching "S*up*erman".
    """
    # Normalize torrent name: replace . and _ with spaces, lowercase
    normalized = re.sub(r'[._]', ' ', torrent_name).lower()

    # Extract year from torrent name
    _, torrent_year = parse_movie_name(torrent_name)

    best_match: Optional[dict] = None
    best_score = 0

    for item in items:
        # Build candidate title list: primary title + sort title + alternatives
        candidates: list[tuple[str, bool]] = []  # (title, is_primary)
        primary_title = item.get("title", "")
        if primary_title:
            candidates.append((primary_title, True))

        sort_title = item.get("sortTitle", "")
        if sort_title and sort_title.lower() != primary_title.lower():
            candidates.append((sort_title, False))

        for alt in item.get("alternativeTitles", []):
            alt_title = alt.get("title", "")
            if alt_title:
                candidates.append((alt_title, False))

        # Extract year from the arr item
        item_year = str(item.get("year", "")) if item.get("year") else None

        for candidate_title, is_primary in candidates:
            title_lower = candidate_title.lower()
            if len(title_lower) < 2:
                continue

            # Escape regex special characters in the title, then replace
            # spaces with flexible whitespace/separator pattern
            escaped = re.escape(title_lower)
            # Allow spaces in the title to match any separator (space, dot,
            # underscore) in the torrent name — already normalized to spaces
            pattern = escaped.replace(r'\ ', r'\s+')

            # Word-boundary match: title must not be preceded/followed by
            # alphanumeric chars.  This prevents "ray" matching inside
            # "bluray" (preceded by 'u').
            boundary_pattern = rf'(?:^|[^a-z0-9]){pattern}(?:$|[^a-z0-9])'

            if not re.search(boundary_pattern, normalized):
                continue

            # Calculate score
            score = len(title_lower) * 10  # longer titles score higher

            # Year bonus
            if torrent_year and item_year:
                if torrent_year == item_year:
                    score += 500  # exact year match
                elif abs(int(torrent_year) - int(item_year)) <= 1:
                    score += 200  # off-by-one year (re-releases)

            # Primary title bonus
            if is_primary:
                score += 50

            # Short title safety: titles ≤4 chars MUST have a year match
            if len(title_lower) <= 4:
                if not (torrent_year and item_year and
                        abs(int(torrent_year) - int(item_year)) <= 1):
                    continue  # skip — too risky without year confirmation

            if score > best_score:
                best_score = score
                best_match = item

    return best_match


class QbittorrentAPI:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        # Add Host header for qBittorrent CSRF protection when using port mapping
        headers = {"Host": "localhost:8080"}
        self.client = httpx.Client(
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers=headers,
        )

    def close(self) -> None:
        self.client.close()

    def login(self) -> None:
        response = self.client.post(
            f"{self.base_url}/api/v2/auth/login",
            data={"username": self.username, "password": self.password},
        )
        response.raise_for_status()
        if response.text.strip() != "Ok.":
            raise RuntimeError("qBittorrent authentication failed")

    def list_completed(self) -> List[TorrentRecord]:
        response = self.client.get(
            f"{self.base_url}/api/v2/torrents/info",
            params={"filter": "completed"},
        )
        response.raise_for_status()
        items = response.json() or []
        records: List[TorrentRecord] = []
        for item in items:
            records.append(
                TorrentRecord(
                    hash=item.get("hash", ""),
                    name=item.get("name", ""),
                    category=item.get("category") or "",
                    save_path=Path(item.get("save_path") or ""),
                    content_path=Path(item.get("content_path") or ""),
                    size=item.get("size") or 0,
                    completion_on=item.get("completion_on") or 0,
                )
            )
        return [record for record in records if record.hash and record.save_path]

    def list_all_names(self) -> Set[str]:
        """Return the names of ALL torrents (any state) in qBittorrent."""
        response = self.client.get(
            f"{self.base_url}/api/v2/torrents/info",
        )
        response.raise_for_status()
        items = response.json() or []
        return {item.get("name", "") for item in items if item.get("name")}

    def list_files(self, torrent_hash: str) -> List[Path]:
        response = self.client.get(
            f"{self.base_url}/api/v2/torrents/files",
            params={"hash": torrent_hash},
        )
        response.raise_for_status()
        files = response.json() or []
        return [Path(entry.get("name", "")) for entry in files if entry.get("name")]

    def remove_torrents(
        self, torrent_hashes: Iterable[str], *, delete_files: bool = True
    ) -> None:
        hashes = "|".join(torrent_hashes)
        if not hashes:
            return
        response = self.client.post(
            f"{self.base_url}/api/v2/torrents/delete",
            data={
                "hashes": hashes,
                "deleteFiles": "true" if delete_files else "false",
            },
        )
        response.raise_for_status()


class PipelineRunner:
    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    def run_forever(self, interval: float = 60.0) -> None:
        log.info("starting worker loop (interval=%ss)", interval)
        while True:
            try:
                self._tick()
                self._save_tick_health(error=None)
            except Exception as exc:  # pragma: no cover - runtime safety net
                log.error("error: %s", exc)
                self._save_tick_health(error=str(exc))
            time.sleep(interval)

    def _save_tick_health(self, error: str | None) -> None:
        """Persist last_tick timestamp and optional error to pipeline state."""
        from datetime import datetime, timezone

        self.repo.update_pipeline_health(
            last_tick=datetime.now(timezone.utc).isoformat(),
            error=error,
        )

    def _tick(self) -> None:
        config = self.repo.load_stack()
        if not config.services.pipeline.enabled:
            return

        self._cleanup_stale_staging(config)

        state = self.repo.load_state()
        qb_secrets = state.get("secrets", {}).get("qbittorrent", {})

        qb_cfg = config.services.qbittorrent
        # When VPN is active, qBittorrent shares gluetun's network namespace
        # and is reachable at gluetun:<port>, not qbittorrent:<port>.
        vpn_active = config.services.gluetun.enabled
        qb_host = "gluetun" if vpn_active else "qbittorrent"
        base_url = f"http://{qb_host}:8080"
        api = QbittorrentAPI(
            base_url=base_url,
            username=qb_secrets.get("username") or qb_cfg.username,
            password=qb_secrets.get("password") or qb_cfg.password,
        )

        needs_refresh: dict[str, bool] = {}  # category -> True
        qbt_all_names: Set[str] = set()  # names of ALL torrents in qBT

        # ── Phase 1: Process torrents tracked by qBittorrent ──────────
        try:
            api.login()
            qbt_all_names = api.list_all_names()
            torrents = api.list_completed()

            if torrents:
                # ── Stale cleanup sweep ──────────────────────────────────
                # Remove torrents (+ source files) that are already marked
                # as processed in pipeline state but were never cleaned up.
                #
                # CRITICAL: If the torrent completed in qBT *after* we
                # marked it processed, it's a re-download.  Clear the old
                # processed entry so the pipeline reprocesses it fresh.
                STALE_AGE = 24 * 3600  # 24 hours
                now = time.time()
                stale_hashes: list[str] = []
                reprocess_hashes: list[str] = []
                for t in torrents:
                    if not self._is_processed(t.hash):
                        continue
                    entry = self._processed_entry(t.hash)
                    if not entry:
                        continue
                    status = entry.get("status")
                    processed_at = entry.get("timestamp", 0)
                    age = now - processed_at

                    if t.completion_on > 0 and processed_at > 0:
                        if t.completion_on > processed_at:
                            log.info(
                                "re-download detected: %s — clearing old '%s' "
                                "state to reprocess",
                                t.name[:60], status,
                            )
                            reprocess_hashes.append(t.hash)
                            continue

                    if status == "ok":
                        log.info(
                            "stale cleanup: %s (%.1f GB)",
                            t.name[:60], t.size / (1024**3),
                        )
                        stale_hashes.append(t.hash)
                    elif status == "partial":
                        if not t.content_path.exists():
                            log.info(
                                "stale cleanup (partial, no files): %s",
                                t.name[:60],
                            )
                            stale_hashes.append(t.hash)
                        elif age > STALE_AGE:
                            log.info(
                                "stale cleanup (partial, %.0fh old): %s (%.1f GB)",
                                age / 3600, t.name[:60], t.size / (1024**3),
                            )
                            stale_hashes.append(t.hash)
                    elif status in ("ffmpeg_failed", "plan_failed"):
                        if age > STALE_AGE:
                            log.info(
                                "stale cleanup (%s, %.0fh old): %s (%.1f GB)",
                                status, age / 3600, t.name[:60], t.size / (1024**3),
                            )
                            stale_hashes.append(t.hash)

                for h in reprocess_hashes:
                    self._clear_processed(h)

                if stale_hashes:
                    api.remove_torrents(stale_hashes, delete_files=True)
                    log.info(
                        "removed %d stale torrent(s) from qBittorrent",
                        len(stale_hashes),
                    )

                # Filter to processable torrents, then sort smallest-first.
                pending = [
                    t for t in torrents
                    if self._should_process(config, t.category)
                    and not self._is_processed(t.hash)
                ]
                pending.sort(key=lambda t: t.size)

                for torrent in pending:
                    dest_free = self._get_dest_free(config)
                    needed = torrent.size
                    if needed > 0 and dest_free < needed:
                        log.warning(
                            "skipping %s... (%.1f GB) — only %.1f GB free on pool",
                            torrent.name[:50], torrent.size / (1024**3),
                            dest_free / (1024**3),
                        )
                        continue
                    ok = self._process_torrent(api, config, torrent)
                    if ok:
                        needs_refresh[torrent.category] = True

        except Exception as exc:
            log.error("qBittorrent error: %s", exc)
        finally:
            api.close()

        # ── Phase 2: Process orphans on disk not tracked by qBittorrent ──

        # Expire old orphan hashes (7 days)
        ORPHAN_EXPIRY = 7 * 86400
        now = time.time()
        pipeline = self.repo.load_pipeline_state()
        processed = pipeline.get("processed", {})
        expired = [
            h for h, entry in processed.items()
            if h.startswith("orphan_")
            and isinstance(entry, dict)
            and now - entry.get("timestamp", now) > ORPHAN_EXPIRY
        ]
        for h in expired:
            self.repo.delete_pipeline_entry(h)
        if expired:
            log.info("expired %d orphan hash(es)", len(expired))

        try:
            orphan_ok = self._scan_orphans(config, qbt_all_names)
            if orphan_ok:
                # Orphan scanner returns the categories that had successes
                for cat in orphan_ok:
                    needs_refresh[cat] = True
        except Exception as exc:
            log.error("orphan scan error: %s", exc)

        # ── Phase 3: Trigger Sonarr/Radarr library refresh ───────────
        if needs_refresh:
            self._refresh_arr_services(config, needs_refresh)

    def _should_process(self, config: StackConfig, category: str) -> bool:
        """Check if a torrent category should be processed.

        Normalizes category to handle *arr service suffixes (e.g., 'tv-sonarr' -> 'tv').
        """
        # Normalize category to strip *arr suffixes
        normalized = category
        for suffix in ["-sonarr", "-radarr"]:
            if category.endswith(suffix):
                normalized = category[: -len(suffix)]
                break

        categories = config.download_policy.categories
        return normalized in {categories.radarr, categories.sonarr}

    # ------------------------------------------------------------------
    # Orphan scanner — process items on disk not tracked by qBittorrent
    # ------------------------------------------------------------------

    # Minimum age (seconds) before an orphan is considered stable enough
    # to process.  Prevents grabbing files mid-copy or mid-download.
    _ORPHAN_STABLE_AGE = 300  # 5 minutes

    def _resolve_complete_dir(self, config: StackConfig) -> Path | None:
        """Find the downloads/complete directory, trying Docker paths first."""
        candidates = [
            Path("/downloads/complete"),
            Path(config.paths.scratch) / "complete" if config.paths.scratch else None,
            Path("/mnt/scratch/complete"),
        ]
        for candidate in candidates:
            if candidate is not None and candidate.is_dir():
                return candidate
        return None

    def _scan_orphans(
        self, config: StackConfig, qbt_names: Set[str],
    ) -> dict[str, bool]:
        """Scan the downloads/complete directory for orphaned items.

        An orphan is a file/directory in the complete folder that:
        - Is not tracked by any qBittorrent torrent (by name)
        - Has not been modified in the last 5 minutes (stable)
        - Has not already been processed (by deterministic hash)

        Returns a dict of categories that had successful processing,
        suitable for passing to ``_refresh_arr_services()``.
        """
        # Resolve the downloads/complete path (inside the container).
        # In Docker, /downloads is the standard mount point; try it first.
        complete_dir = self._resolve_complete_dir(config)
        if complete_dir is None:
            return {}

        now = time.time()
        refreshed: dict[str, bool] = {}

        # Collect items to scan.  qBittorrent saves into category sub-dirs
        # (e.g. complete/movies/, complete/tv/), so we need to look inside
        # those rather than treating them as orphans themselves.
        categories = config.download_policy.categories
        category_dirs = {categories.radarr, categories.sonarr}  # e.g. {"movies", "tv"}

        scan_items: list[tuple[Path, str | None]] = []  # (path, forced_category)
        for item in sorted(complete_dir.iterdir()):
            if item.name.startswith("."):
                continue
            if item.is_dir() and item.name in category_dirs:
                # Descend into category sub-directories
                for child in sorted(item.iterdir()):
                    if not child.name.startswith("."):
                        scan_items.append((child, item.name))
            else:
                scan_items.append((item, None))

        for item, forced_category in scan_items:
            name = item.name
            if name.startswith("."):
                continue

            # Skip items that qBittorrent knows about (any state)
            if name in qbt_names:
                continue

            # Stability check: item must not have been modified recently.
            # For directories, check the most recently modified file.
            try:
                if item.is_dir():
                    mtimes = [
                        f.stat().st_mtime
                        for f in item.rglob("*")
                        if f.is_file()
                    ]
                    newest = max(mtimes) if mtimes else item.stat().st_mtime
                else:
                    newest = item.stat().st_mtime

                if now - newest < self._ORPHAN_STABLE_AGE:
                    continue  # Still being written to
            except OSError:
                continue

            # Generate a deterministic hash from the name so we can track
            # processed state across restarts.
            orphan_hash = "orphan_" + hashlib.sha256(
                name.encode()
            ).hexdigest()[:16]

            if self._is_processed(orphan_hash):
                continue

            # Detect category — prefer the parent directory name when the
            # item came from a category sub-directory (e.g. complete/movies/).
            category = forced_category or self._detect_orphan_category(name, config)

            # Collect files
            if item.is_file():
                files = [item]
            else:
                files = sorted(
                    (f for f in item.rglob("*") if f.is_file()),
                    key=lambda p: p.stat().st_size,
                    reverse=True,
                )

            if not files:
                continue

            size = sum(f.stat().st_size for f in files)
            size_gb = size / (1024 ** 3)

            # Skip directories with no meaningful content — files exist but
            # are all 0 bytes, meaning they're still being written/allocated.
            # Also require at least one video file to avoid wasting a retry
            # on directories that only contain .nfo/.txt/.jpg extras.
            has_video = any(
                f.suffix.lower() in {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts", ".iso"}
                for f in files
            )
            if item.is_dir() and (size == 0 or not has_video):
                log.debug(
                    "skipping orphan %s — no video content yet "
                    "(size=%.1f GB, videos=%s)",
                    name[:60], size_gb, has_video,
                )
                continue

            log.info(
                "orphan detected: %s (%.1f GB, category=%s)",
                name, size_gb, category,
            )

            # Check destination space
            dest_free = self._get_dest_free(config)
            if size > 0 and dest_free < size:
                log.warning(
                    "skipping orphan %s... (%.1f GB) — only %.1f GB free on pool",
                    name[:50], size_gb, dest_free / (1024**3),
                )
                continue

            # Build a TorrentRecord for the orphan
            torrent = TorrentRecord(
                hash=orphan_hash,
                name=name,
                category=category,
                save_path=complete_dir,
                content_path=item,
                size=size,
            )

            # Process using the same pipeline as regular torrents,
            # but without qBT API calls for file listing and removal.
            ok = self._process_orphan(config, torrent, files)
            if ok:
                refreshed[category] = True

        return refreshed

    def _detect_orphan_category(
        self, name: str, config: StackConfig
    ) -> str:
        """Guess category (movies/tv) from an orphan's directory name."""
        name_lower = name.lower()
        tv_patterns = [".s0", ".s1", ".s2", ".s3", " s0", " s1", " s2",
                       " s3", "season", "complete series"]
        if any(p in name_lower for p in tv_patterns):
            return config.download_policy.categories.sonarr
        return config.download_policy.categories.radarr

    def _process_orphan(
        self,
        config: StackConfig,
        torrent: TorrentRecord,
        files: List[Path],
    ) -> bool:
        """Process an orphan item through the remux pipeline.

        Similar to ``_process_torrent()`` but doesn't use qBT API for
        file listing or torrent removal (since qBT doesn't track these).
        """
        info = TorrentInfo(
            hash=torrent.hash,
            name=torrent.name,
            category=torrent.category,
            download_path=torrent.save_path,
            files=files,
        )

        # Check for ISO files
        iso_dir: Optional[Path] = None
        iso_file = self._find_iso_file(files)
        if iso_file:
            try:
                iso_dir = self._open_iso(iso_file, torrent.hash)
            except (RuntimeError, OSError) as exc:
                log.error("orphan ISO open failed: %s", exc)
                self._mark_processed(
                    torrent.hash, "plan_failed", f"ISO: {exc}"
                )
                return False

        try:
            return self._execute_orphan_pipeline(
                config, torrent, info, iso_mount_dir=iso_dir,
            )
        finally:
            if iso_dir:
                self._close_iso(iso_dir)

    def _execute_orphan_pipeline(
        self,
        config: StackConfig,
        torrent: TorrentRecord,
        info: TorrentInfo,
        *,
        iso_mount_dir: Optional[Path] = None,
    ) -> bool:
        """Run the remux pipeline for an orphan (no qBT API needed)."""
        metadata = self._lookup_arr_metadata(config, torrent)
        keep_audio_langs = set(config.media_policy.movies.keep_audio)

        worker = PipelineWorker(config)
        try:
            plans = worker.build_plans(
                info,
                original_language=metadata.original_language if metadata else None,
                library_path=metadata.library_path if metadata else None,
                iso_mount_dir=iso_mount_dir,
            )
        except ValueError as exc:
            log.error("orphan plan failed for %s: %s", torrent.name, exc)
            # NEVER delete orphan source on plan failure — there is no way to
            # re-download.  Mark as failed; retry logic will re-attempt later.
            self._mark_processed(torrent.hash, "plan_failed", str(exc))
            return False

        total = len(plans)
        succeeded = 0
        failed = 0

        for i, plan in enumerate(plans, 1):
            log.info(
                "  [%d/%d] remuxing: %s -> %s",
                i, total, plan.source.name, plan.final_output.name,
            )
            success = self._run_ffmpeg(
                plan.ffmpeg_command, source=plan.source
            )
            if not success:
                log.error(
                    "  [%d/%d] ffmpeg FAILED for %s",
                    i, total, plan.source.name,
                )
                if plan.staging_output.exists():
                    try:
                        plan.staging_output.unlink()
                    except OSError:
                        pass
                failed += 1
                continue

            if not self._validate_output(
                plan.staging_output, plan.source,
                keep_audio_langs=keep_audio_langs,
            ):
                log.error(
                    "  [%d/%d] REJECTED: output failed validation",
                    i, total,
                )
                failed += 1
                continue

            plan.final_output.parent.mkdir(parents=True, exist_ok=True)
            if plan.final_output.exists():
                existing_size = plan.final_output.stat().st_size
                new_size = plan.staging_output.stat().st_size
                if existing_size > new_size:
                    log.warning(
                        "  [%d/%d] REFUSED to overwrite existing %s "
                        "(%.2f GB) with smaller file (%.2f GB)",
                        i, total, plan.final_output.name,
                        existing_size / (1024**3), new_size / (1024**3),
                    )
                    try:
                        plan.staging_output.unlink()
                    except OSError:
                        pass
                    failed += 1
                    continue

            shutil.move(str(plan.staging_output), str(plan.final_output))
            log.info("  [%d/%d] moved to %s", i, total, plan.final_output)
            succeeded += 1

        # Clean up staging files
        if plans:
            for plan in plans:
                if plan.staging_output.exists():
                    try:
                        plan.staging_output.unlink()
                    except OSError:
                        pass

        if failed == 0 and succeeded > 0:
            self._cleanup_path(torrent.content_path)
            self._cleanup_empty_parent(torrent.content_path)
            self._mark_processed(
                torrent.hash, "ok",
                f"orphan: {succeeded}/{total} files processed"
            )
            size_gb = torrent.size / (1024 ** 3)
            log.info(
                "orphan completed: %s (%d/%d files, freed %.1f GB)",
                torrent.name, succeeded, total, size_gb,
            )
            return True
        elif succeeded > 0:
            self._mark_processed(
                torrent.hash, "partial",
                f"orphan: {succeeded}/{total} ok, {failed}/{total} failed"
            )
            log.warning(
                "orphan partial: %s (%d/%d ok, %d/%d failed)",
                torrent.name, succeeded, total, failed, total,
            )
            return True
        else:
            self._mark_processed(
                torrent.hash, "ffmpeg_failed",
                f"orphan: all {total} files failed"
            )
            log.error(
                "orphan FAILED: %s (all %d files failed)",
                torrent.name, total,
            )
            return False

    def _cleanup_stale_staging(self, config: StackConfig) -> None:
        """Remove stale .tmp_ staging files left by crashed remux operations."""
        max_age = 2 * 3600  # 2 hours
        now = time.time()

        # Same resolution as PipelineWorker._resolve_pool_root
        pool_root: Path | None = None
        for candidate in (Path("/data"), Path(config.paths.pool)):
            if candidate.exists():
                pool_root = candidate
                break
        if pool_root is None:
            return

        for media_dir in (pool_root / "movies", pool_root / "tv"):
            if not media_dir.is_dir():
                continue
            for tmp_file in media_dir.rglob(".tmp_*"):
                if not tmp_file.is_file():
                    continue
                try:
                    age = now - tmp_file.stat().st_mtime
                    if age > max_age:
                        size_gb = tmp_file.stat().st_size / (1024**3)
                        tmp_file.unlink()
                        log.info(
                            "cleaned stale staging file: %s (%.1f GB, %.0fh old)",
                            tmp_file.name, size_gb, age / 3600,
                        )
                except OSError:
                    pass

    def _get_dest_free(self, config: StackConfig) -> int:
        """Return free bytes on the destination (pool) filesystem.

        Since the pipeline now writes directly to pool, we check
        pool free space instead of scratch.
        """
        # Same resolution as PipelineWorker._resolve_pool_root
        for candidate in (Path("/data"), Path(config.paths.pool)):
            if candidate.exists():
                return shutil.disk_usage(candidate).free
        return shutil.disk_usage("/").free

    def _normalize_category(self, category: str) -> str:
        """Strip *arr suffixes from a category name."""
        for suffix in ["-sonarr", "-radarr"]:
            if category.endswith(suffix):
                return category[: -len(suffix)]
        return category

    # Statuses that represent permanent outcomes — the item is done.
    _TERMINAL_STATUSES = frozenset({"ok", "partial", "skipped_no_files"})
    # Failed statuses eligible for retry after a cooldown.
    _RETRYABLE_STATUSES = frozenset({"plan_failed", "ffmpeg_failed"})
    # How long before a failed orphan is retried (30 minutes).
    _ORPHAN_RETRY_DELAY = 30 * 60
    # Maximum retry attempts before giving up permanently.
    _ORPHAN_MAX_RETRIES = 5

    def _is_processed(self, torrent_hash: str) -> bool:
        pipeline = self.repo.load_pipeline_state()
        processed = pipeline.get("processed", {})
        entry = processed.get(torrent_hash)
        if entry is None:
            return False
        if not isinstance(entry, dict):
            return True  # legacy format — treat as processed
        status = entry.get("status", "ok")
        if status in self._TERMINAL_STATUSES:
            return True
        if status in self._RETRYABLE_STATUSES:
            # Orphan failures get retried after a cooldown, up to max retries
            if torrent_hash.startswith("orphan_"):
                retries = entry.get("retries", 0)
                if retries >= self._ORPHAN_MAX_RETRIES:
                    return True  # exhausted retries
                elapsed = time.time() - entry.get("timestamp", 0)
                # Exponential backoff: 30m, 60m, 120m, 240m, 480m
                delay = self._ORPHAN_RETRY_DELAY * (2 ** retries)
                if elapsed < delay:
                    return True  # not yet time to retry
                return False  # eligible for retry
            # Non-orphan failures: handled by the stale cleanup in Phase 1
            return True
        return True

    def _processed_status(self, torrent_hash: str) -> Optional[str]:
        """Return the pipeline status string for a torrent, or None."""
        entry = self._processed_entry(torrent_hash)
        if entry:
            return entry.get("status")
        return None

    def _processed_entry(self, torrent_hash: str) -> Optional[dict]:
        """Return the full pipeline entry dict for a torrent, or None."""
        pipeline = self.repo.load_pipeline_state()
        processed = pipeline.get("processed", {})
        entry = processed.get(torrent_hash)
        return entry if isinstance(entry, dict) else None

    def _mark_processed(
        self, torrent_hash: str, status: str, detail: str = ""
    ) -> None:
        entry: dict = {"status": status, "timestamp": int(time.time())}
        if detail:
            entry["detail"] = detail
        # Track retry count for failed orphans so we can enforce max retries
        if status in self._RETRYABLE_STATUSES and torrent_hash.startswith("orphan_"):
            prev = self._processed_entry(torrent_hash)
            if prev and prev.get("status") in self._RETRYABLE_STATUSES:
                entry["retries"] = prev.get("retries", 0) + 1
            else:
                entry["retries"] = 0
        self.repo.update_pipeline_entry(torrent_hash, entry)

    def _clear_processed(self, torrent_hash: str) -> None:
        """Remove a torrent from the processed set so it can be reprocessed."""
        self.repo.delete_pipeline_entry(torrent_hash)

    def _lookup_arr_metadata(
        self, config: StackConfig, torrent: TorrentRecord
    ) -> Optional[ArrMetadata]:
        """Query Radarr or Sonarr for metadata about a movie/show.

        Primary method: hash-based lookup via the *arr download history.
        The *arr ``downloadId`` field IS the torrent hash, giving us a
        direct, 100% reliable mapping from torrent → movie/show.

        Fallback: word-boundary name matching (for manual qBittorrent
        additions that don't appear in *arr history).

        Extracts:
        - ``original_language``: ISO 639-2/B code (e.g. "eng", "jpn") from
          the *arr ``originalLanguage`` field (sourced from TMDB/TVDB).
        - ``library_path``: The canonical library path from the *arr ``path``
          field (e.g. ``/data/movies/Hereditary (2018)``).

        Returns an ``ArrMetadata`` with whatever fields could be resolved,
        or ``None`` if the lookup failed entirely (no API key, wrong
        category, network error).
        """
        state = self.repo.load_state()
        secrets_state = state.get("secrets", {})
        cat_config = config.download_policy.categories
        normalized = self._normalize_category(torrent.category)

        if normalized == cat_config.radarr:
            service_name = "radarr"
            port = 7878
            movie_endpoint = "/api/v3/movie"
            history_endpoint = "/api/v3/history"
        elif normalized == cat_config.sonarr:
            service_name = "sonarr"
            port = 8989
            movie_endpoint = "/api/v3/series"
            history_endpoint = "/api/v3/history"
        else:
            return None

        api_key = secrets_state.get(service_name, {}).get("api_key")
        if not api_key:
            log.debug("no API key for %s, skipping metadata lookup", service_name)
            return None

        headers = {"X-Api-Key": api_key}
        timeout = httpx.Timeout(15.0, connect=5.0)
        # Radarr/Sonarr share gluetun's network namespace when VPN is active
        host = service_name
        if config.services.gluetun.enabled:
            from ..models import VPN_ROUTED_SERVICES
            if service_name in VPN_ROUTED_SERVICES:
                host = "gluetun"
        base = f"http://{host}:{port}"

        try:
            # ── Primary: hash-based lookup via download history ──────────
            # The *arr downloadId is the torrent hash (uppercase).
            download_id = torrent.hash.upper()
            hist_response = httpx.get(
                f"{base}{history_endpoint}",
                params={"downloadId": download_id, "pageSize": 5},
                headers=headers,
                timeout=timeout,
            )
            hist_response.raise_for_status()
            hist_data = hist_response.json()
            hist_records = hist_data.get("records", [])

            if hist_records:
                # Found in history — get the media ID directly
                media_id = hist_records[0].get("movieId") or hist_records[0].get("seriesId")
                if media_id:
                    # Fetch the full movie/series record by ID
                    item_response = httpx.get(
                        f"{base}{movie_endpoint}/{media_id}",
                        headers=headers,
                        timeout=timeout,
                    )
                    item_response.raise_for_status()
                    matched_item = item_response.json()
                    title = matched_item.get("title", "")
                    log.debug(
                        "hash lookup: %s... -> '%s' (id=%s)",
                        download_id[:12], title, media_id,
                    )
                    return self._extract_arr_metadata(
                        matched_item, service_name=service_name,
                    )

            # ── Fallback: word-boundary name matching ────────────────────
            log.debug(
                "hash not in %s history, falling back to name matching",
                service_name,
            )
            response = httpx.get(
                f"{base}{movie_endpoint}",
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            items = response.json()

            # Word-boundary matching with year scoring — prevents false
            # positives like "Ray" matching "BluRay" torrents
            best_match = _match_torrent_to_arr(torrent.name, items)

            if best_match:
                title = best_match.get("title", "")
                log.debug("name match: '%s' -> '%s'", torrent.name[:50], title)
                return self._extract_arr_metadata(
                    best_match, service_name=service_name,
                )
            else:
                log.warning(
                    "could not match torrent '%s' to any %s entry",
                    torrent.name, service_name,
                )
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            log.error("%s metadata lookup failed: %s", service_name, exc)
        except Exception as exc:
            log.error("unexpected error in metadata lookup: %s", exc)

        return None

    def _extract_arr_metadata(
        self, item: dict, *, service_name: Optional[str] = None,
    ) -> ArrMetadata:
        """Extract ArrMetadata from a Radarr movie or Sonarr series dict."""
        title = item.get("title", "")
        metadata = ArrMetadata()

        # Preserve the media ID so we can notify the *arr after remux
        media_id = item.get("id")
        if media_id is not None:
            metadata.media_id = media_id
            metadata.service_name = service_name

        # Extract library path from the *arr "path" field
        arr_path = item.get("path")
        if arr_path:
            metadata.library_path = Path(arr_path)
            log.debug("library path for '%s': %s", title, metadata.library_path)

        # Extract original language
        orig_lang = item.get("originalLanguage", {})
        lang_name = orig_lang.get("name", "")
        if lang_name:
            iso_code = arr_language_to_iso(lang_name)
            if iso_code and iso_code != "und":
                log.debug(
                    "original language for '%s': %s -> %s",
                    title, lang_name, iso_code,
                )
                metadata.original_language = iso_code
            else:
                log.warning(
                    "unrecognized original language '%s' for '%s'",
                    lang_name, title,
                )

        return metadata

    def _process_torrent(
        self,
        api: QbittorrentAPI,
        config: StackConfig,
        torrent: TorrentRecord,
    ) -> bool:
        """Process a completed torrent through the remux pipeline.

        Returns True if ALL files were processed successfully.
        """
        log.info("processing: %s (%s...)", torrent.name, torrent.hash[:8])
        files = api.list_files(torrent.hash)
        if not files:
            log.warning("no files for %s, skipping", torrent.name)
            self._mark_processed(torrent.hash, "skipped_no_files")
            return False

        download_path = torrent.save_path
        full_paths = [download_path / file for file in files]
        info = TorrentInfo(
            hash=torrent.hash,
            name=torrent.name,
            category=torrent.category,
            download_path=download_path,
            files=full_paths,
        )

        # Check for ISO files — mount or extract if found
        iso_dir: Optional[Path] = None
        iso_file = self._find_iso_file(full_paths)
        if iso_file:
            try:
                iso_dir = self._open_iso(iso_file, torrent.hash)
            except (RuntimeError, OSError) as exc:
                log.error("ISO open failed for %s: %s", torrent.name, exc)
                self._mark_processed(torrent.hash, "plan_failed", f"ISO: {exc}")
                return False

        try:
            return self._execute_pipeline(
                api, config, torrent, info, iso_mount_dir=iso_dir,
            )
        finally:
            if iso_dir:
                self._close_iso(iso_dir)

    def _execute_pipeline(
        self,
        api: QbittorrentAPI,
        config: StackConfig,
        torrent: TorrentRecord,
        info: TorrentInfo,
        *,
        iso_mount_dir: Optional[Path] = None,
    ) -> bool:
        """Run the remux pipeline for a torrent.

        Separated from ``_process_torrent()`` so that ISO mount/unmount
        can wrap this entire block in a ``try/finally``.
        """
        # Look up metadata from Radarr/Sonarr (original language + library path)
        metadata = self._lookup_arr_metadata(config, torrent)

        # Extract preferred audio languages for post-remux validation
        keep_audio_langs = set(config.media_policy.movies.keep_audio)

        worker = PipelineWorker(config)
        try:
            plans = worker.build_plans(
                info,
                original_language=metadata.original_language if metadata else None,
                library_path=metadata.library_path if metadata else None,
                iso_mount_dir=iso_mount_dir,
            )
        except ValueError as exc:
            log.error("plan failed for %s: %s", torrent.name, exc)
            # Task 1d: Clean up source files for torrents that can never be
            # processed (e.g. no video files found).  These will just waste
            # scratch space forever since they'll never succeed on retry.
            self._cleanup_path(torrent.content_path)
            self._cleanup_empty_parent(torrent.content_path)
            api.remove_torrents([torrent.hash])
            self._mark_processed(torrent.hash, "plan_failed", str(exc))
            return False

        total = len(plans)
        succeeded = 0
        failed = 0
        succeeded_plans: list = []

        for i, plan in enumerate(plans, 1):
            log.info(
                "  [%d/%d] remuxing: %s -> %s",
                i, total, plan.source.name, plan.final_output.name,
            )
            success = self._run_ffmpeg(plan.ffmpeg_command, source=plan.source)
            if not success:
                log.error("  [%d/%d] ffmpeg FAILED for %s", i, total, plan.source.name)
                # Clean up any partial output to free disk space immediately
                if plan.staging_output.exists():
                    try:
                        plan.staging_output.unlink()
                        log.info("  cleaned up partial output: %s", plan.staging_output.name)
                    except OSError:
                        pass
                failed += 1
                continue

            # --- SAFETY: Validate output before moving to library ---
            if not self._validate_output(
                plan.staging_output, plan.source,
                keep_audio_langs=keep_audio_langs,
            ):
                log.error(
                    "  [%d/%d] REJECTED: output failed validation for %s",
                    i, total, plan.source.name,
                )
                failed += 1
                continue

            # --- SAFETY: Never overwrite a valid existing library file ---
            # If a file already exists at the target path and is larger than
            # our new output, refuse to overwrite.  This prevents the cascade
            # where a corrupt small file replaces a valid large one.
            plan.final_output.parent.mkdir(parents=True, exist_ok=True)
            if plan.final_output.exists():
                existing_size = plan.final_output.stat().st_size
                new_size = plan.staging_output.stat().st_size
                if existing_size > new_size:
                    log.warning(
                        "  [%d/%d] REFUSED to overwrite existing %s "
                        "(%.2f GB) with smaller file (%.2f GB)",
                        i, total, plan.final_output.name,
                        existing_size / (1024**3), new_size / (1024**3),
                    )
                    try:
                        plan.staging_output.unlink()
                    except OSError:
                        pass
                    failed += 1
                    continue
                else:
                    log.info(
                        "  [%d/%d] replacing existing %s (%.2f GB -> %.2f GB)",
                        i, total, plan.final_output.name,
                        existing_size / (1024**3), new_size / (1024**3),
                    )

            shutil.move(str(plan.staging_output), str(plan.final_output))
            log.info("  [%d/%d] moved to %s", i, total, plan.final_output)
            succeeded += 1
            succeeded_plans.append(plan)

        # Clean up any leftover .tmp_ staging files (e.g. from failed plans
        # where the unlink in the failure path also failed).  Also cleans up
        # concat list files from BDMV processing.
        if plans:
            cleaned_dirs: set = set()
            for plan in plans:
                if plan.staging_output.exists():
                    try:
                        plan.staging_output.unlink()
                    except OSError:
                        pass
                cleaned_dirs.add(plan.staging_output.parent)
            # Remove any .tmp_ concat files left behind
            for d in cleaned_dirs:
                for tmp in d.glob(".tmp_*_concat.txt"):
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

        if failed == 0:
            # All files processed successfully — clean up source files
            self._cleanup_path(torrent.content_path)
            # Task 1a: Also try to remove the parent directory if it's now
            # empty.  Torrent content_path is often a file inside a directory
            # (e.g. /downloads/complete/movies/TorrentName/file.mkv), and
            # removing the file leaves the TorrentName/ directory behind.
            self._cleanup_empty_parent(torrent.content_path)
            api.remove_torrents([torrent.hash])
            self._mark_processed(
                torrent.hash, "ok",
                f"{succeeded}/{total} files processed"
            )
            # Notify Radarr/Sonarr about this specific item so it discovers
            # the file immediately instead of waiting for a bulk library scan.
            if metadata and metadata.media_id and metadata.service_name:
                self._refresh_arr_item(config, metadata)
            log.info(
                "completed: %s (%d/%d files)",
                torrent.name, succeeded, total,
            )
            return True
        elif succeeded > 0:
            # Task 1b: Partial success — clean up the source files that DID
            # succeed (to reclaim scratch space), leave failures for debug.
            for plan in succeeded_plans:
                self._cleanup_path(plan.source)
            # Still notify — the files that succeeded are in the library
            if metadata and metadata.media_id and metadata.service_name:
                self._refresh_arr_item(config, metadata)
            self._mark_processed(
                torrent.hash, "partial",
                f"{succeeded}/{total} succeeded, {failed}/{total} failed"
            )
            log.warning(
                "partial: %s (%d/%d ok, %d/%d failed)",
                torrent.name, succeeded, total, failed, total,
            )
            return True  # still trigger refresh for the files that did succeed
        else:
            # Total failure
            self._mark_processed(
                torrent.hash, "ffmpeg_failed",
                f"all {total} files failed"
            )
            log.error("FAILED: %s (all %d files failed)", torrent.name, total)
            return False

    def _compute_ffmpeg_timeout(self, source: Path) -> int:
        """Compute an ffmpeg timeout proportional to source file size.

        Base: 2 hours, plus 1 hour per 25 GB of source.  Large BDMVs
        (70+ GB) need several hours for copy-mux.  Capped at 8 hours.
        """
        try:
            size_gb = source.stat().st_size / (1024 ** 3)
        except OSError:
            size_gb = 0
        timeout_secs = int(7200 + (size_gb / 25) * 3600)
        return min(timeout_secs, 8 * 3600)

    def _run_ffmpeg(self, command: List[str], *, source: Optional[Path] = None) -> bool:
        timeout = self._compute_ffmpeg_timeout(source) if source else 3600
        timeout_hours = timeout / 3600
        try:
            proc = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
        except OSError as exc:
            log.error("ffmpeg failed to start: %s", exc)
            return False
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            log.error("ffmpeg timed out after %.1f hours — sending SIGKILL", timeout_hours)
            proc.kill()
            proc.communicate()  # Reap zombie process
            return False
        if proc.returncode != 0:
            stderr_stripped = stderr.strip()
            # Only log last few lines of stderr to avoid flooding
            lines = stderr_stripped.split("\n")
            tail = "\n".join(lines[-5:]) if len(lines) > 5 else stderr_stripped
            log.error("ffmpeg error (exit %d): %s", proc.returncode, tail)
            return False
        return True

    def _validate_output(
        self, staging_output: Path, source: Path,
        *, keep_audio_langs: Optional[set] = None,
    ) -> bool:
        """Validate a remuxed file before moving it to the library.

        Checks:
        1. File exists and is at least 1 MB (rejects corrupt stubs).
        2. ffprobe confirms video + audio streams are present.
        3. Duration is at least 1 minute (rejects menu fragments).
        4. Output is at least 1% of source size (rejects near-empty files).
        5. Language audit: warns if no audio matches preferred languages.

        Returns True if the output looks valid, False otherwise.
        """
        if not staging_output.exists():
            log.error("VALIDATION FAILED: output file does not exist: %s", staging_output)
            return False

        output_size = staging_output.stat().st_size
        min_size = 1024 * 1024  # 1 MB absolute minimum

        if output_size < min_size:
            log.error(
                "VALIDATION FAILED: output is only %s bytes (< 1 MB) — likely corrupt stub",
                f"{output_size:,}",
            )
            # Clean up the bad output
            try:
                staging_output.unlink()
            except OSError:
                pass
            return False

        # Check output is at least 1% of source size (copy-mux should be
        # close to source size minus stripped tracks, never tiny)
        try:
            source_size = source.stat().st_size
            if source_size > 0 and output_size < source_size * 0.01:
                log.error(
                    "VALIDATION FAILED: output (%.2f GB) is < 1%% of source "
                    "(%.2f GB) — likely corrupt",
                    output_size / (1024**3), source_size / (1024**3),
                )
                try:
                    staging_output.unlink()
                except OSError:
                    pass
                return False
        except OSError:
            pass  # Source may already be gone in some edge cases

        # Quick ffprobe to verify streams and duration
        try:
            probe = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-print_format", "json",
                    "-show_format", "-show_streams",
                    str(staging_output),
                ],
                capture_output=True, text=True, timeout=30,
            )
            if probe.returncode != 0:
                log.error("VALIDATION FAILED: ffprobe failed on output — cannot verify file integrity")
                return False  # If we can't verify the output, reject it

            data = json.loads(probe.stdout)
            fmt = data.get("format", {})
            duration = float(fmt.get("duration", 0))
            streams = data.get("streams", [])

            has_video = any(s.get("codec_type") == "video" for s in streams)
            has_audio = any(s.get("codec_type") == "audio" for s in streams)

            if not has_video:
                log.error("VALIDATION FAILED: output has no video stream")
                try:
                    staging_output.unlink()
                except OSError:
                    pass
                return False

            if not has_audio:
                log.error("VALIDATION FAILED: output has no audio stream")
                try:
                    staging_output.unlink()
                except OSError:
                    pass
                return False

            if duration < 60:
                log.error(
                    "VALIDATION FAILED: output duration is %.1fs (< 1 min) — likely menu fragment",
                    duration,
                )
                try:
                    staging_output.unlink()
                except OSError:
                    pass
                return False

            # Language audit: warn if no audio matches preferred languages.
            # Warning-only — legitimate foreign films may only have original
            # language audio, and hard rejection would cause false positives.
            if keep_audio_langs:
                from .remux import _normalize_lang
                audio_langs = set()
                for s in streams:
                    if s.get("codec_type") == "audio":
                        lang = _normalize_lang(
                            s.get("tags", {}).get("language", "und")
                        )
                        audio_langs.add(lang)

                preferred = {_normalize_lang(l) for l in keep_audio_langs if l.lower() != "und"}
                actual = {l for l in audio_langs if l != "und"}

                if preferred and actual and not preferred.intersection(actual):
                    log.warning(
                        "LANGUAGE WARNING: output audio is [%s] but preferred "
                        "languages are [%s]. File may not have usable audio.",
                        ", ".join(sorted(actual)), ", ".join(sorted(preferred)),
                    )

            log.info(
                "validation OK: %.2f GB, %.1f min, %s + %d audio",
                output_size / (1024**3), duration / 60,
                "video" if has_video else "NO VIDEO",
                sum(1 for s in streams if s.get("codec_type") == "audio"),
            )
            return True

        except Exception as exc:
            log.error("VALIDATION FAILED: probe error (%s) — cannot verify file integrity", exc)
            return False  # If we can't verify the output, reject it

    # ------------------------------------------------------------------
    # ISO handling helpers
    # ------------------------------------------------------------------

    def _find_iso_file(self, files: List[Path]) -> Optional[Path]:
        """Find the first .iso file in a torrent's file list."""
        for f in files:
            if f.suffix.lower() == ".iso" and f.exists():
                return f
        return None

    def _open_iso(self, iso_path: Path, torrent_hash: str) -> Path:
        """Open a Blu-ray ISO image and return a directory with its contents.

        Strategy:
        1. Try ``mount -o loop,ro`` (zero-copy, works for UDF Blu-ray ISOs).
           Requires ``CAP_SYS_ADMIN`` and access to ``/dev/loop-control``.
        2. Fall back to ``7z x`` extraction (works for ISO 9660 images
           without special privileges, but not UDF).

        The returned directory is cleaned up by ``_close_iso()`` in the
        finally block of ``_process_torrent()``.
        """
        iso_size_gb = iso_path.stat().st_size / (1024 ** 3)

        # --- Strategy 1: loopback mount (preferred for UDF Blu-rays) ---
        mount_dir = Path(f"/tmp/iso_{torrent_hash}")
        mount_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["mount", "-o", "loop,ro", str(iso_path), str(mount_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            log.info(
                "mounted ISO: %s (%.1f GB) -> %s",
                iso_path.name, iso_size_gb, mount_dir,
            )
            return mount_dir

        # Mount failed — clean up empty mount point
        mount_err = result.stderr.strip()
        try:
            mount_dir.rmdir()
        except OSError:
            pass
        log.warning("mount failed (%s), trying 7z extraction...", mount_err)

        # --- Strategy 2: 7z extraction (ISO 9660 fallback) ---
        extract_dir = Path(f"/downloads/iso_extract_{torrent_hash}")
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True, exist_ok=True)

        log.info(
            "extracting ISO: %s (%.1f GB) -> %s",
            iso_path.name, iso_size_gb, extract_dir,
        )

        result = subprocess.run(
            [
                "7z", "x",
                f"-o{extract_dir}",  # output directory
                "-y",                # yes to all prompts
                "-bd",               # disable progress indicator
                str(iso_path),
            ],
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hour — large ISOs can take a while
        )
        if result.returncode != 0:
            try:
                shutil.rmtree(extract_dir)
            except OSError:
                pass
            raise RuntimeError(
                f"Failed to open ISO {iso_path.name}: "
                f"mount failed ({mount_err}), "
                f"7z failed ({result.stderr.strip() or result.stdout.strip()})"
            )

        log.info("extracted ISO: %s -> %s", iso_path.name, extract_dir)
        return extract_dir

    def _close_iso(self, iso_dir: Path) -> None:
        """Clean up an ISO mount point or extraction directory."""
        try:
            # Try unmount first (in case it was loop-mounted)
            result = subprocess.run(
                ["umount", str(iso_dir)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                iso_dir.rmdir()
                log.info("unmounted ISO: %s", iso_dir)
                return

            # Not a mount point — must be an extraction directory
            shutil.rmtree(iso_dir)
            log.info("cleaned up ISO extract: %s", iso_dir)
        except Exception as exc:
            log.warning("failed to clean up %s: %s", iso_dir, exc)

    def _cleanup_path(self, path: Path) -> None:
        """Remove a file or directory. Silently ignores missing paths."""
        if not str(path):
            return
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        except OSError as exc:
            log.warning("cleanup failed for %s: %s", path, exc)

    # Directories that must never be removed by cleanup.  These are the
    # structural directories that qBittorrent and the pipeline rely on.
    _PROTECTED_DIRS = frozenset({
        "complete", "incomplete", "movies", "tv",
        "downloads", "postproc", "transcode",
    })

    def _cleanup_empty_parent(self, path: Path) -> None:
        """Try to remove the parent directory if it's empty.

        This handles the common case where content_path is a file inside a
        torrent directory.  After removing the file, the directory is empty
        but won't be cleaned up unless we explicitly rmdir it.
        Walks up at most 2 levels to catch nested empty dirs, but never
        removes protected structural directories (complete/, movies/, tv/).
        """
        parent = path.parent
        for _ in range(2):
            if not parent.is_dir():
                break
            if parent.name in self._PROTECTED_DIRS:
                break  # Never delete structural directories
            try:
                # rmdir() only succeeds on empty directories
                parent.rmdir()
                log.info("removed empty directory: %s", parent)
                parent = parent.parent
            except OSError:
                break  # Not empty or permission error — stop

    # ------------------------------------------------------------------
    # Sonarr / Radarr library refresh
    # ------------------------------------------------------------------

    def _refresh_arr_item(
        self, config: StackConfig, metadata: ArrMetadata,
    ) -> None:
        """Notify Radarr/Sonarr about a specific movie/series after remux.

        Sends a targeted RefreshMovie or RefreshSeries command for the exact
        media item, so the *arr service rescans only that item's folder and
        links the newly placed file in its database immediately.

        Falls back silently on failure — the bulk rescan at end-of-cycle
        acts as a safety net.
        """
        service_name = metadata.service_name
        media_id = metadata.media_id
        if not service_name or media_id is None:
            return

        state = self.repo.load_state()
        api_key = state.get("secrets", {}).get(service_name, {}).get("api_key")
        if not api_key:
            return

        if service_name == "radarr":
            port, command = 7878, "RefreshMovie"
            id_field = "movieIds"
        elif service_name == "sonarr":
            port, command = 8989, "RefreshSeries"
            id_field = "seriesIds"
        else:
            return

        host = service_name
        if config.services.gluetun.enabled:
            from ..models import VPN_ROUTED_SERVICES
            if service_name in VPN_ROUTED_SERVICES:
                host = "gluetun"

        try:
            response = httpx.post(
                f"http://{host}:{port}/api/v3/command",
                json={"name": command, id_field: [media_id]},
                headers={"X-Api-Key": api_key},
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
            response.raise_for_status()
            log.info(
                "notified %s: %s(%s=[%s])",
                service_name, command, id_field, media_id,
            )
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            # Non-fatal — bulk rescan at end of cycle is the safety net
            log.warning(
                "%s per-item refresh failed (id=%s): %s",
                service_name, media_id, exc,
            )

    def _refresh_arr_services(
        self, config: StackConfig, categories: dict[str, bool]
    ) -> None:
        """Trigger Sonarr/Radarr disk rescan for categories that were processed.

        After the pipeline places remuxed files into the library directories,
        Sonarr/Radarr need to rescan to discover and import them.
        """
        state = self.repo.load_state()
        secrets_state = state.get("secrets", {})
        cat_config = config.download_policy.categories

        for raw_category in categories:
            normalized = self._normalize_category(raw_category)
            if normalized == cat_config.sonarr:
                self._trigger_arr_rescan(
                    "sonarr", 8989, "/api/v3",
                    secrets_state.get("sonarr", {}),
                    "RescanSeries",
                    config=config,
                )
            elif normalized == cat_config.radarr:
                self._trigger_arr_rescan(
                    "radarr", 7878, "/api/v3",
                    secrets_state.get("radarr", {}),
                    "RescanMovie",
                    config=config,
                )

    def _trigger_arr_rescan(
        self,
        service_name: str,
        internal_port: int,
        api_prefix: str,
        service_secrets: dict,
        command_name: str,
        config: Optional[StackConfig] = None,
    ) -> None:
        """Send a rescan command to a Sonarr/Radarr service."""
        api_key = service_secrets.get("api_key")
        if not api_key:
            log.debug("no API key for %s, skipping refresh", service_name)
            return

        # Radarr/Sonarr share gluetun's network namespace when VPN is active,
        # so they're reachable at gluetun:<port>, not <service_name>:<port>.
        host = service_name
        if config and config.services.gluetun.enabled:
            from ..models import VPN_ROUTED_SERVICES
            if service_name in VPN_ROUTED_SERVICES:
                host = "gluetun"
        base_url = f"http://{host}:{internal_port}{api_prefix}"
        try:
            response = httpx.post(
                f"{base_url}/command",
                json={"name": command_name},
                headers={"X-Api-Key": api_key},
                timeout=httpx.Timeout(10.0, connect=5.0),
            )
            response.raise_for_status()
            log.info("triggered %s on %s", command_name, service_name)
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            log.error("%s refresh failed: %s", service_name, exc)


def main() -> None:
    root = Path(os.getenv("ORCH_ROOT", str(Path(__file__).resolve().parents[2])))
    # Pipeline worker runs with read-only config access
    repo = ConfigRepository(root, read_only=True)
    interval = float(os.getenv("PIPELINE_INTERVAL", "60"))
    log.info("config root: %s", root)
    runner = PipelineRunner(repo)
    runner.run_forever(interval=interval)


if __name__ == "__main__":
    main()

