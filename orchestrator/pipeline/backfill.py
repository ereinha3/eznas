"""Automatic backfill of missing TV episodes via Prowlarr season pack search.

When Sonarr cannot find individual episodes (dead seeders, niche content),
this module periodically searches Prowlarr for full season packs and adds
them to qBittorrent.  The existing pipeline then processes the downloads.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from ..models import BackfillConfig, StackConfig, VPN_ROUTED_SERVICES
from ..storage import ConfigRepository

log = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BackfillCandidate:
    """A series+season with missing episodes eligible for backfill."""

    series_id: int
    series_title: str
    tvdb_id: Optional[int]
    season_number: int
    missing_episodes: list[int]
    total_episodes_in_season: int
    series_type: str  # "standard", "anime", "daily"
    first_seen: int  # unix timestamp when first noticed missing
    year: Optional[int] = None  # series premiere year
    alternate_titles: list[str] = field(default_factory=list)


@dataclass
class ProwlarrResult:
    """A single search result from Prowlarr."""

    title: str
    guid: str
    download_url: Optional[str]
    magnet_url: Optional[str]
    seeders: int
    size: int  # bytes
    indexer: str


@dataclass
class ScoredResult:
    """A Prowlarr result with a computed quality score."""

    result: ProwlarrResult
    score: int
    reasons: list[str]


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

_QUALITY_PATTERNS = {
    "2160p": 25,
    "4k": 25,
    "uhd": 20,
    "1080p": 20,
    "720p": 5,
    "480p": -20,
    "hdtv": -10,
}

_GOOD_CODEC = re.compile(r"\b(x265|hevc|h\.?265)\b", re.IGNORECASE)
_BAD_QUALITY = re.compile(r"\b(cam|ts|telesync|hdcam|hdts)\b", re.IGNORECASE)
_FOREIGN_ONLY = re.compile(
    r"\b(sub\s*ita|vostfr|ita\b|french\b|german\b|spanish\b|"
    r"latino\b|rus\b|russian\b|polski\b|czech\b|hun\b|hungarian\b)",
    re.IGNORECASE,
)


def _score_result(
    result: ProwlarrResult,
    candidate: BackfillCandidate,
    cfg: BackfillConfig,
) -> ScoredResult:
    """Score a Prowlarr result for a given candidate season."""
    score = 0
    reasons: list[str] = []
    title_lower = result.title.lower()
    size_gb = result.size / (1024**3)

    # --- Hard filters ---
    if result.seeders < cfg.min_seeders:
        return ScoredResult(result, -9999, [f"seeders={result.seeders} < min={cfg.min_seeders}"])
    if size_gb > cfg.max_size_gb:
        return ScoredResult(result, -9999, [f"size={size_gb:.1f}GB > max={cfg.max_size_gb}GB"])
    if _BAD_QUALITY.search(result.title):
        return ScoredResult(result, -9999, ["bad quality (CAM/TS)"])
    if _FOREIGN_ONLY.search(result.title):
        return ScoredResult(result, -9999, ["foreign-only release"])

    # --- Year mismatch detection ---
    # If the series has a known year (e.g. 1996), penalise results that
    # explicitly mention a DIFFERENT year (e.g. "2023").  This prevents
    # grabbing remakes instead of originals.
    if candidate.year:
        year_matches = re.findall(r"\b((?:19|20)\d{2})\b", result.title)
        for ym in year_matches:
            yr = int(ym)
            if abs(yr - candidate.year) > 2:
                return ScoredResult(
                    result, -9999,
                    [f"year mismatch: result={yr}, series={candidate.year}"],
                )

    # --- Title relevance check ---
    # Verify the result title actually matches the series name, not a
    # spinoff or different show.  Normalize both to lowercase words.
    series_words = set(re.findall(r"[a-z0-9]+", candidate.series_title.lower()))
    result_words = set(re.findall(r"[a-z0-9]+", result.title.lower()))
    # Require at least 50% of series name words appear in the result
    if series_words:
        overlap = len(series_words & result_words) / len(series_words)
        if overlap < 0.5:
            return ScoredResult(
                result, -9999,
                [f"title mismatch: only {overlap:.0%} overlap with '{candidate.series_title}'"],
            )
        # Detect spinoff indicators not in the series name
        spinoff_markers = {"animated", "swearnet", "jail", "live", "movie", "movies",
                           "collection", "specials"}
        extra_markers = (result_words & spinoff_markers) - series_words
        if extra_markers:
            return ScoredResult(
                result, -9999,
                [f"spinoff detected ({', '.join(extra_markers)}) not in series title"],
            )

    # --- Season match ---
    season_pat = rf"\bS0*{candidate.season_number}\b"
    season_word = rf"\bseason\s*{candidate.season_number}\b"
    if re.search(season_pat, result.title, re.IGNORECASE):
        score += 50
        reasons.append("season tag match +50")
    elif re.search(season_word, result.title, re.IGNORECASE):
        score += 40
        reasons.append("season word match +40")
    else:
        # Might be a complete series pack or wrong season
        if "complete" in title_lower or "batch" in title_lower:
            score += 10
            reasons.append("complete/batch +10")
        else:
            score -= 100
            reasons.append("no season match -100")

    # --- Wrong season detection ---
    # If the result explicitly names a DIFFERENT season, penalise heavily
    other_season = re.findall(r"\bS(\d{1,2})\b", result.title, re.IGNORECASE)
    for s in other_season:
        if int(s) != candidate.season_number and int(s) != 0:
            score -= 200
            reasons.append(f"wrong season S{s} -200")
            break

    # --- Seeders ---
    if result.seeders >= 20:
        score += 30
        reasons.append(f"seeders={result.seeders} +30")
    elif result.seeders >= 10:
        score += 20
        reasons.append(f"seeders={result.seeders} +20")
    elif result.seeders >= 5:
        score += 10
        reasons.append(f"seeders={result.seeders} +10")

    # --- Quality ---
    for pattern, pts in _QUALITY_PATTERNS.items():
        if pattern in title_lower:
            score += pts
            reasons.append(f"{pattern} {'+' if pts > 0 else ''}{pts}")
            break

    if _GOOD_CODEC.search(result.title):
        score += 10
        reasons.append("good codec +10")

    # --- Size preference (prefer reasonable sizes) ---
    if 5 <= size_gb <= 60:
        score += 5
        reasons.append("reasonable size +5")

    # --- Dual audio bonus ---
    if re.search(r"\b(dual[\s._-]?audio|multi)\b", title_lower):
        score += 10
        reasons.append("dual audio +10")

    return ScoredResult(result, score, reasons)


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

class BackfillEngine:
    """Periodically searches Prowlarr for season packs to fill Sonarr gaps."""

    # Skip specials
    _SKIP_SEASON = {0}
    # Cooldown before retrying a season that had no results
    _SKIP_COOLDOWN = 24 * 3600  # 24 hours
    # Cooldown before re-grabbing a season whose torrent disappeared
    _GRAB_COOLDOWN = 24 * 3600  # 24 hours
    # Delay between Prowlarr searches (politeness)
    _SEARCH_DELAY = 5

    def __init__(self, repo: ConfigRepository) -> None:
        self.repo = repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def maybe_run(self, config: StackConfig) -> None:
        """Called every pipeline tick.  Runs a backfill cycle if due."""
        bf_cfg = config.services.pipeline.backfill
        if not bf_cfg.enabled:
            return

        state = self._load_state()
        last_run = state.get("last_run", "")
        if last_run:
            try:
                last_dt = datetime.fromisoformat(last_run)
                elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
                if elapsed < bf_cfg.interval_minutes * 60:
                    return
            except (ValueError, TypeError):
                pass

        log.info("backfill: starting cycle")
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
        """Run one backfill cycle: find missing -> search -> grab."""
        secrets = self.repo.load_state().get("secrets", {})
        sonarr_key = secrets.get("sonarr", {}).get("api_key")
        prowlarr_key = secrets.get("prowlarr", {}).get("api_key")
        qb_creds = secrets.get("qbittorrent", {})

        if not sonarr_key or not prowlarr_key:
            log.debug("backfill: missing API keys, skipping")
            return

        vpn = config.services.gluetun.enabled
        sonarr_host = "gluetun" if vpn and "sonarr" in VPN_ROUTED_SERVICES else "sonarr"
        prowlarr_host = "gluetun" if vpn and "prowlarr" in VPN_ROUTED_SERVICES else "prowlarr"
        qb_host = "gluetun" if vpn else "qbittorrent"

        timeout = httpx.Timeout(30.0, connect=10.0)
        search_timeout = httpx.Timeout(180.0, connect=10.0)

        # 1. Fetch missing episodes from Sonarr
        try:
            missing_eps = self._fetch_missing(sonarr_host, sonarr_key, timeout)
            series_map = self._fetch_series(sonarr_host, sonarr_key, timeout)
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            log.warning("backfill: Sonarr API error: %s", exc)
            return

        if not missing_eps:
            log.info("backfill: no missing episodes found")
            self._cleanup_state(state, set())
            return

        # 2. Group into candidates
        candidates = self._build_candidates(
            missing_eps, series_map, bf_cfg, state
        )
        if not candidates:
            log.info("backfill: no candidates above threshold")
            return

        # 3. Get active qBittorrent torrent names to avoid duplicates
        qb_names: set[str] = set()
        try:
            qb_names = self._get_qbt_names(
                qb_host, qb_creds, config.services.qbittorrent
            )
        except Exception as exc:
            log.warning("backfill: qBittorrent check failed: %s", exc)

        # 4. Also check Sonarr queue for pending downloads
        queued_seasons: set[str] = set()
        try:
            queued_seasons = self._get_sonarr_queue_seasons(
                sonarr_host, sonarr_key, timeout
            )
        except Exception:
            pass

        # 5. Search and grab
        grabs = 0
        searched = 0
        skipped = 0

        for candidate in candidates:
            if grabs >= bf_cfg.max_grabs_per_cycle:
                break

            key = f"{candidate.series_id}:{candidate.season_number}"

            # Skip if already grabbed recently
            if self._is_grabbed(state, key, qb_names):
                skipped += 1
                continue

            # Skip if in Sonarr queue already
            if key in queued_seasons:
                log.debug(
                    "backfill: %s S%02d already in Sonarr queue, skipping",
                    candidate.series_title, candidate.season_number,
                )
                skipped += 1
                continue

            # Skip if recently skipped (no results found)
            if self._is_skipped(state, key):
                skipped += 1
                continue

            # Rate limit searches
            if searched > 0:
                time.sleep(self._SEARCH_DELAY)

            # Search Prowlarr
            searched += 1
            try:
                results = self._search_prowlarr(
                    prowlarr_host, prowlarr_key, candidate, search_timeout
                )
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                log.warning(
                    "backfill: Prowlarr search failed for %s S%02d: %s",
                    candidate.series_title, candidate.season_number, exc,
                )
                continue

            if not results:
                log.info(
                    "backfill: no results for %s S%02d",
                    candidate.series_title, candidate.season_number,
                )
                self._record_skip(state, key, "no_results")
                continue

            # Score and pick best
            scored = [
                _score_result(r, candidate, bf_cfg) for r in results
            ]
            scored = [s for s in scored if s.score > 0]
            scored.sort(key=lambda s: s.score, reverse=True)

            if not scored:
                log.info(
                    "backfill: %d results for %s S%02d but none scored positive",
                    len(results), candidate.series_title, candidate.season_number,
                )
                self._record_skip(state, key, "no_viable_results")
                continue

            best = scored[0]
            log.info(
                "backfill: best for %s S%02d: %s (%.1f GB, %d seeders, score=%d)",
                candidate.series_title, candidate.season_number,
                best.result.title[:60],
                best.result.size / (1024**3),
                best.result.seeders,
                best.score,
            )
            for reason in best.reasons:
                log.debug("  scoring: %s", reason)

            # Add to qBittorrent
            category = config.download_policy.categories.sonarr
            ok = self._add_to_qbt(
                qb_host, qb_creds, config.services.qbittorrent,
                best.result, category,
            )
            if ok:
                grabs += 1
                self._record_grab(state, key, best.result)
                log.info(
                    "backfill: GRABBED %s S%02d -> %s (%.1f GB)",
                    candidate.series_title, candidate.season_number,
                    best.result.title[:50], best.result.size / (1024**3),
                )
            else:
                log.warning(
                    "backfill: failed to add torrent for %s S%02d",
                    candidate.series_title, candidate.season_number,
                )

        log.info(
            "backfill: cycle done — searched=%d, grabbed=%d, skipped=%d, candidates=%d",
            searched, grabs, skipped, len(candidates),
        )

    # ------------------------------------------------------------------
    # Sonarr API
    # ------------------------------------------------------------------

    def _fetch_missing(
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

    def _fetch_series(
        self, host: str, api_key: str, timeout: httpx.Timeout
    ) -> dict[int, dict]:
        """Fetch all series and return a map of seriesId -> series data."""
        resp = httpx.get(
            f"http://{host}:8989/api/v3/series",
            headers={"X-Api-Key": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        return {s["id"]: s for s in resp.json()}

    def _get_sonarr_queue_seasons(
        self, host: str, api_key: str, timeout: httpx.Timeout
    ) -> set[str]:
        """Return set of 'seriesId:season' keys for items in Sonarr queue."""
        resp = httpx.get(
            f"http://{host}:8989/api/v3/queue",
            params={"page": 1, "pageSize": 500},
            headers={"X-Api-Key": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        keys: set[str] = set()
        for record in resp.json().get("records", []):
            sid = record.get("seriesId")
            season = record.get("seasonNumber")
            if sid is not None and season is not None:
                keys.add(f"{sid}:{season}")
        return keys

    # ------------------------------------------------------------------
    # Candidate building
    # ------------------------------------------------------------------

    def _build_candidates(
        self,
        missing_eps: list[dict],
        series_map: dict[int, dict],
        bf_cfg: BackfillConfig,
        state: dict,
    ) -> list[BackfillCandidate]:
        """Group missing episodes into season-level candidates."""
        now = int(time.time())
        threshold_ts = now - bf_cfg.missing_days_threshold * 86400

        # Group by (seriesId, season)
        groups: dict[str, list[dict]] = {}
        for ep in missing_eps:
            sid = ep.get("seriesId")
            season = ep.get("seasonNumber", 0)
            if season in self._SKIP_SEASON:
                continue
            # Only include episodes that have actually aired
            air_date = ep.get("airDateUtc", "")
            if air_date and air_date > datetime.now(timezone.utc).isoformat():
                continue
            key = f"{sid}:{season}"
            groups.setdefault(key, []).append(ep)

        # Update first_seen tracking
        first_seen = state.setdefault("first_seen", {})
        current_keys = set(groups.keys())
        for key in current_keys:
            if key not in first_seen:
                first_seen[key] = now
        # Clean up keys no longer missing
        for key in list(first_seen.keys()):
            if key not in current_keys:
                del first_seen[key]

        # Build candidates that meet threshold
        candidates: list[BackfillCandidate] = []
        for key, eps in groups.items():
            seen_ts = first_seen.get(key, now)
            if seen_ts > threshold_ts:
                continue  # hasn't been missing long enough

            sid_str, season_str = key.split(":")
            sid = int(sid_str)
            season = int(season_str)

            series = series_map.get(sid)
            if not series:
                continue

            # Get total episode count for this season
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

        # Sort: most missing first, then oldest first_seen
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
        """Search Prowlarr for season packs matching a candidate."""
        headers = {"X-Api-Key": api_key}
        all_results: list[ProwlarrResult] = []

        # Build search queries (try multiple)
        queries = self._build_search_queries(candidate)

        for query in queries[:2]:  # limit to 2 queries per candidate
            try:
                resp = httpx.get(
                    f"http://{host}:9696/api/v1/search",
                    params={"query": query, "type": "search"},
                    headers=headers,
                    timeout=timeout,
                )
                resp.raise_for_status()
                for item in resp.json():
                    pr = ProwlarrResult(
                        title=item.get("title", ""),
                        guid=item.get("guid", ""),
                        download_url=item.get("downloadUrl"),
                        magnet_url=item.get("magnetUrl"),
                        seeders=item.get("seeders", 0),
                        size=item.get("size", 0),
                        indexer=item.get("indexer", ""),
                    )
                    # Only include results with download capability
                    if pr.download_url or pr.magnet_url:
                        all_results.append(pr)
            except httpx.TimeoutException:
                log.warning(
                    "backfill: Prowlarr search timed out for query: %s",
                    query,
                )
                continue

        # Deduplicate by guid
        seen_guids: set[str] = set()
        unique: list[ProwlarrResult] = []
        for r in all_results:
            if r.guid not in seen_guids:
                seen_guids.add(r.guid)
                unique.append(r)

        return unique

    def _build_search_queries(self, candidate: BackfillCandidate) -> list[str]:
        """Build Prowlarr search query strings for a candidate."""
        title = candidate.series_title
        season = candidate.season_number
        year = candidate.year
        queries = []

        # Include year for disambiguation when available (e.g. "1996")
        year_tag = f" {year}" if year else ""

        if candidate.series_type == "anime":
            # Anime: try batch queries
            queries.append(f"{title}{year_tag} S{season:02d} 1080p")
            queries.append(f"{title} batch 1080p")
            # Try alternate titles
            for alt in candidate.alternate_titles[:1]:
                queries.append(f"{alt} S{season:02d} 1080p")
        else:
            queries.append(f"{title}{year_tag} S{season:02d} 1080p")
            queries.append(f"{title} S{season:02d} 1080p")

        return queries

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
        """Add a torrent to qBittorrent from a Prowlarr result."""
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
                log.error("backfill: qBittorrent auth failed")
                return False

            # Try magnet first
            if result.magnet_url:
                resp = client.post(
                    f"http://{host}:8080/api/v2/torrents/add",
                    data={"urls": result.magnet_url, "category": category},
                )
                return resp.status_code == 200

            # Try download URL (may be a torrent file or magnet redirect)
            if result.download_url:
                if result.download_url.startswith("magnet:"):
                    resp = client.post(
                        f"http://{host}:8080/api/v2/torrents/add",
                        data={"urls": result.download_url, "category": category},
                    )
                    return resp.status_code == 200

                # Download the .torrent file via the URL
                try:
                    dl_resp = httpx.get(
                        result.download_url,
                        timeout=30,
                        follow_redirects=False,
                    )
                    # Handle magnet redirects
                    if dl_resp.status_code in (301, 302, 303, 307, 308):
                        location = dl_resp.headers.get("location", "")
                        if location.startswith("magnet:"):
                            resp = client.post(
                                f"http://{host}:8080/api/v2/torrents/add",
                                data={"urls": location, "category": category},
                            )
                            return resp.status_code == 200

                    if dl_resp.status_code == 200 and len(dl_resp.content) > 100:
                        resp = client.post(
                            f"http://{host}:8080/api/v2/torrents/add",
                            data={"category": category},
                            files={
                                "torrents": (
                                    "backfill.torrent",
                                    dl_resp.content,
                                    "application/x-bittorrent",
                                )
                            },
                        )
                        return resp.status_code == 200
                except Exception as exc:
                    log.warning("backfill: download URL fetch failed: %s", exc)
                    return False

            return False
        finally:
            client.close()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _load_state(self) -> dict:
        return self.repo.load_backfill_state()

    def _save_state(self, state: dict) -> None:
        self.repo.save_backfill_state(state)

    def _is_grabbed(self, state: dict, key: str, qbt_names: set[str]) -> bool:
        """Check if this season was already grabbed recently."""
        grabbed = state.get("grabbed", {})
        entry = grabbed.get(key)
        if not entry:
            return False
        # If grab is old enough, allow re-search
        age = time.time() - entry.get("timestamp", 0)
        if age > self._GRAB_COOLDOWN:
            del grabbed[key]
            return False
        return True

    def _is_skipped(self, state: dict, key: str) -> bool:
        """Check if this season was skipped (no results) recently."""
        skipped = state.get("skipped", {})
        entry = skipped.get(key)
        if not entry:
            return False
        retry_after = entry.get("retry_after", 0)
        if time.time() > retry_after:
            del skipped[key]
            return False
        return True

    def _record_grab(self, state: dict, key: str, result: ProwlarrResult) -> None:
        grabbed = state.setdefault("grabbed", {})
        grabbed[key] = {
            "timestamp": int(time.time()),
            "torrent_name": result.title,
            "indexer": result.indexer,
        }

    def _record_skip(self, state: dict, key: str, reason: str) -> None:
        skipped = state.setdefault("skipped", {})
        skipped[key] = {
            "reason": reason,
            "timestamp": int(time.time()),
            "retry_after": int(time.time()) + self._SKIP_COOLDOWN,
        }

    def _cleanup_state(self, state: dict, current_missing: set[str]) -> None:
        """Remove state entries for seasons no longer missing."""
        for section in ("first_seen", "grabbed", "skipped"):
            data = state.get(section, {})
            for key in list(data.keys()):
                if key not in current_missing:
                    del data[key]
