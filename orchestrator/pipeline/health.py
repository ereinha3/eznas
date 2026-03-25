"""Download health monitor — detects stalled torrents and triggers re-search.

Runs every few minutes (configurable) as part of the pipeline tick.  For each
downloading torrent it tracks byte progress over time.  When a torrent makes
zero progress for ``stall_threshold_minutes`` it is:

1. Removed from qBittorrent
2. Blocklisted in Sonarr/Radarr (so the same release isn't re-grabbed)
3. A new search is triggered (SeasonSearch preferred for Sonarr, MoviesSearch
   for Radarr)

This replaces the old passive waiting approach with aggressive, automatic
retry behaviour.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from ..models import BackfillConfig, StackConfig, VPN_ROUTED_SERVICES
from ..storage import ConfigRepository

log = logging.getLogger("pipeline")


class DownloadHealthMonitor:
    """Watches active downloads and handles stalls via blocklist + re-search."""

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    # ------------------------------------------------------------------
    # Public API — called every pipeline tick
    # ------------------------------------------------------------------

    def maybe_check(self, config: StackConfig) -> None:
        """Run a stall check if enough time has elapsed since the last one."""
        bf_cfg = config.services.pipeline.backfill
        if not bf_cfg.enabled or not bf_cfg.stall_detection_enabled:
            return

        state = self.repo.load_health_state()
        last_check = state.get("last_check", "")
        if last_check:
            try:
                last_dt = datetime.fromisoformat(last_check)
                elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if elapsed < bf_cfg.stall_check_interval_minutes * 60:
                    return
            except (ValueError, TypeError):
                pass

        try:
            self._run_check(config, bf_cfg, state)
        finally:
            state["last_check"] = datetime.now(timezone.utc).isoformat()
            self.repo.save_health_state(state)

    # ------------------------------------------------------------------
    # Core check
    # ------------------------------------------------------------------

    def _run_check(
        self, config: StackConfig, bf_cfg: BackfillConfig, state: dict
    ) -> None:
        """Check all downloading torrents for stalls."""
        secrets = self.repo.load_state().get("secrets", {})
        qb_creds = secrets.get("qbittorrent", {})
        sonarr_key = secrets.get("sonarr", {}).get("api_key")
        radarr_key = secrets.get("radarr", {}).get("api_key")

        vpn = config.services.gluetun.enabled
        qb_host = "gluetun" if vpn else "qbittorrent"
        sonarr_host = "gluetun" if vpn and "sonarr" in VPN_ROUTED_SERVICES else "sonarr"
        radarr_host = "gluetun" if vpn and "radarr" in VPN_ROUTED_SERVICES else "radarr"

        timeout = httpx.Timeout(30.0, connect=10.0)

        # 1. Get all torrents from qBittorrent
        try:
            torrents = self._get_qbt_torrents(
                qb_host, qb_creds, config.services.qbittorrent,
            )
        except Exception as exc:
            log.debug("health: qBittorrent unavailable: %s", exc)
            return

        # Filter to only downloading torrents
        downloading = [
            t for t in torrents
            if t.get("state") in (
                "downloading", "stalledDL", "metaDL", "forcedDL",
                "forcedMetaDL", "queuedDL",
            )
        ]

        if not downloading:
            # Clean up old progress snapshots
            state.pop("torrent_progress", None)
            return

        # 2. Check for stalls by comparing to previous snapshot
        progress = state.setdefault("torrent_progress", {})
        stall_actions = state.setdefault("stall_actions", {})
        now = int(time.time())
        stall_threshold_s = bf_cfg.stall_threshold_minutes * 60
        stalled: list[tuple[str, str]] = []  # (hash, category)
        cat_config = config.download_policy.categories

        for t in downloading:
            h = t.get("hash", "")
            category = t.get("category", "")
            downloaded = t.get("downloaded", 0)
            prev = progress.get(h)

            if prev is None:
                # First time seeing this torrent — record baseline
                progress[h] = {
                    "downloaded": downloaded,
                    "checked_at": now,
                    "stall_count": 0,
                }
                continue

            prev_downloaded = prev.get("downloaded", 0)
            prev_checked = prev.get("checked_at", now)

            if downloaded > prev_downloaded:
                # Making progress — reset
                progress[h] = {
                    "downloaded": downloaded,
                    "checked_at": now,
                    "stall_count": 0,
                }
                continue

            # No progress since last check
            stall_duration = now - prev_checked
            stall_count = prev.get("stall_count", 0) + 1
            progress[h] = {
                "downloaded": downloaded,
                "checked_at": prev_checked,  # keep original stall start
                "stall_count": stall_count,
            }

            num_seeds = t.get("num_seeds", 0)
            is_stalled_state = t.get("state") in ("stalledDL", "metaDL")

            # Immediate kill: 0 seeds + stalled state = dead torrent
            if num_seeds == 0 and is_stalled_state and stall_count >= 2:
                name = t.get("name", h)[:60]
                log.info(
                    "health: DEAD torrent %s (0 seeds, state=%s, %d checks)",
                    name, t.get("state"), stall_count,
                )
                stalled.append((h, category))
                continue

            if stall_duration >= stall_threshold_s:
                name = t.get("name", h)[:60]
                log.info(
                    "health: STALLED %s (0 progress for %d min, %d bytes downloaded)",
                    name, stall_duration // 60, downloaded,
                )
                stalled.append((h, category))

        # 3. Handle stalled torrents
        if stalled and (sonarr_key or radarr_key):
            sonarr_queue = []
            radarr_queue = []

            if sonarr_key:
                try:
                    sonarr_queue = self._get_arr_queue(
                        sonarr_host, 8989, sonarr_key, timeout
                    )
                except Exception as exc:
                    log.warning("health: Sonarr queue fetch failed: %s", exc)

            if radarr_key:
                try:
                    radarr_queue = self._get_arr_queue(
                        radarr_host, 7878, radarr_key, timeout
                    )
                except Exception as exc:
                    log.warning("health: Radarr queue fetch failed: %s", exc)

            for h, torrent_category in stalled:
                # Check retry limit
                action_key = h.upper()
                action = stall_actions.get(action_key, {})
                retries = action.get("retries", 0)

                if retries >= bf_cfg.max_stall_retries:
                    log.warning(
                        "health: max retries (%d) reached for %s, giving up",
                        bf_cfg.max_stall_retries, h[:16],
                    )
                    # Remove from qBT but don't search again
                    self._remove_from_qbt(
                        qb_host, qb_creds, config.services.qbittorrent, h
                    )
                    progress.pop(h, None)
                    continue

                # Exponential backoff: don't re-search too quickly after
                # repeated failures.  Still remove the dead torrent from qBT.
                next_search_after = action.get("next_search_after", 0)
                if now < next_search_after:
                    log.info(
                        "health: backoff active for %s, removing dead torrent but "
                        "deferring re-search (%d min left)",
                        h[:16], (next_search_after - now) // 60,
                    )
                    self._remove_from_qbt(
                        qb_host, qb_creds, config.services.qbittorrent, h
                    )
                    progress.pop(h, None)
                    continue

                # Category-aware: check the matching service FIRST based
                # on the torrent's qBT category, then fall back to the
                # other service.  This prevents misrouted blocklisting.
                normalized_cat = torrent_category
                for suffix in ("-sonarr", "-radarr"):
                    if torrent_category.endswith(suffix):
                        normalized_cat = torrent_category[: -len(suffix)]
                        break

                if normalized_cat == cat_config.sonarr:
                    primary = ("sonarr", sonarr_queue, sonarr_host, 8989, sonarr_key)
                    secondary = ("radarr", radarr_queue, radarr_host, 7878, radarr_key)
                else:
                    primary = ("radarr", radarr_queue, radarr_host, 7878, radarr_key)
                    secondary = ("sonarr", sonarr_queue, sonarr_host, 8989, sonarr_key)

                handled = self._handle_stall_in_arr(
                    h, primary[1], primary[0], primary[2], primary[3],
                    primary[4], bf_cfg, timeout,
                )
                if not handled:
                    handled = self._handle_stall_in_arr(
                        h, secondary[1], secondary[0], secondary[2], secondary[3],
                        secondary[4], bf_cfg, timeout,
                    )

                if not handled:
                    log.info(
                        "health: removing orphan stalled torrent %s",
                        h[:16],
                    )
                    self._remove_from_qbt(
                        qb_host, qb_creds, config.services.qbittorrent, h
                    )

                # Compute exponential backoff for next retry
                backoff_minutes = [0, 30, 120]  # retry 0: immediate, 1: 30min, 2: 2hr
                backoff_idx = min(retries, len(backoff_minutes) - 1)
                next_allowed = now + backoff_minutes[backoff_idx] * 60

                # Record the action
                stall_actions[action_key] = {
                    "retries": retries + 1,
                    "last_action": now,
                    "next_search_after": next_allowed,
                }

                # Clean up progress tracking for removed torrent
                progress.pop(h, None)

        # 4. Cleanup: remove progress entries for hashes no longer in qBT
        active_hashes = {t.get("hash", "") for t in torrents}
        for h in list(progress.keys()):
            if h not in active_hashes:
                del progress[h]

        # Cleanup: remove stall_actions older than 7 days
        for key in list(stall_actions.keys()):
            entry = stall_actions[key]
            if isinstance(entry, dict) and now - entry.get("last_action", 0) > 7 * 86400:
                del stall_actions[key]

    # ------------------------------------------------------------------
    # Arr queue + blocklist + search
    # ------------------------------------------------------------------

    def _handle_stall_in_arr(
        self,
        torrent_hash: str,
        queue: list[dict],
        service: str,  # "sonarr" or "radarr"
        host: str,
        port: int,
        api_key: Optional[str],
        bf_cfg: BackfillConfig,
        timeout: httpx.Timeout,
    ) -> bool:
        """Find a stalled torrent in an arr queue, blocklist it, and re-search.

        Returns True if the torrent was found and handled.
        """
        if not api_key:
            return False

        # Sonarr/Radarr store downloadId as uppercase hash
        hash_upper = torrent_hash.upper()
        queue_record = None
        for record in queue:
            if record.get("downloadId", "").upper() == hash_upper:
                queue_record = record
                break

        if not queue_record:
            return False

        queue_id = queue_record.get("id")
        title = queue_record.get("title", "unknown")[:60]

        log.info(
            "health: blocklisting stalled %s download: %s (queue id=%s)",
            service, title, queue_id,
        )

        # Blocklist and remove from download client
        try:
            headers = {"X-Api-Key": api_key}
            resp = httpx.delete(
                f"http://{host}:{port}/api/v3/queue/{queue_id}",
                params={"removeFromClient": "true", "blocklist": "true"},
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
        except Exception as exc:
            log.warning("health: failed to blocklist in %s: %s", service, exc)
            return False

        # Trigger re-search
        try:
            self._trigger_search(
                queue_record, service, host, port, api_key, bf_cfg, timeout,
            )
        except Exception as exc:
            log.warning("health: failed to trigger %s search: %s", service, exc)

        return True

    def _trigger_search(
        self,
        queue_record: dict,
        service: str,
        host: str,
        port: int,
        api_key: str,
        bf_cfg: BackfillConfig,
        timeout: httpx.Timeout,
    ) -> None:
        """Trigger a search in Sonarr/Radarr after blocklisting."""
        headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

        if service == "sonarr":
            series_id = queue_record.get("seriesId")
            season_number = queue_record.get("seasonNumber")
            episode_id = queue_record.get("episodeId")

            if series_id and season_number is not None and bf_cfg.prefer_season_packs:
                # Prefer season-level search
                log.info(
                    "health: triggering Sonarr SeasonSearch (series=%s, season=%s)",
                    series_id, season_number,
                )
                httpx.post(
                    f"http://{host}:{port}/api/v3/command",
                    json={
                        "name": "SeasonSearch",
                        "seriesId": series_id,
                        "seasonNumber": season_number,
                    },
                    headers=headers,
                    timeout=timeout,
                )
            elif episode_id:
                # Fall back to episode-level search
                log.info(
                    "health: triggering Sonarr EpisodeSearch (episodeIds=[%s])",
                    episode_id,
                )
                httpx.post(
                    f"http://{host}:{port}/api/v3/command",
                    json={
                        "name": "EpisodeSearch",
                        "episodeIds": [episode_id],
                    },
                    headers=headers,
                    timeout=timeout,
                )
            elif series_id:
                log.info(
                    "health: triggering Sonarr SeriesSearch (seriesId=%s)",
                    series_id,
                )
                httpx.post(
                    f"http://{host}:{port}/api/v3/command",
                    json={"name": "SeriesSearch", "seriesId": series_id},
                    headers=headers,
                    timeout=timeout,
                )

        elif service == "radarr":
            movie_id = queue_record.get("movieId")
            if movie_id:
                log.info(
                    "health: triggering Radarr MoviesSearch (movieIds=[%s])",
                    movie_id,
                )
                httpx.post(
                    f"http://{host}:{port}/api/v3/command",
                    json={"name": "MoviesSearch", "movieIds": [movie_id]},
                    headers=headers,
                    timeout=timeout,
                )

    # ------------------------------------------------------------------
    # qBittorrent helpers
    # ------------------------------------------------------------------

    def _get_qbt_torrents(
        self, host: str, qb_creds: dict, qb_cfg: Any,
    ) -> list[dict]:
        """Fetch all torrents from qBittorrent."""
        client = httpx.Client(
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers={"Host": "localhost:8080"},
        )
        try:
            username = qb_creds.get("username") or qb_cfg.username
            password = qb_creds.get("password") or qb_cfg.password
            resp = client.post(
                f"http://{host}:8080/api/v2/auth/login",
                data={"username": username, "password": password},
            )
            if resp.text.strip() != "Ok.":
                return []
            resp = client.get(f"http://{host}:8080/api/v2/torrents/info")
            resp.raise_for_status()
            return resp.json() or []
        finally:
            client.close()

    def _remove_from_qbt(
        self, host: str, qb_creds: dict, qb_cfg: Any, torrent_hash: str,
    ) -> None:
        """Remove a torrent from qBittorrent."""
        client = httpx.Client(
            timeout=httpx.Timeout(20.0, connect=5.0),
            headers={"Host": "localhost:8080"},
        )
        try:
            username = qb_creds.get("username") or qb_cfg.username
            password = qb_creds.get("password") or qb_cfg.password
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
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Arr queue helper
    # ------------------------------------------------------------------

    def _get_arr_queue(
        self, host: str, port: int, api_key: str, timeout: httpx.Timeout,
    ) -> list[dict]:
        """Fetch the full download queue from a Sonarr/Radarr instance."""
        all_records: list[dict] = []
        page = 1
        while True:
            resp = httpx.get(
                f"http://{host}:{port}/api/v3/queue",
                params={"page": page, "pageSize": 200, "includeUnknownSeriesItems": "true"},
                headers={"X-Api-Key": api_key},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            records = data.get("records", [])
            all_records.extend(records)
            if len(all_records) >= data.get("totalRecords", 0):
                break
            page += 1
        return all_records
