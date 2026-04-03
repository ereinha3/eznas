"""Unit tests for the media recommendation engine (V2 — collaborative embeddings).

Tests the recommender index, engine, training pipeline, and cold-start
projection with mocked external dependencies. Validates core logic:
vector math, scoring, profile building, state management, auto-request,
ALS embedding loading, and projection MLP.
"""

from __future__ import annotations

import base64
import gzip
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from orchestrator.models import RecommenderConfig
from orchestrator.pipeline.recommender import RecommenderEngine
from orchestrator.pipeline.recommender_index import (
    EMBEDDING_DIM,
    EmbeddingIndex,
    IndexedItem,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_config(**overrides) -> RecommenderConfig:
    defaults = {
        "enabled": True,
        "refresh_interval_hours": 24,
        "min_vote_count": 5,
        "use_compressed_index": False,
        "min_watched_for_profile": 1,
        "max_recommendations_per_user": 10,
        "because_you_watched_count": 5,
        "owned_weight": 1.5,
        "collaborative_enabled": False,
        "collaborative_weight": 0.3,
        "auto_request_enabled": False,
        "auto_request_max_per_day": 2,
    }
    defaults.update(overrides)
    return RecommenderConfig(**defaults)


def _make_item(tmdb_id: int, title: str = "", media_type: str = "movie", **kw) -> IndexedItem:
    return IndexedItem(
        tmdb_id=tmdb_id,
        imdb_id=kw.get("imdb_id", f"tt{tmdb_id:07d}"),
        title=title or f"Movie {tmdb_id}",
        genres=kw.get("genres", ["Drama"]),
        vote_average=kw.get("vote_average", 7.0),
        release_date=kw.get("release_date", "2020-01-01"),
        poster_path=kw.get("poster_path", f"/poster_{tmdb_id}.jpg"),
        media_type=media_type,
        in_library=kw.get("in_library", False),
        jellyfin_id=kw.get("jellyfin_id", None),
        source=kw.get("source", "als"),
    )


def _random_embedding(seed: int = 0) -> np.ndarray:
    """Generate a random normalized 128-dim vector (ALS embedding dimension)."""
    rng = np.random.RandomState(seed)
    vec = rng.randn(EMBEDDING_DIM).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _encode_vector(vec: np.ndarray) -> str:
    return base64.b64encode(vec.astype(np.float32).tobytes()).decode("ascii")


def _build_fake_index(data_dir: Path, items: list[IndexedItem], vectors: np.ndarray):
    """Build a real FAISS FlatIP index from provided items and vectors."""
    import faiss

    vecs = vectors.copy().astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-8)
    vecs = vecs / norms

    index = faiss.IndexFlatIP(EMBEDDING_DIM)
    index.add(vecs)
    faiss.write_index(index, str(data_dir / "faiss_movie.index"))
    np.save(str(data_dir / "vectors.npy"), vecs)

    rows = []
    for item in items:
        rows.append({
            "tmdb_id": item.tmdb_id,
            "imdb_id": item.imdb_id,
            "title": item.title,
            "genres": item.genres,
            "vote_average": item.vote_average,
            "release_date": item.release_date,
            "poster_path": item.poster_path,
            "media_type": item.media_type,
            "source": item.source,
        })
    with gzip.open(data_dir / "metadata.json.gz", "wt") as f:
        json.dump(rows, f)


def _make_stack_yaml(tmp_path: Path) -> Path:
    """Write a minimal stack.yaml and return the config dir."""
    import yaml
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "stack.yaml").write_text(yaml.dump({
        "version": 1,
        "paths": {"pool": "/p", "scratch": "/s", "appdata": str(tmp_path / "appdata")},
        "services": {
            "qbittorrent": {"enabled": True, "port": 8080, "username": "a", "password": "b"},
            "radarr": {"enabled": True, "port": 7878},
            "sonarr": {"enabled": True, "port": 8989},
            "prowlarr": {"enabled": True, "port": 9696},
            "jellyseerr": {"enabled": True, "port": 5055},
            "jellyfin": {"enabled": True, "port": 8096},
            "pipeline": {"enabled": True, "recommender": {"enabled": True}},
        },
        "proxy": {"enabled": False},
        "download_policy": {"categories": {"radarr": "movies", "sonarr": "tv"}},
        "media_policy": {"movies": {"keep_audio": ["eng"], "keep_subs": ["eng"]}},
        "quality": {"preset": "balanced"},
        "runtime": {"user_id": 1000, "group_id": 1000, "timezone": "UTC"},
        "users": [],
    }))
    return config_dir


# ── EmbeddingIndex unit tests ────────────────────────────────────────


class TestEmbeddingIndex:

    def test_init_creates_data_dir(self, tmp_path: Path):
        d = tmp_path / "subdir" / "recommender"
        idx = EmbeddingIndex(d)
        assert d.exists()
        assert not idx.loaded
        assert idx.item_count == 0

    def test_load_returns_false_when_no_files(self, tmp_path: Path):
        idx = EmbeddingIndex(tmp_path)
        assert idx.load() is False

    def test_load_from_disk(self, tmp_path: Path):
        items = [_make_item(i, f"Movie {i}") for i in range(100)]
        vecs = np.stack([_random_embedding(i) for i in range(100)]).astype(np.float32)
        _build_fake_index(tmp_path, items, vecs)

        idx = EmbeddingIndex(tmp_path)
        assert idx.load() is True
        assert idx.loaded
        assert idx.item_count == 100

    def test_search_returns_sorted_results(self, tmp_path: Path):
        n = 50
        items = [_make_item(i) for i in range(n)]
        vecs = np.stack([_random_embedding(i) for i in range(n)]).astype(np.float32)
        _build_fake_index(tmp_path, items, vecs)

        idx = EmbeddingIndex(tmp_path)
        idx.load()

        query = _random_embedding(0)
        results = idx.search(query, k=5)
        assert len(results) == 5
        assert results[0][0] == 0  # Item 0 most similar to itself
        scores = [s for _, s in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_empty_index(self, tmp_path: Path):
        idx = EmbeddingIndex(tmp_path)
        assert idx.search(_random_embedding(0)) == []

    def test_get_item_valid(self, tmp_path: Path):
        items = [_make_item(42, "Fight Club")]
        vecs = _random_embedding(0).reshape(1, -1)
        _build_fake_index(tmp_path, items, vecs)

        idx = EmbeddingIndex(tmp_path)
        idx.load()

        item = idx.get_item(0)
        assert item is not None
        assert item.tmdb_id == 42
        assert item.title == "Fight Club"

    def test_get_item_out_of_range(self, tmp_path: Path):
        idx = EmbeddingIndex(tmp_path)
        assert idx.get_item(-1) is None
        assert idx.get_item(9999) is None

    def test_get_by_tmdb_id(self, tmp_path: Path):
        items = [_make_item(550, "Fight Club"), _make_item(680, "Pulp Fiction")]
        vecs = np.stack([_random_embedding(i) for i in range(2)]).astype(np.float32)
        _build_fake_index(tmp_path, items, vecs)

        idx = EmbeddingIndex(tmp_path)
        idx.load()

        result = idx.get_by_tmdb_id(550)
        assert result is not None
        assert result[1].title == "Fight Club"
        assert idx.get_by_tmdb_id(99999) is None

    def test_get_vector(self, tmp_path: Path):
        items = [_make_item(1)]
        vec = _random_embedding(0).reshape(1, -1)
        _build_fake_index(tmp_path, items, vec.copy())

        idx = EmbeddingIndex(tmp_path)
        idx.load()

        retrieved = idx.get_vector(0)
        assert retrieved is not None
        assert retrieved.shape == (EMBEDDING_DIM,)
        assert abs(np.dot(retrieved, retrieved) - 1.0) < 0.01

    def test_mark_owned(self, tmp_path: Path):
        items = [_make_item(10), _make_item(20), _make_item(30)]
        vecs = np.stack([_random_embedding(i) for i in range(3)]).astype(np.float32)
        _build_fake_index(tmp_path, items, vecs)

        idx = EmbeddingIndex(tmp_path)
        idx.load()

        marked = idx.mark_owned({10, 30, 99999}, {10: "jf-10", 30: "jf-30"})
        assert marked == 2
        assert idx.get_item(0).in_library is True
        assert idx.get_item(0).jellyfin_id == "jf-10"
        assert idx.get_item(1).in_library is False

    def test_append_projected_items(self, tmp_path: Path):
        items = [_make_item(1, "Original")]
        vecs = _random_embedding(0).reshape(1, -1)
        _build_fake_index(tmp_path, items, vecs)

        idx = EmbeddingIndex(tmp_path)
        idx.load()
        assert idx.item_count == 1

        # Append a projected TV show
        new_items = [_make_item(999, "New Show", media_type="tv", source="projected")]
        new_vecs = _random_embedding(99).reshape(1, -1)
        added = idx.append_projected_items(new_items, new_vecs)
        assert added == 1
        assert idx.item_count == 2
        assert idx.get_by_tmdb_id(999) is not None
        assert idx.get_by_tmdb_id(999)[1].source == "projected"

    def test_append_skips_duplicates(self, tmp_path: Path):
        items = [_make_item(1)]
        vecs = _random_embedding(0).reshape(1, -1)
        _build_fake_index(tmp_path, items, vecs)

        idx = EmbeddingIndex(tmp_path)
        idx.load()

        dup_items = [_make_item(1, "Duplicate")]
        dup_vecs = _random_embedding(1).reshape(1, -1)
        assert idx.append_projected_items(dup_items, dup_vecs) == 0
        assert idx.item_count == 1

    def test_metadata_source_field(self, tmp_path: Path):
        items = [_make_item(1, source="als"), _make_item(2, source="projected")]
        vecs = np.stack([_random_embedding(i) for i in range(2)]).astype(np.float32)
        _build_fake_index(tmp_path, items, vecs)

        idx = EmbeddingIndex(tmp_path)
        idx.load()
        assert idx.get_item(0).source == "als"
        assert idx.get_item(1).source == "projected"


# ── RecommenderEngine unit tests ─────────────────────────────────────


class TestRecommenderEngine:

    @pytest.fixture
    def engine_setup(self, tmp_path: Path):
        config_dir = _make_stack_yaml(tmp_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        from orchestrator.storage import ConfigRepository
        repo = ConfigRepository(config_dir)

        # Build a small 20-item fake index
        items = [_make_item(i * 100, f"Movie {i}") for i in range(20)]
        vecs = np.stack([_random_embedding(i) for i in range(20)]).astype(np.float32)
        _build_fake_index(data_dir, items, vecs)

        engine = RecommenderEngine(repo, data_dir)
        config = repo.load_stack()
        rec_cfg = _make_config()

        return engine, repo, config, rec_cfg, data_dir, items

    def test_get_status_empty(self, engine_setup):
        engine, *_ = engine_setup
        status = engine.get_status()
        assert status["last_run"] is None
        assert status["user_count"] == 0
        assert status["total_recommendations"] == 0
        assert status["index_loaded"] is False

    def test_get_recommendations_no_user(self, engine_setup):
        engine, *_ = engine_setup
        assert engine.get_recommendations("nonexistent") is None

    def test_get_similar_returns_results(self, engine_setup):
        engine, *_, items = engine_setup
        engine._index.load()
        results = engine.get_similar(items[0].tmdb_id, k=5)
        assert len(results) <= 5
        for r in results:
            assert "tmdb_id" in r
            assert "score" in r
            assert r["tmdb_id"] != items[0].tmdb_id

    def test_get_similar_unknown_tmdb(self, engine_setup):
        engine, *_ = engine_setup
        engine._index.load()
        assert engine.get_similar(99999999) == []

    def test_encode_decode_vector_roundtrip(self):
        vec = _random_embedding(42)
        encoded = RecommenderEngine._encode_vector(vec)
        decoded = RecommenderEngine._decode_vector(encoded)
        assert decoded is not None
        np.testing.assert_allclose(vec, decoded, atol=1e-7)

    def test_decode_vector_invalid(self):
        assert RecommenderEngine._decode_vector("not_base64!!!") is None

    def test_format_item(self):
        item = _make_item(550, "Fight Club", genres=["Drama", "Thriller"],
                          vote_average=8.4, in_library=True, jellyfin_id="jf-550")
        formatted = RecommenderEngine._format_item(item, 0.9234)
        assert formatted["tmdb_id"] == 550
        assert formatted["title"] == "Fight Club"
        assert formatted["score"] == 0.9234
        assert formatted["in_library"] is True

    def test_build_taste_vector(self, engine_setup):
        engine, *_, items = engine_setup
        engine._index.load()

        tmdb_ids = [items[i].tmdb_id for i in range(5)]
        vec = engine._build_taste_vector(tmdb_ids)
        assert vec is not None
        assert vec.shape == (EMBEDDING_DIM,)
        assert abs(np.linalg.norm(vec) - 1.0) < 0.01

    def test_build_taste_vector_no_matches(self, engine_setup):
        engine, *_ = engine_setup
        engine._index.load()
        assert engine._build_taste_vector([99999, 88888]) is None

    def test_generate_recommendations_filters_watched(self, engine_setup):
        engine, repo, config, rec_cfg, _, items = engine_setup
        engine._index.load()

        watched_ids = [items[0].tmdb_id, items[1].tmdb_id]
        profile_vec = engine._build_taste_vector(watched_ids)
        assert profile_vec is not None

        state = {
            "user_profiles": {
                "user1": {
                    "username": "testuser",
                    "watched_count": 2,
                    "watched_tmdb_ids": watched_ids,
                    "profile_vector": _encode_vector(profile_vec),
                    "last_fetched": int(time.time()),
                }
            }
        }

        engine._generate_recommendations(rec_cfg, state)
        recs = state.get("recommendations", {}).get("user1")
        assert recs is not None
        rec_tmdb_ids = {r["tmdb_id"] for r in recs["for_you"]}
        assert items[0].tmdb_id not in rec_tmdb_ids
        assert items[1].tmdb_id not in rec_tmdb_ids

    def test_generate_recommendations_respects_min_watched(self, engine_setup):
        engine, *_, items = engine_setup
        engine._index.load()

        rec_cfg = _make_config(min_watched_for_profile=5)
        profile_vec = engine._build_taste_vector([items[0].tmdb_id])

        state = {
            "user_profiles": {
                "user1": {
                    "username": "newbie",
                    "watched_count": 2,
                    "watched_tmdb_ids": [items[0].tmdb_id],
                    "profile_vector": _encode_vector(profile_vec),
                    "last_fetched": int(time.time()),
                }
            }
        }

        engine._generate_recommendations(rec_cfg, state)
        assert "user1" not in state.get("recommendations", {})


class TestRecommenderState:

    @pytest.fixture
    def repo(self, tmp_path: Path):
        config_dir = _make_stack_yaml(tmp_path)
        from orchestrator.storage import ConfigRepository
        return ConfigRepository(config_dir)

    def test_recommender_state_roundtrip(self, repo):
        state = {
            "last_run": 1712000000,
            "index_stats": {"movie_count": 60000, "embedding_source": "als"},
        }
        repo.save_recommender_state(state)
        loaded = repo.load_recommender_state()
        assert loaded["last_run"] == 1712000000
        assert loaded["index_stats"]["embedding_source"] == "als"

    def test_recommender_state_empty_by_default(self, repo):
        assert repo.load_recommender_state() == {}

    def test_recommender_state_isolation(self, repo):
        pipeline = repo.load_pipeline_state()
        pipeline["enrichment"] = {"total_processed": 42}
        repo._save_section("pipeline", pipeline)
        repo.save_recommender_state({"last_run": 123})
        pipeline_after = repo.load_pipeline_state()
        assert pipeline_after.get("enrichment", {}).get("total_processed") == 42
        assert pipeline_after.get("recommender", {}).get("last_run") == 123


class TestAutoRequest:

    def test_prune_auto_request_state(self):
        import datetime
        old_date = (datetime.datetime.now() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
        recent_date = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

        auto_state = {
            "user1": {old_date: 3, recent_date: 1},
            "_all_requested": {},
        }
        RecommenderEngine._prune_auto_request_state(auto_state)
        assert old_date not in auto_state["user1"]
        assert recent_date in auto_state["user1"]

    def test_prune_caps_all_requested(self):
        all_requested = {str(i): {"title": f"M{i}", "timestamp": i} for i in range(600)}
        auto_state = {"_all_requested": all_requested}
        RecommenderEngine._prune_auto_request_state(auto_state)
        assert len(auto_state["_all_requested"]) == 500
        assert "599" in auto_state["_all_requested"]
        assert "0" not in auto_state["_all_requested"]

    def test_get_pending_request_tmdb_ids(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [{"media": {"tmdbId": 550}}, {"media": {"tmdbId": 680}}],
            "pageInfo": {"results": 2},
        }
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        ids = RecommenderEngine._get_pending_request_tmdb_ids(mock_client)
        assert ids == {550, 680}

    def test_submit_jellyseerr_request_success(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_client.post.return_value = mock_response

        assert RecommenderEngine._submit_jellyseerr_request(mock_client, 550, "movie") is True


class TestRecommenderConfig:

    def test_default_config(self):
        cfg = RecommenderConfig()
        assert cfg.enabled is False
        assert cfg.refresh_interval_hours == 24

    def test_config_validation_bounds(self):
        with pytest.raises(Exception):
            RecommenderConfig(refresh_interval_hours=0)
        with pytest.raises(Exception):
            RecommenderConfig(owned_weight=0.5)
        with pytest.raises(Exception):
            RecommenderConfig(owned_weight=6.0)

    def test_config_in_pipeline(self):
        from orchestrator.models import PipelineConfig
        pc = PipelineConfig(enabled=True)
        assert isinstance(pc.recommender, RecommenderConfig)


class TestVectorMath:

    def test_encode_preserves_precision(self):
        vec = np.array([1.23456789, -0.987654321] + [0.0] * 126, dtype=np.float32)
        encoded = RecommenderEngine._encode_vector(vec)
        decoded = RecommenderEngine._decode_vector(encoded)
        np.testing.assert_array_equal(vec, decoded)

    def test_empty_watched_returns_none(self, tmp_path: Path):
        items = [_make_item(1)]
        vecs = _random_embedding(0).reshape(1, -1)
        _build_fake_index(tmp_path, items, vecs)

        config_dir = _make_stack_yaml(tmp_path)
        from orchestrator.storage import ConfigRepository
        repo = ConfigRepository(config_dir)
        engine = RecommenderEngine(repo, tmp_path)
        engine._index.load()
        assert engine._build_taste_vector([]) is None


class TestALSTraining:
    """Tests for the ALS training pipeline helpers."""

    def test_encode_genre_vector(self):
        from orchestrator.pipeline.recommender_train import encode_genre_vector, ALL_GENRES

        vec = encode_genre_vector(["Action", "Comedy"])
        assert vec.shape == (len(ALL_GENRES),)
        assert vec[ALL_GENRES.index("Action")] == 1.0
        assert vec[ALL_GENRES.index("Comedy")] == 1.0
        assert vec[ALL_GENRES.index("Drama")] == 0.0

    def test_encode_genre_unknown(self):
        from orchestrator.pipeline.recommender_train import encode_genre_vector, N_GENRE_FEATURES
        vec = encode_genre_vector(["NonexistentGenre"])
        assert vec.sum() == 0.0
        assert vec.shape == (N_GENRE_FEATURES,)

    def test_load_als_embeddings_roundtrip(self, tmp_path: Path):
        from orchestrator.pipeline.recommender_train import load_als_embeddings, _save_als_embeddings

        embeddings = {100: _random_embedding(0), 200: _random_embedding(1)}
        metadata = {100: {"title": "Movie A", "genres": ["Drama"]},
                    200: {"title": "Movie B", "genres": ["Action"]}}
        _save_als_embeddings(tmp_path, embeddings, metadata)

        vectors, meta, tmdb_to_idx = load_als_embeddings(tmp_path)
        assert vectors.shape == (2, EMBEDDING_DIM)
        assert 100 in tmdb_to_idx
        assert 200 in tmdb_to_idx
        assert meta[tmdb_to_idx[100]]["title"] == "Movie A"


class TestProjectionMLP:
    """Tests for the content projection MLP."""

    def test_projection_mlp_load_missing(self, tmp_path: Path):
        from orchestrator.pipeline.recommender_train import ProjectionMLP
        mlp = ProjectionMLP(tmp_path)
        assert not mlp.loaded
        assert mlp.load() is False

    def test_projection_mlp_project_raises_when_not_loaded(self, tmp_path: Path):
        from orchestrator.pipeline.recommender_train import ProjectionMLP
        mlp = ProjectionMLP(tmp_path)
        with pytest.raises(RuntimeError):
            mlp.project(["Action", "Comedy"])


class TestGetStatus:

    def test_status_with_data(self, tmp_path: Path):
        config_dir = _make_stack_yaml(tmp_path)
        from orchestrator.storage import ConfigRepository
        repo = ConfigRepository(config_dir)
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        repo.save_recommender_state({
            "last_run": 1712000000,
            "last_error": None,
            "index_stats": {"movie_count": 60000, "embedding_source": "als"},
            "user_profiles": {
                "u1": {"username": "alice", "watched_count": 47},
            },
            "recommendations": {
                "u1": {"for_you": [{"tmdb_id": 1}], "because_you_watched": []},
            },
            "auto_requests": {"_all_requested": {}},
        })

        engine = RecommenderEngine(repo, data_dir)
        status = engine.get_status()
        assert status["last_run"] == 1712000000
        assert status["user_count"] == 1
        assert status["total_recommendations"] == 1
        assert status["index_stats"]["embedding_source"] == "als"
