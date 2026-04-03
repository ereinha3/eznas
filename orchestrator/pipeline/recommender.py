"""Media recommendation engine (V2 — collaborative embeddings).

Generates personalized recommendations for Jellyfin users by combining:
1. Collaborative item embeddings learned from MovieLens 25M (implicit ALS)
2. Content projection MLP for cold-start items not in MovieLens
3. FAISS nearest-neighbor search for fast recommendation serving
4. Per-user taste profiles derived from Jellyfin watch history

The key insight: embeddings from 25M real user-item interactions capture
behavioral patterns ("users who watch X also watch Y") that text-based
embeddings cannot. New movies get projected into this learned space via
a metadata-to-embedding MLP trained on the MovieLens items.
"""

from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..clients.jellyfin import JellyfinClient
from ..models import RecommenderConfig, StackConfig
from ..storage import ConfigRepository
from .recommender_index import EmbeddingIndex, IndexedItem, EMBEDDING_DIM

log = logging.getLogger("recommender")


class RecommenderEngine:
    """Core recommendation engine.

    Lifecycle:
        1. maybe_run() — called on interval, builds/loads index and generates recs
        2. get_recommendations() — returns cached recs from state (API serving)
        3. get_similar() — ad-hoc FAISS query for a single item
    """

    def __init__(self, repo: ConfigRepository, data_dir: Path) -> None:
        self.repo = repo
        self._jellyfin = JellyfinClient(repo)
        self._index = EmbeddingIndex(data_dir)
        self._data_dir = data_dir
        self._projection_mlp: Any = None  # ProjectionMLP (lazy-loaded)

    def maybe_run(self, config: StackConfig) -> None:
        """Run the recommendation cycle if the interval has elapsed."""
        rec_cfg = config.services.pipeline.recommender
        if not rec_cfg.enabled:
            return

        state = self.repo.load_recommender_state()
        last_run = state.get("last_run", 0)
        interval = rec_cfg.refresh_interval_hours * 3600

        if time.time() - last_run < interval:
            return

        log.info("recommender: starting recommendation cycle")
        t0 = time.time()

        try:
            # Step 1: Ensure ALS embeddings are trained
            self._ensure_als_trained(rec_cfg, state)

            # Step 2: Build or load FAISS index from ALS embeddings
            if not self._index.loaded:
                if not self._index.load():
                    log.info("recommender: building index from ALS embeddings...")
                    stats = self._index.build_from_als()
                    state["index_stats"] = stats
                else:
                    state["index_stats"] = {
                        "movie_count": self._index.item_count,
                        "tv_count": 0,
                        "index_type": "cached",
                        "embedding_source": "als",
                    }

            # Step 3: Project cold-start items (TV shows, new movies not in ALS)
            projected_count = self._project_cold_start_items(config, rec_cfg)
            if projected_count:
                stats = state.get("index_stats", {})
                stats["projected_count"] = projected_count
                state["index_stats"] = stats

            # Step 4: Mark library items as owned
            owned_count = self._mark_library_items(config, state)
            log.info("recommender: marked %d library items as owned", owned_count)

            # Step 5: Build user profiles from Jellyfin watch history
            self._refresh_user_profiles(config, rec_cfg, state)

            # Step 6: Generate recommendations for each user
            self._generate_recommendations(rec_cfg, state)

            # Step 7: Auto-request top unowned recommendations via Jellyseerr
            if rec_cfg.auto_request_enabled:
                self._auto_request(config, rec_cfg, state)

            state["last_run"] = int(time.time())
            state["last_error"] = None
            elapsed = time.time() - t0
            log.info("recommender: cycle complete in %.1fs", elapsed)

        except Exception as exc:
            log.error("recommender: cycle failed: %s", exc, exc_info=True)
            state["last_error"] = str(exc)
            state["last_run"] = int(time.time())

        self.repo.save_recommender_state(state)

    def force_rebuild(self, config: StackConfig) -> dict[str, Any]:
        """Force a full index rebuild and recommendation cycle."""
        rec_cfg = config.services.pipeline.recommender
        state = self.repo.load_recommender_state()

        # Retrain ALS if needed, then build index
        self._ensure_als_trained(rec_cfg, state)
        stats = self._index.build_from_als()
        state["index_stats"] = stats

        # Project cold-start items
        projected = self._project_cold_start_items(config, rec_cfg)
        stats["projected_count"] = projected

        owned_count = self._mark_library_items(config, state)
        self._refresh_user_profiles(config, rec_cfg, state)
        self._generate_recommendations(rec_cfg, state)

        state["last_run"] = int(time.time())
        state["last_error"] = None
        self.repo.save_recommender_state(state)

        return {
            "index_stats": stats,
            "owned_count": owned_count,
            "user_count": len(state.get("user_profiles", {})),
        }

    def get_recommendations(self, user_id: str) -> Optional[dict[str, Any]]:
        """Read cached recommendations for a user from state."""
        state = self.repo.load_recommender_state()
        return state.get("recommendations", {}).get(user_id)

    def get_similar(self, tmdb_id: int, k: int = 10) -> list[dict[str, Any]]:
        """Ad-hoc similarity search for a single TMDb item."""
        self._ensure_index_loaded()
        result = self._index.get_by_tmdb_id(tmdb_id)
        if not result:
            return []
        idx, _ = result
        vec = self._index.get_vector(idx)
        if vec is None:
            return []
        hits = self._index.search(vec, k=k + 1)
        results = []
        for hit_idx, score in hits:
            item = self._index.get_item(hit_idx)
            if item is None or item.tmdb_id == tmdb_id:
                continue
            results.append(self._format_item(item, score))
            if len(results) >= k:
                break
        return results

    def get_status(self) -> dict[str, Any]:
        """Return current engine status for the API."""
        state = self.repo.load_recommender_state()
        profiles = state.get("user_profiles", {})
        recs = state.get("recommendations", {})
        total_recs = sum(len(r.get("for_you", [])) for r in recs.values())
        auto_state = state.get("auto_requests", {})
        all_requested = auto_state.get("_all_requested", {})
        recent_auto = sorted(
            all_requested.values(),
            key=lambda x: x.get("timestamp", 0),
            reverse=True,
        )[:10]
        return {
            "last_run": state.get("last_run"),
            "last_error": state.get("last_error"),
            "index_stats": state.get("index_stats"),
            "user_count": len(profiles),
            "total_recommendations": total_recs,
            "index_loaded": self._index.loaded,
            "auto_request": {
                "total_requested": len(all_requested),
                "recent": recent_auto,
            },
        }

    def get_user_profiles(self) -> list[dict[str, Any]]:
        """Return summary of all user profiles for the admin API."""
        state = self.repo.load_recommender_state()
        profiles = state.get("user_profiles", {})
        result = []
        for user_id, profile in profiles.items():
            result.append({
                "user_id": user_id,
                "username": profile.get("username", ""),
                "watched_count": profile.get("watched_count", 0),
                "has_profile": profile.get("profile_vector") is not None,
                "last_fetched": profile.get("last_fetched"),
            })
        return result

    # ------------------------------------------------------------------ internal

    def _ensure_index_loaded(self) -> None:
        if not self._index.loaded:
            if not self._index.load():
                log.warning("recommender: no index available")
                return
            # Mark library items from cached state
            state = self.repo.load_recommender_state()
            library_map = state.get("library_tmdb_ids", {})
            if library_map and isinstance(library_map, dict):
                # Keys are string TMDb IDs, values are Jellyfin item IDs
                int_map = {int(k): v for k, v in library_map.items()}
                self._index.mark_owned(set(int_map.keys()), int_map)

    def _ensure_als_trained(
        self, rec_cfg: RecommenderConfig, state: dict[str, Any]
    ) -> None:
        """Ensure ALS embeddings exist on disk. Train if missing."""
        als_path = self._data_dir / "als_vectors.npy"
        if als_path.exists():
            return

        log.info("recommender: ALS embeddings not found, training...")
        from .recommender_train import train_collaborative_embeddings

        train_stats = train_collaborative_embeddings(
            data_dir=self._data_dir,
            als_dim=EMBEDDING_DIM,
            min_item_ratings=rec_cfg.min_vote_count,
        )
        state["training_stats"] = train_stats
        log.info("recommender: ALS training complete: %s", train_stats)

    def _get_projection_mlp(self):
        """Lazy-load the content projection MLP."""
        if self._projection_mlp is not None:
            return self._projection_mlp

        from .recommender_train import ProjectionMLP
        mlp = ProjectionMLP(self._data_dir)
        if mlp.load():
            self._projection_mlp = mlp
            return mlp

        # MLP not trained yet — train it now (genre-only, no plot embeddings)
        log.info("recommender: projection MLP not found, training...")
        from .recommender_train import train_projection_mlp
        train_projection_mlp(data_dir=self._data_dir)
        mlp = ProjectionMLP(self._data_dir)
        if mlp.load():
            self._projection_mlp = mlp
        return self._projection_mlp

    def _project_cold_start_items(
        self, config: StackConfig, rec_cfg: RecommenderConfig
    ) -> int:
        """Project cold-start items into the ALS embedding space via MLP.

        Three sources:
        1. TV shows from Sonarr library (owned)
        2. Library movies/series not in ALS training set (owned)
        3. Popular TV shows from TMDb discovery (not owned — fills "Discover Similar")
        """
        mlp = self._get_projection_mlp()
        if mlp is None:
            log.warning("recommender: projection MLP unavailable, skipping cold-start")
            return 0

        import httpx

        projected_items: list[IndexedItem] = []
        projected_genres: list[list[str]] = []

        # Get TMDb API key for poster/metadata enrichment
        tmdb_api_key = getattr(rec_cfg, "tmdb_api_key", None) or ""
        tmdb_client = None
        if tmdb_api_key:
            tmdb_client = httpx.Client(
                base_url="https://api.themoviedb.org/3",
                params={"api_key": tmdb_api_key},
                timeout=httpx.Timeout(10.0, connect=5.0),
            )

        def _fetch_tmdb_tv_metadata(tid: int) -> dict:
            """Fetch TV show metadata from TMDb API."""
            if not tmdb_client:
                return {}
            try:
                resp = tmdb_client.get(f"/tv/{tid}")
                if resp.status_code == 200:
                    return resp.json()
            except Exception:
                pass
            return {}

        try:
            # ── 1. TV shows from Sonarr library ──────────────────────
            secrets = self.repo.load_secrets()
            sonarr_key = secrets.get("sonarr", {}).get("api_key")
            if sonarr_key:
                from ..clients.util import service_base_url
                base_url = service_base_url("sonarr", config, 8989)
                with httpx.Client(
                    base_url=base_url,
                    headers={"X-Api-Key": sonarr_key},
                    timeout=httpx.Timeout(30.0, connect=5.0),
                ) as client:
                    response = client.get("/api/v3/series")
                    response.raise_for_status()
                    all_series = response.json() or []

                for series in all_series:
                    tmdb_id = series.get("tmdbId", 0)
                    title = series.get("title", "")
                    if not tmdb_id or not title:
                        continue
                    if self._index.get_by_tmdb_id(tmdb_id) is not None:
                        continue

                    genres = series.get("genres", [])
                    rating = series.get("ratings", {}).get("value", 0)

                    # Fetch poster from TMDb (not TVDB) for correct format
                    tmdb_data = _fetch_tmdb_tv_metadata(tmdb_id)
                    poster_path = tmdb_data.get("poster_path", "")
                    overview = tmdb_data.get("overview", "") or ""
                    vote_avg = tmdb_data.get("vote_average", float(rating) if rating else 0.0)

                    projected_items.append(IndexedItem(
                        tmdb_id=tmdb_id,
                        imdb_id=str(series.get("imdbId", "")),
                        title=title,
                        genres=genres,
                        vote_average=float(vote_avg),
                        release_date=str(series.get("year", "")),
                        poster_path=poster_path,
                        media_type="tv",
                        overview=overview,
                        source="projected",
                    ))
                    projected_genres.append(genres)

                log.info("recommender: %d library TV shows to project", len(projected_items))

            # ── 2. Library movies/series not in ALS ──────────────────
            library_count_before = len(projected_items)
            library_items = self._jellyfin.get_library_items_with_providers(
                config, item_types="Movie,Series"
            )
            from .recommender_train import load_enrichment_cache
            enrichment = load_enrichment_cache(self._data_dir)

            for item in library_items:
                providers = item.get("ProviderIds", {})
                tmdb_str = providers.get("Tmdb", "")
                if not tmdb_str:
                    continue
                try:
                    tmdb_id = int(tmdb_str)
                except (ValueError, TypeError):
                    continue
                if self._index.get_by_tmdb_id(tmdb_id) is not None:
                    continue
                # Also skip if already added from Sonarr above
                if any(p.tmdb_id == tmdb_id for p in projected_items):
                    continue

                name = item.get("Name", "")
                if not name:
                    continue

                item_type = item.get("Type", "Movie")
                media_type = "tv" if item_type == "Series" else "movie"
                genres = item.get("Genres", []) or []
                overview = item.get("Overview", "")
                rating = item.get("CommunityRating", 0) or 0
                year = item.get("ProductionYear", "")
                premiere = item.get("PremiereDate", "")

                cached = enrichment.get(str(tmdb_id), {})
                poster_path = cached.get("poster_path", "")

                # For TV without poster, fetch from TMDb
                if not poster_path and media_type == "tv":
                    tmdb_data = _fetch_tmdb_tv_metadata(tmdb_id)
                    poster_path = tmdb_data.get("poster_path", "")
                    if not overview:
                        overview = tmdb_data.get("overview", "")

                projected_items.append(IndexedItem(
                    tmdb_id=tmdb_id,
                    imdb_id="",
                    title=name,
                    genres=genres,
                    vote_average=float(rating),
                    release_date=premiere[:10] if premiere else str(year),
                    poster_path=poster_path,
                    media_type=media_type,
                    overview=overview,
                    source="projected",
                ))
                projected_genres.append(genres)

            library_projected = len(projected_items) - library_count_before
            if library_projected > 0:
                log.info("recommender: %d additional library items to project", library_projected)

            # ── 3. Popular TV shows from TMDb discovery ──────────────
            # Fetch popular TV shows NOT in library to fill "Discover Similar"
            if tmdb_client:
                tv_discovery_count = 0
                existing_tv_ids = {
                    p.tmdb_id for p in projected_items if p.media_type == "tv"
                }
                # Also check items already in the index
                import gzip as _gzip
                import json as _json
                try:
                    meta_path = self._data_dir / "metadata.json.gz"
                    if meta_path.exists():
                        with _gzip.open(meta_path, "rt") as f:
                            for m in _json.load(f):
                                if m.get("media_type") == "tv":
                                    existing_tv_ids.add(m["tmdb_id"])
                except Exception:
                    pass

                log.info("recommender: fetching popular TV shows from TMDb for discovery...")
                for page in range(1, 51):  # Up to 1000 TV shows (20 per page)
                    try:
                        resp = tmdb_client.get("/discover/tv", params={
                            "sort_by": "popularity.desc",
                            "vote_count.gte": 100,
                            "page": page,
                        })
                        if resp.status_code != 200:
                            break
                        results = resp.json().get("results", [])
                        if not results:
                            break

                        for tv in results:
                            tid = tv.get("id", 0)
                            if not tid or tid in existing_tv_ids:
                                continue
                            if self._index.get_by_tmdb_id(tid) is not None:
                                continue

                            existing_tv_ids.add(tid)
                            name = tv.get("name", "")
                            if not name:
                                continue

                            # Map TMDb genre IDs to names
                            genre_ids = tv.get("genre_ids", [])
                            genres = self._map_tmdb_genre_ids(genre_ids)

                            projected_items.append(IndexedItem(
                                tmdb_id=tid,
                                imdb_id="",
                                title=name,
                                genres=genres,
                                vote_average=float(tv.get("vote_average", 0)),
                                release_date=tv.get("first_air_date", ""),
                                poster_path=tv.get("poster_path", ""),
                                media_type="tv",
                                overview=tv.get("overview", ""),
                                source="projected",
                            ))
                            projected_genres.append(genres)
                            tv_discovery_count += 1

                        import time as _time
                        _time.sleep(0.03)  # Rate limit
                    except Exception as exc:
                        log.warning("recommender: TMDb TV discovery page %d failed: %s", page, exc)
                        break

                log.info("recommender: discovered %d popular TV shows from TMDb", tv_discovery_count)

        except Exception as exc:
            log.warning("recommender: cold-start projection error: %s", exc)
        finally:
            if tmdb_client:
                tmdb_client.close()

        if not projected_items:
            return 0

        vectors = mlp.project_batch(projected_genres)
        added = self._index.append_projected_items(projected_items, vectors)
        log.info("recommender: projected %d total cold-start items into index", added)
        return added

    @staticmethod
    def _map_tmdb_genre_ids(genre_ids: list[int]) -> list[str]:
        """Map TMDb genre IDs to genre name strings."""
        # TMDb TV genre ID mapping
        tmdb_genres = {
            10759: "Action & Adventure", 16: "Animation", 35: "Comedy",
            80: "Crime", 99: "Documentary", 18: "Drama", 10751: "Family",
            10762: "Kids", 9648: "Mystery", 10763: "News", 10764: "Reality",
            10765: "Sci-Fi & Fantasy", 10766: "Soap", 10767: "Talk",
            10768: "War & Politics", 37: "Western",
            # Movie genres that might appear
            28: "Action", 12: "Adventure", 14: "Fantasy",
            36: "History", 27: "Horror", 10402: "Music",
            10749: "Romance", 878: "Sci-Fi", 53: "Thriller",
        }
        return [tmdb_genres.get(gid, f"Genre-{gid}") for gid in genre_ids if gid in tmdb_genres]

    def _mark_library_items(self, config: StackConfig, state: dict[str, Any] | None = None) -> int:
        """Query Jellyfin for all library items and mark them in the index."""
        try:
            items = self._jellyfin.get_library_items_with_providers(
                config, item_types="Movie,Series"
            )
        except Exception as exc:
            log.warning("recommender: failed to fetch library items: %s", exc)
            return 0

        tmdb_ids: set[int] = set()
        jellyfin_map: dict[int, str] = {}
        for item in items:
            providers = item.get("ProviderIds", {})
            tmdb_str = providers.get("Tmdb")
            if tmdb_str:
                try:
                    tid = int(tmdb_str)
                    tmdb_ids.add(tid)
                    jellyfin_map[tid] = item.get("Id", "")
                except (ValueError, TypeError):
                    pass

        # Persist library mapping so lazy-loaded index can mark items
        if state is not None:
            state["library_tmdb_ids"] = jellyfin_map

        return self._index.mark_owned(tmdb_ids, jellyfin_map)

    def _refresh_user_profiles(
        self,
        config: StackConfig,
        rec_cfg: RecommenderConfig,
        state: dict[str, Any],
    ) -> None:
        """Fetch Jellyfin watch history and build taste vectors per user."""
        try:
            users = self._jellyfin.get_users(config)
        except Exception as exc:
            log.warning("recommender: failed to fetch Jellyfin users: %s", exc)
            return

        profiles = state.setdefault("user_profiles", {})

        for user in users:
            user_id = user.get("Id", "")
            username = user.get("Name", "")
            if not user_id:
                continue

            try:
                watched_items = self._jellyfin.get_user_watched_items(
                    config, user_id, item_types="Movie,Series"
                )
            except Exception as exc:
                log.warning("recommender: failed to fetch history for %s: %s", username, exc)
                continue

            watched_tmdb_ids: list[int] = []
            for item in watched_items:
                providers = item.get("ProviderIds", {})
                tmdb_str = providers.get("Tmdb")
                if tmdb_str:
                    try:
                        watched_tmdb_ids.append(int(tmdb_str))
                    except (ValueError, TypeError):
                        pass

            profile_vec = self._build_taste_vector(watched_tmdb_ids)

            profiles[user_id] = {
                "username": username,
                "watched_count": len(watched_tmdb_ids),
                "watched_tmdb_ids": watched_tmdb_ids[:1000],
                "profile_vector": (
                    self._encode_vector(profile_vec) if profile_vec is not None else None
                ),
                "last_fetched": int(time.time()),
            }

            log.info(
                "recommender: user %s — %d watched, profile=%s",
                username, len(watched_tmdb_ids),
                "built" if profile_vec is not None else "insufficient data",
            )

    def _build_taste_vector(self, watched_tmdb_ids: list[int]) -> Optional[np.ndarray]:
        """Build a normalized taste vector from a user's watch history."""
        vectors = []
        for tmdb_id in watched_tmdb_ids:
            result = self._index.get_by_tmdb_id(tmdb_id)
            if result is None:
                continue
            idx, _ = result
            vec = self._index.get_vector(idx)
            if vec is not None:
                vectors.append(vec)

        if len(vectors) < 1:
            return None

        profile = np.mean(vectors, axis=0).astype(np.float32)
        norm = np.linalg.norm(profile)
        if norm > 0:
            profile = profile / norm
        return profile

    def _generate_recommendations(
        self,
        rec_cfg: RecommenderConfig,
        state: dict[str, Any],
    ) -> None:
        """Generate and cache recommendations for all profiled users."""
        profiles = state.get("user_profiles", {})
        recommendations: dict[str, Any] = {}

        for user_id, profile_data in profiles.items():
            vec_encoded = profile_data.get("profile_vector")
            watched_ids = set(profile_data.get("watched_tmdb_ids", []))

            if vec_encoded is None:
                continue
            if profile_data.get("watched_count", 0) < rec_cfg.min_watched_for_profile:
                continue

            profile_vec = self._decode_vector(vec_encoded)
            if profile_vec is None:
                continue

            for_you = self._generate_for_you(profile_vec, watched_ids, rec_cfg)
            because = self._generate_because_you_watched(
                profile_data.get("watched_tmdb_ids", []),
                watched_ids,
                rec_cfg,
            )

            recommendations[user_id] = {
                "for_you": for_you,
                "because_you_watched": because,
                "generated_at": int(time.time()),
            }

            log.info(
                "recommender: %s — %d for_you, %d because_you_watched sections",
                profile_data.get("username", user_id),
                len(for_you), len(because),
            )

        state["recommendations"] = recommendations

    def _generate_for_you(
        self,
        profile_vec: np.ndarray,
        watched_ids: set[int],
        rec_cfg: RecommenderConfig,
    ) -> list[dict[str, Any]]:
        """FAISS nearest-neighbor search from user taste vector."""
        fetch_k = rec_cfg.max_recommendations_per_user * 3
        hits = self._index.search(profile_vec, k=fetch_k)

        results = []
        for idx, score in hits:
            item = self._index.get_item(idx)
            if item is None or item.tmdb_id in watched_ids:
                continue

            adjusted = score
            if item.in_library:
                adjusted *= rec_cfg.owned_weight

            entry = self._format_item(item, adjusted)
            entry["reason"] = "taste_profile"
            results.append(entry)

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:rec_cfg.max_recommendations_per_user]

    def _generate_because_you_watched(
        self,
        watched_tmdb_ids: list[int],
        watched_set: set[int],
        rec_cfg: RecommenderConfig,
    ) -> list[dict[str, Any]]:
        """For each recent watch, find similar items."""
        sections = []
        recent = watched_tmdb_ids[:10]

        for tmdb_id in recent:
            result = self._index.get_by_tmdb_id(tmdb_id)
            if result is None:
                continue
            idx, seed_item = result
            vec = self._index.get_vector(idx)
            if vec is None:
                continue

            hits = self._index.search(vec, k=rec_cfg.because_you_watched_count + 10)
            items = []
            for hit_idx, score in hits:
                item = self._index.get_item(hit_idx)
                if item is None or item.tmdb_id == tmdb_id or item.tmdb_id in watched_set:
                    continue
                adjusted = score * (rec_cfg.owned_weight if item.in_library else 1.0)
                items.append(self._format_item(item, adjusted))
                if len(items) >= rec_cfg.because_you_watched_count:
                    break

            if items:
                sections.append({
                    "seed_tmdb_id": tmdb_id,
                    "seed_title": seed_item.title,
                    "items": items,
                })

        return sections

    # ------------------------------------------------------------------ Jellyseerr auto-request

    def _auto_request(
        self,
        config: StackConfig,
        rec_cfg: RecommenderConfig,
        state: dict[str, Any],
    ) -> None:
        """Auto-request top unowned recommendations via Jellyseerr."""
        if not rec_cfg.auto_request_max_per_day:
            return

        try:
            api_key = self._get_jellyseerr_api_key(config)
            if not api_key:
                log.info("recommender: no Jellyseerr API key, skipping auto-request")
                return

            from ..clients.util import service_base_url
            import httpx

            base_url = service_base_url("jellyseerr", config, 5055)

            with httpx.Client(
                base_url=base_url,
                headers={"X-Api-Key": api_key, "Accept": "application/json"},
                timeout=httpx.Timeout(20.0, connect=5.0),
            ) as client:
                existing_tmdb_ids = self._get_pending_request_tmdb_ids(client)

                auto_state = state.setdefault("auto_requests", {})
                all_requested = auto_state.setdefault("_all_requested", {})
                today = time.strftime("%Y-%m-%d")
                total_requested = 0

                recommendations = state.get("recommendations", {})
                for user_id, user_recs in recommendations.items():
                    user_auto = auto_state.setdefault(user_id, {})
                    today_count = user_auto.get(today, 0)
                    remaining = rec_cfg.auto_request_max_per_day - today_count

                    if remaining <= 0:
                        continue

                    for_you = user_recs.get("for_you", [])
                    requested_this_cycle = 0

                    for rec in for_you:
                        if requested_this_cycle >= remaining:
                            break
                        if rec.get("in_library"):
                            continue

                        tmdb_id = rec.get("tmdb_id")
                        media_type = rec.get("media_type", "movie")

                        if not tmdb_id:
                            continue
                        if tmdb_id in existing_tmdb_ids:
                            continue
                        if str(tmdb_id) in all_requested:
                            continue

                        success = self._submit_jellyseerr_request(
                            client, tmdb_id, media_type
                        )
                        if success:
                            requested_this_cycle += 1
                            total_requested += 1
                            existing_tmdb_ids.add(tmdb_id)
                            all_requested[str(tmdb_id)] = {
                                "title": rec.get("title", ""),
                                "media_type": media_type,
                                "score": rec.get("score", 0),
                                "user_id": user_id,
                                "timestamp": int(time.time()),
                            }

                    user_auto[today] = today_count + requested_this_cycle

                if total_requested:
                    log.info(
                        "recommender: auto-requested %d items via Jellyseerr",
                        total_requested,
                    )

                self._prune_auto_request_state(auto_state)

        except Exception as exc:
            log.warning("recommender: auto-request failed: %s", exc)

    def _get_jellyseerr_api_key(self, config: StackConfig) -> Optional[str]:
        import json as _json
        from ..clients.util import get_service_config_dir
        config_dir = get_service_config_dir("jellyseerr", config)
        settings_path = config_dir / "settings.json"
        try:
            data = _json.loads(settings_path.read_text())
            return data.get("main", {}).get("apiKey")
        except (FileNotFoundError, _json.JSONDecodeError, KeyError):
            return None

    @staticmethod
    def _get_pending_request_tmdb_ids(client) -> set[int]:
        tmdb_ids: set[int] = set()
        try:
            page = 1
            while True:
                response = client.get(
                    "/api/v1/request",
                    params={"take": 100, "skip": (page - 1) * 100},
                )
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])
                if not results:
                    break
                for req in results:
                    media = req.get("media", {})
                    tmdb_id = media.get("tmdbId")
                    if tmdb_id:
                        tmdb_ids.add(int(tmdb_id))
                page_info = data.get("pageInfo", {})
                if page * 100 >= page_info.get("results", 0):
                    break
                page += 1
        except Exception as exc:
            log.warning("recommender: failed to fetch Jellyseerr requests: %s", exc)
        return tmdb_ids

    @staticmethod
    def _submit_jellyseerr_request(client, tmdb_id: int, media_type: str) -> bool:
        try:
            response = client.post(
                "/api/v1/request",
                json={"mediaType": media_type, "mediaId": tmdb_id},
            )
            if response.status_code in (200, 201):
                log.info("recommender: auto-requested %s tmdb=%d", media_type, tmdb_id)
                return True
            else:
                log.debug(
                    "recommender: Jellyseerr request for tmdb=%d returned %d",
                    tmdb_id, response.status_code,
                )
                return False
        except Exception as exc:
            log.debug("recommender: Jellyseerr request failed for tmdb=%d: %s", tmdb_id, exc)
            return False

    @staticmethod
    def _prune_auto_request_state(auto_state: dict) -> None:
        import datetime
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        for user_id in list(auto_state.keys()):
            if user_id.startswith("_"):
                continue
            user_data = auto_state[user_id]
            if isinstance(user_data, dict):
                for date_key in list(user_data.keys()):
                    if date_key < cutoff:
                        del user_data[date_key]

        all_req = auto_state.get("_all_requested", {})
        if len(all_req) > 500:
            sorted_items = sorted(
                all_req.items(),
                key=lambda x: x[1].get("timestamp", 0),
                reverse=True,
            )
            auto_state["_all_requested"] = dict(sorted_items[:500])

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _format_item(item: IndexedItem, score: float) -> dict[str, Any]:
        return {
            "tmdb_id": item.tmdb_id,
            "title": item.title,
            "score": round(score, 4),
            "in_library": item.in_library,
            "media_type": item.media_type,
            "genres": item.genres,
            "vote_average": item.vote_average,
            "release_date": item.release_date,
            "poster_path": item.poster_path,
            "overview": item.overview,
            "jellyfin_id": item.jellyfin_id,
        }

    @staticmethod
    def _encode_vector(vec: np.ndarray) -> str:
        return base64.b64encode(vec.astype(np.float32).tobytes()).decode("ascii")

    @staticmethod
    def _decode_vector(encoded: str) -> Optional[np.ndarray]:
        try:
            raw = base64.b64decode(encoded)
            return np.frombuffer(raw, dtype=np.float32).copy()
        except Exception:
            return None
