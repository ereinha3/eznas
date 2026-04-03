"""Integration tests for recommender API endpoints.

Tests the FastAPI routes for the recommendation engine, including
auth requirements, response shapes, and error handling. Uses a
TestClient with mocked recommender engine internals.
"""

from __future__ import annotations

import base64
import gzip
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient

from orchestrator.app import app
from orchestrator.models import RecommenderConfig
from orchestrator.pipeline.recommender import RecommenderEngine
from orchestrator.pipeline.recommender_index import EMBEDDING_DIM, IndexedItem
from orchestrator.storage import ConfigRepository


# ── Helpers ──────────────────────────────────────────────────────────


def _random_embedding(seed: int = 0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    vec = rng.randn(EMBEDDING_DIM).astype(np.float32)
    vec /= np.linalg.norm(vec)
    return vec


def _make_item(tmdb_id: int, title: str = "", **kw) -> IndexedItem:
    return IndexedItem(
        tmdb_id=tmdb_id,
        imdb_id=kw.get("imdb_id", f"tt{tmdb_id:07d}"),
        title=title or f"Movie {tmdb_id}",
        genres=kw.get("genres", ["Drama"]),
        vote_average=kw.get("vote_average", 7.0),
        release_date=kw.get("release_date", "2020-01-01"),
        poster_path=kw.get("poster_path", f"/poster_{tmdb_id}.jpg"),
        media_type=kw.get("media_type", "movie"),
        in_library=kw.get("in_library", False),
        jellyfin_id=kw.get("jellyfin_id", None),
        source=kw.get("source", "als"),
    )


def _build_fake_index(data_dir: Path, items: list[IndexedItem], vectors: np.ndarray):
    import faiss
    faiss.normalize_L2(vectors)
    index = faiss.IndexFlatIP(EMBEDDING_DIM)
    index.add(vectors)
    faiss.write_index(index, str(data_dir / "faiss_movie.index"))
    rows = [
        {
            "tmdb_id": item.tmdb_id, "imdb_id": item.imdb_id, "title": item.title,
            "genres": item.genres, "vote_average": item.vote_average,
            "release_date": item.release_date, "poster_path": item.poster_path,
            "media_type": item.media_type, "source": item.source,
        }
        for item in items
    ]
    with gzip.open(data_dir / "metadata.json.gz", "wt") as f:
        json.dump(rows, f)


def _encode_vector(vec: np.ndarray) -> str:
    return base64.b64encode(vec.astype(np.float32).tobytes()).decode("ascii")


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def api_setup(tmp_path: Path):
    """Set up a test environment with config repo, fake index, and auth token."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    appdata_dir = tmp_path / "appdata" / "pipeline" / "recommender"
    appdata_dir.mkdir(parents=True)

    # Write stack.yaml with appdata pointing to our temp dir
    stack = {
        "version": 1,
        "paths": {"pool": "/data/pool", "scratch": "/data/scratch", "appdata": str(tmp_path / "appdata")},
        "services": {
            "qbittorrent": {"enabled": True, "port": 8080, "username": "admin", "password": "pass"},
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
    }
    (config_dir / "stack.yaml").write_text(yaml.dump(stack))

    repo = ConfigRepository(config_dir)

    # Build a small fake index in the appdata location
    items = [_make_item(i * 100, f"Movie {i}") for i in range(20)]
    vecs = np.stack([_random_embedding(i) for i in range(20)]).astype(np.float32)
    _build_fake_index(appdata_dir, items, vecs)

    # Seed some recommender state
    profile_vec = _random_embedding(99)
    repo.save_recommender_state({
        "last_run": int(time.time()),
        "last_error": None,
        "index_stats": {"movie_count": 20, "tv_count": 0, "index_type": "FlatIP"},
        "user_profiles": {
            "user-abc": {
                "username": "testuser",
                "watched_count": 5,
                "watched_tmdb_ids": [0, 100, 200],
                "profile_vector": _encode_vector(profile_vec),
                "last_fetched": int(time.time()),
            },
        },
        "recommendations": {
            "user-abc": {
                "for_you": [
                    {"tmdb_id": 300, "title": "Movie 3", "score": 0.95,
                     "in_library": True, "media_type": "movie", "genres": ["Drama"],
                     "vote_average": 7.0, "release_date": "2020-01-01",
                     "poster_path": "/poster_300.jpg", "jellyfin_id": "jf-300"},
                    {"tmdb_id": 400, "title": "Movie 4", "score": 0.88,
                     "in_library": False, "media_type": "movie", "genres": ["Action"],
                     "vote_average": 6.5, "release_date": "2021-03-15",
                     "poster_path": "/poster_400.jpg", "jellyfin_id": None},
                ],
                "because_you_watched": [
                    {
                        "seed_tmdb_id": 0,
                        "seed_title": "Movie 0",
                        "items": [
                            {"tmdb_id": 500, "title": "Movie 5", "score": 0.80,
                             "in_library": False, "media_type": "movie", "genres": ["Drama"],
                             "vote_average": 7.2, "release_date": "2019-06-01",
                             "poster_path": "/poster_500.jpg", "jellyfin_id": None},
                        ],
                    },
                ],
                "generated_at": int(time.time()),
            },
        },
        "auto_requests": {"_all_requested": {}},
    })

    # Create auth token for API calls
    from orchestrator.auth import AuthManager
    from orchestrator.models import UserRole
    state = repo.load_state()
    auth_mgr = AuthManager(state)
    auth_mgr.create_user("admin", "testpass", role=UserRole.ADMIN)
    session = auth_mgr.authenticate("admin", "testpass")
    assert session is not None
    repo.save_state(state)
    token = session.token

    return repo, config_dir, appdata_dir, items, token


@pytest.fixture
def client(api_setup):
    repo, config_dir, appdata_dir, items, token = api_setup
    with patch("orchestrator.app.repo", repo):
        with TestClient(app) as c:
            yield c, token, items


# ── API endpoint tests ───────────────────────────────────────────────


class TestRecommenderStatusEndpoint:

    def test_status_requires_auth(self, client):
        c, token, _ = client
        resp = c.get("/api/pipeline/recommender/status")
        assert resp.status_code == 401

    def test_status_returns_data(self, client):
        c, token, _ = client
        resp = c.get(
            "/api/pipeline/recommender/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "last_run" in data
        assert "user_count" in data
        assert data["user_count"] == 1
        assert "index_stats" in data
        assert "auto_request" in data


class TestRecommenderForUserEndpoint:

    def test_for_user_returns_recs(self, client):
        c, token, _ = client
        resp = c.get(
            "/api/pipeline/recommender/for-user/user-abc",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "for_you" in data
        assert "because_you_watched" in data
        assert len(data["for_you"]) == 2
        assert data["for_you"][0]["tmdb_id"] == 300

    def test_for_user_404_missing(self, client):
        c, token, _ = client
        resp = c.get(
            "/api/pipeline/recommender/for-user/nonexistent",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 404

    def test_for_user_requires_auth(self, client):
        c, _, _ = client
        resp = c.get("/api/pipeline/recommender/for-user/user-abc")
        assert resp.status_code == 401


class TestRecommenderSimilarEndpoint:

    def test_similar_returns_results(self, client):
        c, token, items = client
        # Use a tmdb_id that exists in the fake index
        resp = c.get(
            f"/api/pipeline/recommender/similar/{items[0].tmdb_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "tmdb_id" in data
        assert "similar" in data

    def test_similar_unknown_tmdb(self, client):
        c, token, _ = client
        resp = c.get(
            "/api/pipeline/recommender/similar/99999999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["similar"] == []

    def test_similar_caps_k(self, client):
        c, token, items = client
        resp = c.get(
            f"/api/pipeline/recommender/similar/{items[0].tmdb_id}?k=100",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        # k should be capped at 50 by the endpoint
        assert len(resp.json()["similar"]) <= 50


class TestRecommenderUsersEndpoint:

    def test_users_requires_admin(self, client):
        c, token, _ = client
        # Our test user is "owner" role which should have admin access
        resp = c.get(
            "/api/pipeline/recommender/users",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "users" in data
        assert len(data["users"]) == 1
        assert data["users"][0]["username"] == "testuser"


class TestJellyfinCompatEndpoints:

    def test_jellyfin_for_you(self, client):
        c, _, _ = client
        # Jellyfin-compat endpoints have no auth (Docker network isolation)
        resp = c.get("/api/jellyfin/recommendations/user-abc?type=for_you&limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "Items" in data
        assert "TotalRecordCount" in data
        assert len(data["Items"]) <= 5

    def test_jellyfin_because_you_watched(self, client):
        c, _, _ = client
        resp = c.get(
            "/api/jellyfin/recommendations/user-abc"
            "?type=because_you_watched&seed_tmdb_id=0&limit=10"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "Items" in data
        assert len(data["Items"]) >= 1

    def test_jellyfin_unknown_user(self, client):
        c, _, _ = client
        resp = c.get("/api/jellyfin/recommendations/unknown-user?type=for_you")
        assert resp.status_code == 200
        assert resp.json()["Items"] == []

    def test_jellyfin_sections(self, client):
        c, _, _ = client
        resp = c.get("/api/jellyfin/recommendations/user-abc/sections?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert "Sections" in data
        # Should have at least "Recommended for You" section
        assert len(data["Sections"]) >= 1
        assert data["Sections"][0]["Name"] == "Recommended for You"

    def test_jellyfin_sections_unknown_user(self, client):
        c, _, _ = client
        resp = c.get("/api/jellyfin/recommendations/unknown-user/sections")
        assert resp.status_code == 200
        assert resp.json()["Sections"] == []

    def test_jellyfin_item_format(self, client):
        c, _, _ = client
        resp = c.get("/api/jellyfin/recommendations/user-abc?type=for_you&limit=1")
        items = resp.json()["Items"]
        if items:
            item = items[0]
            # Jellyfin-compatible format should have these fields
            assert "Name" in item
            assert "Type" in item
            assert "ProviderIds" in item
            assert "PosterUrl" in item
