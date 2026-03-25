"""Prowlarr direct-grab fallback — bypasses Sonarr/Radarr title matching.

When Sonarr/Radarr cannot match release titles back to the correct series
(e.g. "Unknown Series" rejections, alias conflicts between remakes), this
module searches Prowlarr directly and adds magnet URIs to qBittorrent.

Runs as Phase 5 of the pipeline tick, after the normal backfill engine.
Only acts on seasons/movies that have been stuck (0% completion, monitored)
for longer than ``prowlarr_fallback_min_age_hours``.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import BackfillConfig, StackConfig, VPN_ROUTED_SERVICES
from ..storage import ConfigRepository
from .backfill import (
    BackfillCandidate,
    ProwlarrResult,
    _score_result,
)

log = logging.getLogger("pipeline")


class ProwlarrDirectGrab:
    """Searches Prowlarr directly and adds magnets to qBittorrent.

    This is a last-resort fallback for content that Sonarr/Radarr refuse
    to grab because they can't match the release title.
    """

    _SEARCH_DELAY = 5  # seconds between Prowlarr queries
    _GRAB_COOLDOWN = 48 * 3600  # don't re-grab same key for 48h

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    # ------------------------------------------------------------------
    # Public API — called every pipeline tick
    # ------------------------------------------------------------------

    def maybe_run(self, config: StackConfig) -> None:
        """Run a fallback cycle if enough time has elapsed."""
        bf_cfg = config.services.pipeline.backfill
        if not bf_cfg.enabled or not bf_cfg.prowlarr_fallback_enabled:
            return

        state = self._load_state()
        last_run = state.get("last_run", "")
        if last_run:
            try:
                last_dt = datetime.fromisoformat(last_run)
                elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if elapsed < bf_cfg.prowlarr_fallback_interval_hours * 3600:
                    return
            except (ValueError, TypeError):
                pass

        log.info("prowlarr-fallback: starting cycle")
        try:
            self._run_cycle(config, bf_cfg, state)
        finally:
            state["last_run"] = datetime.now(timezone.utc).isoformat()
            self._save_state(state)

    # ------------------------------------------------------------------
    # Core cycle
    # ------------------------------------------------------------------

    def _run_cycle(
        self, config: StackConfig, bf_cfg: BackfillConfig, state: dict
    ) -> None:
        """Find stuck content in Sonarr/Radarr, search Prowlarr, add to qBT."""
        secrets = self.repo.load_state().get("secrets", {})
        sonarr_key = secrets.get("sonarr", {}).get("api_key")
        radarr_key = secrets.get("radarr", {}).get("api_key")
        prowlarr_key = secrets.get("prowlarr", {}).get("api_key")
        qb_creds = secrets.get("qbittorrent", {})

        if not prowlarr_key:
            log.debug("prowlarr-fallback: no Prowlarr API key, skipping")
            return

        vpn = config.services.gluetun.enabled
        sonarr_host = "gluetun" if vpn and "sonarr" in VPN_ROUTED_SERVICES else "sonarr"
        radarr_host = "gluetun" if vpn and "radarr" in VPN_ROUTED_SERVICES else "radarr"
        prowlarr_host = "gluetun" if vpn and "prowlarr" in VPN_ROUTED_SERVICES else "prowlarr"
        qb_host = "gluetun" if vpn else "qbittorrent"

        timeout = httpx.Timeout(30.0, connect=10.0)
        search_timeout = httpx.Timeout(180.0, connect=10.0)

        # Get active qBT torrent names to avoid duplicates
        qbt_names: set[str] = set()
        try:
            qbt_names = self._get_qbt_names(
                qb_host, qb_creds, config.services.qbittorrent
            )
        except Exception as exc:
            log.warning("prowlarr-fallback: qBittorrent check failed: %s", exc)

        grabs = 0
        searched = 0

        # --- Sonarr stuck seasons ---
        if sonarr_key:
            sonarr_grabs, sonarr_searched = self._process_sonarr(
                config, bf_cfg, state, sonarr_host, sonarr_key,
                prowlarr_host, prowlarr_key, qb_host, qb_creds,
                qbt_names, timeout, search_timeout,
            )
            grabs += sonarr_grabs
            searched += sonarr_searched

        # --- Radarr stuck movies ---
        if radarr_key:
            radarr_grabs, radarr_searched = self._process_radarr(
                config, bf_cfg, state, radarr_host, radarr_key,
                prowlarr_host, prowlarr_key, qb_host, qb_creds,
                qbt_names, timeout, search_timeout,
            )
            grabs += radarr_grabs
            searched += radarr_searched

        log.info(
            "prowlarr-fallback: cycle done — searched=%d, grabbed=%d",
            searched, grabs,
        )

    # ------------------------------------------------------------------
    # Sonarr: find stuck seasons
    # ------------------------------------------------------------------

    def _process_sonarr(
        self,
        config: StackConfig,
        bf_cfg: BackfillConfig,
        state: dict,
        sonarr_host: str,
        sonarr_key: str,
        prowlarr_host: str,
        prowlarr_key: str,
        qb_host: str,
        qb_creds: dict,
        qbt_names: set[str],
        timeout: httpx.Timeout,
        search_timeout: httpx.Timeout,
    ) -> tuple[int, int]:
        """Find Sonarr seasons stuck at 0% and direct-grab from Prowlarr."""
        headers = {"X-Api-Key": sonarr_key}
        grabs = 0
        searched = 0
        min_age_s = bf_cfg.prowlarr_fallback_min_age_hours * 3600

        # 1. Fetch all missing episodes
        try:
            missing_eps = self._fetch_sonarr_missing(sonarr_host, sonarr_key, timeout)
            series_map = self._fetch_sonarr_series(sonarr_host, sonarr_key, timeout)
        except Exception as exc:
            log.warning("prowlarr-fallback: Sonarr API error: %s", exc)
            return 0, 0

        if not missing_eps:
            return 0, 0

        # 2. Get Sonarr queue to see what's already downloading
        queued_seasons: set[str] = set()
        try:
            resp = httpx.get(
                f"http://{sonarr_host}:8989/api/v3/queue",
                params={"page": 1, "pageSize": 500},
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            for record in resp.json().get("records", []):
                sid = record.get("seriesId")
                season = record.get("seasonNumber")
                if sid is not None and season is not None:
                    queued_seasons.add(f"{sid}:{season}")
        except Exception:
            pass

        # 3. Group missing into season-level candidates
        candidates = self._build_sonarr_candidates(
            missing_eps, series_map, state, min_age_s
        )

        grabbed = state.setdefault("grabbed", {})

        for candidate in candidates:
            if grabs >= bf_cfg.max_grabs_per_cycle:
                break

            key = f"sonarr:{candidate.series_id}:{candidate.season_number}"

            # Skip if already grabbed recently
            if self._is_grabbed(grabbed, key, qbt_names):
                continue

            # Skip if in Sonarr queue
            queue_key = f"{candidate.series_id}:{candidate.season_number}"
            if queue_key in queued_seasons:
                continue

            # Rate limit
            if searched > 0:
                time.sleep(self._SEARCH_DELAY)

            searched += 1
            try:
                results = self._search_prowlarr(
                    prowlarr_host, prowlarr_key, candidate, search_timeout
                )
            except Exception as exc:
                log.warning(
                    "prowlarr-fallback: search failed for %s S%02d: %s",
                    candidate.series_title, candidate.season_number, exc,
                )
                continue

            if not results:
                log.info(
                    "prowlarr-fallback: no results for %s S%02d",
                    candidate.series_title, candidate.season_number,
                )
                continue

            # Score and pick best, excluding torrents already in qBT
            scored = [_score_result(r, candidate, bf_cfg) for r in results]
            scored = [
                s for s in scored
                if s.score > 0
                and s.result.title not in qbt_names
                and s.result.seeders >= bf_cfg.min_seeders
            ]
            scored.sort(key=lambda s: s.score, reverse=True)

            if not scored:
                log.info(
                    "prowlarr-fallback: %d results for %s S%02d, none viable",
                    len(results), candidate.series_title, candidate.season_number,
                )
                continue

            best = scored[0]
            log.info(
                "prowlarr-fallback: best for %s S%02d: %s (%.1f GB, %d seeders, score=%d)",
                candidate.series_title, candidate.season_number,
                best.result.title[:60],
                best.result.size / (1024**3),
                best.result.seeders,
                best.score,
            )

            category = config.download_policy.categories.sonarr
            ok = self._add_to_qbt(
                qb_host, qb_creds, config.services.qbittorrent,
                best.result, category,
            )
            if ok:
                grabs += 1
                grabbed[key] = {
                    "title": best.result.title[:80],
                    "timestamp": int(time.time()),
                }
                # Record metadata so the pipeline can match this torrent
                # back to the correct arr service without hash lookup.
                self._record_grab_metadata(
                    state, best.result.title,
                    service="sonarr",
                    media_id=candidate.series_id,
                    season=candidate.season_number,
                    category=category,
                    library_title=candidate.series_title,
                )
                log.info(
                    "prowlarr-fallback: GRABBED %s S%02d -> %s",
                    candidate.series_title, candidate.season_number,
                    best.result.title[:50],
                )

        return grabs, searched

    # ------------------------------------------------------------------
    # Radarr: find stuck movies
    # ------------------------------------------------------------------

    def _process_radarr(
        self,
        config: StackConfig,
        bf_cfg: BackfillConfig,
        state: dict,
        radarr_host: str,
        radarr_key: str,
        prowlarr_host: str,
        prowlarr_key: str,
        qb_host: str,
        qb_creds: dict,
        qbt_names: set[str],
        timeout: httpx.Timeout,
        search_timeout: httpx.Timeout,
    ) -> tuple[int, int]:
        """Find Radarr movies stuck at 0% and direct-grab from Prowlarr."""
        grabs = 0
        searched = 0
        min_age_s = bf_cfg.prowlarr_fallback_min_age_hours * 3600

        # 1. Fetch missing movies
        try:
            resp = httpx.get(
                f"http://{radarr_host}:7878/api/v3/movie",
                headers={"X-Api-Key": radarr_key},
                timeout=timeout,
            )
            resp.raise_for_status()
            movies = resp.json()
        except Exception as exc:
            log.warning("prowlarr-fallback: Radarr API error: %s", exc)
            return 0, 0

        # Filter to monitored, missing movies
        missing = [
            m for m in movies
            if m.get("monitored") and not m.get("hasFile")
        ]
        if not missing:
            return 0, 0

        # 2. Check Radarr queue
        queued_ids: set[int] = set()
        try:
            resp = httpx.get(
                f"http://{radarr_host}:7878/api/v3/queue",
                params={"page": 1, "pageSize": 500},
                headers={"X-Api-Key": radarr_key},
                timeout=timeout,
            )
            resp.raise_for_status()
            for record in resp.json().get("records", []):
                mid = record.get("movieId")
                if mid is not None:
                    queued_ids.add(mid)
        except Exception:
            pass

        # 3. Track first-seen and filter by age
        first_seen = state.setdefault("radarr_first_seen", {})
        now = int(time.time())
        grabbed = state.setdefault("grabbed", {})

        for movie in missing:
            if grabs >= bf_cfg.max_grabs_per_cycle:
                break

            movie_id = movie.get("id")
            title = movie.get("title", "Unknown")
            year = movie.get("year")
            key = f"radarr:{movie_id}"

            # Track first-seen
            mid_str = str(movie_id)
            if mid_str not in first_seen:
                first_seen[mid_str] = now
            seen_ts = first_seen[mid_str]

            # Must be stuck long enough
            if now - seen_ts < min_age_s:
                continue

            # Skip if grabbed recently
            if self._is_grabbed(grabbed, key, qbt_names):
                continue

            # Skip if in Radarr queue
            if movie_id in queued_ids:
                continue

            if searched > 0:
                time.sleep(self._SEARCH_DELAY)

            searched += 1

            # Build a synthetic candidate for scoring
            candidate = BackfillCandidate(
                series_id=movie_id,
                series_title=title,
                tvdb_id=None,
                season_number=1,
                missing_episodes=[1],
                total_episodes_in_season=1,
                series_type="standard",
                first_seen=seen_ts,
                year=year,
            )

            # Search Prowlarr
            queries = [f"{title} {year} 1080p" if year else f"{title} 1080p"]
            if year:
                queries.append(f"{title} {year}")

            results = self._search_prowlarr_raw(
                prowlarr_host, prowlarr_key, queries, search_timeout
            )
            if not results:
                log.info("prowlarr-fallback: no results for movie %s", title)
                continue

            # Score — reuse _score_result but season matching won't apply well,
            # so also filter manually by title overlap
            scored = [_score_result(r, candidate, bf_cfg) for r in results]
            # For movies, season match penalty is expected; boost any with score > -100
            scored = [
                s for s in scored
                if s.score > -100
                and s.result.title not in qbt_names
                and s.result.seeders >= bf_cfg.min_seeders
            ]
            scored.sort(key=lambda s: s.score, reverse=True)

            if not scored:
                log.info(
                    "prowlarr-fallback: %d results for movie %s, none viable",
                    len(results), title,
                )
                continue

            best = scored[0]
            log.info(
                "prowlarr-fallback: best for movie %s: %s (%.1f GB, %d seeders, score=%d)",
                title, best.result.title[:60],
                best.result.size / (1024**3),
                best.result.seeders,
                best.score,
            )

            category = config.download_policy.categories.radarr
            ok = self._add_to_qbt(
                qb_host, qb_creds, config.services.qbittorrent,
                best.result, category,
            )
            if ok:
                grabs += 1
                grabbed[key] = {
                    "title": best.result.title[:80],
                    "timestamp": int(time.time()),
                }
                # Record metadata so the pipeline can match this torrent
                # back to the correct arr service without hash lookup.
                self._record_grab_metadata(
                    state, best.result.title,
                    service="radarr",
                    media_id=movie_id,
                    season=None,
                    category=category,
                    library_title=title,
                )
                log.info(
                    "prowlarr-fallback: GRABBED movie %s -> %s",
                    title, best.result.title[:50],
                )

        # Clean up first_seen for movies no longer missing
        missing_ids = {str(m.get("id")) for m in missing}
        for mid_str in list(first_seen.keys()):
            if mid_str not in missing_ids:
                del first_seen[mid_str]

        return grabs, searched

    # ------------------------------------------------------------------
    # Sonarr helpers
    # ------------------------------------------------------------------

    def _fetch_sonarr_missing(
        self, host: str, api_key: str, timeout: httpx.Timeout
    ) -> list[dict]:
        """Fetch all missing monitored episodes from Sonarr."""
        headers = {"X-Api-Key": api_key}
        all_episodes: list[dict] = []
        page = 1
        while True:
            resp = httpx.get(
                f"http://{host}:8989/api/v3/wanted/missing",
                params={
                    "page": page,
                    "pageSize": 500,
                    "sortKey": "airDateUtc",
                    "sortDirection": "ascending",
                    "includeSeries": "false",
                },
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            records = data.get("records", [])
            all_episodes.extend(records)
            if len(all_episodes) >= data.get("totalRecords", 0):
                break
            page += 1
        return all_episodes

    def _fetch_sonarr_series(
        self, host: str, api_key: str, timeout: httpx.Timeout
    ) -> dict[int, dict]:
        """Fetch all series -> {id: series}."""
        resp = httpx.get(
            f"http://{host}:8989/api/v3/series",
            headers={"X-Api-Key": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        return {s["id"]: s for s in resp.json()}

    def _build_sonarr_candidates(
        self,
        missing_eps: list[dict],
        series_map: dict[int, dict],
        state: dict,
        min_age_s: int,
    ) -> list[BackfillCandidate]:
        """Build candidates from missing episodes that have been stuck long enough."""
        now = int(time.time())
        first_seen = state.setdefault("sonarr_first_seen", {})

        # Group by (seriesId, season)
        groups: dict[str, list[dict]] = {}
        for ep in missing_eps:
            sid = ep.get("seriesId")
            season = ep.get("seasonNumber", 0)
            if season == 0:  # skip specials
                continue
            air_date = ep.get("airDateUtc", "")
            if air_date and air_date > datetime.now(timezone.utc).isoformat():
                continue
            key = f"{sid}:{season}"
            groups.setdefault(key, []).append(ep)

        # Track first_seen
        for key in groups:
            if key not in first_seen:
                first_seen[key] = now

        # Clean up resolved keys
        for key in list(first_seen.keys()):
            if key not in groups:
                del first_seen[key]

        candidates: list[BackfillCandidate] = []
        for key, eps in groups.items():
            seen_ts = first_seen.get(key, now)
            if now - seen_ts < min_age_s:
                continue

            sid_str, season_str = key.split(":")
            sid = int(sid_str)
            season = int(season_str)

            series = series_map.get(sid)
            if not series:
                continue

            total_in_season = 0
            for s_info in series.get("seasons", []):
                if s_info.get("seasonNumber") == season:
                    total_in_season = s_info.get("statistics", {}).get(
                        "totalEpisodeCount", 0
                    )
                    break

            if total_in_season == 0:
                continue

            alt_titles = [
                alt.get("title", "")
                for alt in series.get("alternateTitles", [])
                if alt.get("title")
            ]

            candidates.append(BackfillCandidate(
                series_id=sid,
                series_title=series.get("title", "Unknown"),
                tvdb_id=series.get("tvdbId"),
                season_number=season,
                missing_episodes=[ep.get("episodeNumber", 0) for ep in eps],
                total_episodes_in_season=total_in_season,
                series_type=series.get("seriesType", "standard"),
                first_seen=seen_ts,
                year=series.get("year"),
                alternate_titles=alt_titles,
            ))

        # Sort: most missing first
        candidates.sort(key=lambda c: (-len(c.missing_episodes), c.first_seen))
        return candidates

    # ------------------------------------------------------------------
    # Prowlarr search
    # ------------------------------------------------------------------

    def _search_prowlarr(
        self,
        host: str,
        api_key: str,
        candidate: BackfillCandidate,
        timeout: httpx.Timeout,
    ) -> list[ProwlarrResult]:
        """Search Prowlarr for a candidate season."""
        title = candidate.series_title
        season = candidate.season_number
        year = candidate.year
        year_tag = f" {year}" if year else ""

        queries = []
        if candidate.series_type == "anime":
            queries.append(f"{title}{year_tag} S{season:02d}")
            queries.append(f"{title} batch")
            for alt in candidate.alternate_titles[:1]:
                queries.append(f"{alt} S{season:02d}")
        else:
            queries.append(f"{title}{year_tag} S{season:02d}")
            queries.append(f"{title} S{season:02d}")

        return self._search_prowlarr_raw(host, api_key, queries, timeout)

    def _search_prowlarr_raw(
        self,
        host: str,
        api_key: str,
        queries: list[str],
        timeout: httpx.Timeout,
    ) -> list[ProwlarrResult]:
        """Run multiple Prowlarr search queries and return deduplicated results."""
        headers = {"X-Api-Key": api_key}
        all_results: list[ProwlarrResult] = []

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

                    # The guid field often contains the real magnet URI;
                    # the magnetUrl field is frequently a Prowlarr redirect
                    # that fails behind VPN.  Prefer guid when it's a magnet.
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
                log.warning("prowlarr-fallback: search timed out for: %s", query)
            except Exception as exc:
                log.warning("prowlarr-fallback: search error for '%s': %s", query, exc)

        # Deduplicate by guid
        seen: set[str] = set()
        unique: list[ProwlarrResult] = []
        for r in all_results:
            if r.guid not in seen:
                seen.add(r.guid)
                unique.append(r)

        return unique

    # ------------------------------------------------------------------
    # qBittorrent
    # ------------------------------------------------------------------

    def _get_qbt_names(
        self, host: str, qb_creds: dict, qb_cfg: Any
    ) -> set[str]:
        """Get names of all torrents in qBittorrent."""
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
                return set()
            resp = client.get(f"http://{host}:8080/api/v2/torrents/info")
            resp.raise_for_status()
            return {t.get("name", "") for t in resp.json() or []}
        finally:
            client.close()

    def _add_to_qbt(
        self,
        host: str,
        qb_creds: dict,
        qb_cfg: Any,
        result: ProwlarrResult,
        category: str,
    ) -> bool:
        """Add a torrent to qBittorrent, preferring magnet URIs."""
        client = httpx.Client(
            timeout=httpx.Timeout(30.0, connect=10.0),
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
                log.error("prowlarr-fallback: qBittorrent auth failed")
                return False

            # Resolve category save path so qBT places files correctly.
            try:
                cats_resp = client.get(f"http://{host}:8080/api/v2/torrents/categories")
                cat_info = cats_resp.json().get(category, {})
                savepath = cat_info.get("savePath", "")
            except Exception:
                savepath = ""

            add_data = {"category": category}
            if savepath:
                add_data["savepath"] = savepath

            # 1. Try real magnet URI (extracted from guid or magnetUrl)
            if result.magnet_url:
                resp = client.post(
                    f"http://{host}:8080/api/v2/torrents/add",
                    data={**add_data, "urls": result.magnet_url},
                )
                if resp.status_code == 200:
                    return True

            # 2. Try download URL
            if result.download_url:
                if result.download_url.startswith("magnet:"):
                    resp = client.post(
                        f"http://{host}:8080/api/v2/torrents/add",
                        data={**add_data, "urls": result.download_url},
                    )
                    return resp.status_code == 200

                # Download .torrent file
                try:
                    dl_resp = httpx.get(
                        result.download_url,
                        timeout=30,
                        follow_redirects=False,
                    )
                    if dl_resp.status_code in (301, 302, 303, 307, 308):
                        location = dl_resp.headers.get("location", "")
                        if location.startswith("magnet:"):
                            resp = client.post(
                                f"http://{host}:8080/api/v2/torrents/add",
                                data={**add_data, "urls": location},
                            )
                            return resp.status_code == 200

                    if dl_resp.status_code == 200 and len(dl_resp.content) > 100:
                        resp = client.post(
                            f"http://{host}:8080/api/v2/torrents/add",
                            data=add_data,
                            files={
                                "torrents": (
                                    "fallback.torrent",
                                    dl_resp.content,
                                    "application/x-bittorrent",
                                )
                            },
                        )
                        return resp.status_code == 200
                except Exception as exc:
                    log.warning("prowlarr-fallback: download URL fetch failed: %s", exc)

            return False
        finally:
            client.close()

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _record_grab_metadata(
        self,
        state: dict,
        torrent_title: str,
        service: str,
        media_id: int,
        season: int | None,
        category: str,
        library_title: str,
    ) -> None:
        """Store metadata for a fallback grab so the pipeline can match it
        back to the correct arr service without relying on hash lookup.

        The pipeline's ``_lookup_arr_metadata()`` checks this mapping before
        falling back to fragile name matching.
        """
        grab_meta = state.setdefault("grab_metadata", {})
        # Key by torrent title (what qBT will show as the torrent name)
        grab_meta[torrent_title[:120]] = {
            "service": service,          # "sonarr" or "radarr"
            "media_id": media_id,        # seriesId or movieId
            "season": season,            # season number (None for movies)
            "category": category,        # "tv" or "movies"
            "library_title": library_title,  # clean title for path building
            "timestamp": int(time.time()),
        }
        # Cap entries to prevent unbounded growth
        if len(grab_meta) > 200:
            oldest = sorted(grab_meta.items(), key=lambda x: x[1].get("timestamp", 0))
            for key, _ in oldest[:50]:
                del grab_meta[key]

    def _load_state(self) -> dict:
        return self.repo.load_fallback_state()

    def _save_state(self, state: dict) -> None:
        self.repo.save_fallback_state(state)

    def _is_grabbed(self, grabbed: dict, key: str, qbt_names: set[str]) -> bool:
        """Check if this key was already grabbed recently.

        If the torrent title is no longer in qBT (stall-killed, failed, etc.),
        clear the grab entry so we retry on the next cycle.
        """
        entry = grabbed.get(key)
        if not entry:
            return False
        age = time.time() - entry.get("timestamp", 0)
        if age > self._GRAB_COOLDOWN:
            del grabbed[key]
            return False
        # If the grabbed torrent disappeared from qBT, allow retry
        title = entry.get("title", "")
        if title and title not in qbt_names:
            log.info(
                "prowlarr-fallback: grabbed torrent gone from qBT, retrying: %s",
                title[:60],
            )
            del grabbed[key]
            return False
        return True
