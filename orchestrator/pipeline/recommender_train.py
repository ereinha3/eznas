"""Train collaborative item embeddings from MovieLens 25M using implicit ALS.

Downloads the MovieLens 25M dataset, trains an Alternating Least Squares
model on binarized user-item interactions (confidence-weighted by rating),
extracts item embeddings, and maps them to TMDb IDs.

Optionally trains a content projection MLP that maps movie metadata
(genres, year, plot embedding) to the collaborative embedding space,
enabling cold-start recommendations for items not in MovieLens.

Usage:
    from orchestrator.pipeline.recommender_train import train_collaborative_embeddings
    result = train_collaborative_embeddings(data_dir=Path("/data/recommender"))
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Optional
from urllib.request import urlretrieve

import numpy as np

log = logging.getLogger("recommender")

MOVIELENS_URL = "https://files.grouplens.org/datasets/movielens/ml-25m.zip"
EMBEDDING_DIM = 128
ALS_ITERATIONS = 50


def train_collaborative_embeddings(
    data_dir: Path,
    als_dim: int = EMBEDDING_DIM,
    als_iterations: int = ALS_ITERATIONS,
    min_user_ratings: int = 5,
    min_item_ratings: int = 5,
) -> dict[str, Any]:
    """Train ALS on MovieLens 25M and save item embeddings mapped to TMDb IDs.

    Returns stats dict with training info.
    """
    import implicit
    from scipy.sparse import csr_matrix

    data_dir.mkdir(parents=True, exist_ok=True)
    ml_dir = _ensure_movielens(data_dir)

    # ── Load ratings ─────────────────────────────────────────────────
    log.info("recommender-train: loading MovieLens ratings...")
    t0 = time.time()

    ratings_path = ml_dir / "ratings.csv"
    user_ids: list[int] = []
    item_ids: list[int] = []
    ratings: list[float] = []

    with open(ratings_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            user_ids.append(int(row["userId"]))
            item_ids.append(int(row["movieId"]))
            ratings.append(float(row["rating"]))

    log.info(
        "recommender-train: loaded %d ratings in %.1fs",
        len(ratings), time.time() - t0,
    )

    # ── Build sparse interaction matrix ──────────────────────────────
    # Map to contiguous indices
    unique_users = sorted(set(user_ids))
    unique_items = sorted(set(item_ids))
    user_map = {uid: i for i, uid in enumerate(unique_users)}
    item_map = {iid: i for i, iid in enumerate(unique_items)}
    item_map_rev = {i: iid for iid, i in item_map.items()}

    n_users = len(unique_users)
    n_items = len(unique_items)

    rows = np.array([user_map[u] for u in user_ids], dtype=np.int32)
    cols = np.array([item_map[i] for i in item_ids], dtype=np.int32)

    # Confidence-weighted implicit feedback: confidence = 1 + alpha * rating
    # Higher ratings → stronger positive signal
    alpha = 2.0
    confidence = np.array([1.0 + alpha * r for r in ratings], dtype=np.float32)

    interaction_matrix = csr_matrix(
        (confidence, (rows, cols)),
        shape=(n_users, n_items),
    )

    log.info(
        "recommender-train: interaction matrix: %d users × %d items, %d nnz",
        n_users, n_items, interaction_matrix.nnz,
    )

    # ── Filter sparse users/items ────────────────────────────────────
    # Keep users with >= min_user_ratings and items with >= min_item_ratings
    user_counts = np.diff(interaction_matrix.indptr)
    item_counts = np.array(interaction_matrix.getnnz(axis=0))

    valid_users = user_counts >= min_user_ratings
    valid_items = item_counts >= min_item_ratings

    if not valid_users.all() or not valid_items.all():
        interaction_matrix = interaction_matrix[valid_users][:, valid_items]
        # Update reverse maps
        valid_user_indices = np.where(valid_users)[0]
        valid_item_indices = np.where(valid_items)[0]
        item_map_rev = {
            new_i: item_map_rev[old_i]
            for new_i, old_i in enumerate(valid_item_indices)
        }
        n_users = interaction_matrix.shape[0]
        n_items = interaction_matrix.shape[1]
        log.info(
            "recommender-train: after filtering: %d users × %d items",
            n_users, n_items,
        )

    # ── Train ALS ────────────────────────────────────────────────────
    log.info(
        "recommender-train: training ALS (dim=%d, iterations=%d)...",
        als_dim, als_iterations,
    )
    t1 = time.time()

    model = implicit.als.AlternatingLeastSquares(
        factors=als_dim,
        iterations=als_iterations,
        regularization=0.01,
        random_state=42,
        use_gpu=False,  # CPU is fine for this scale
    )
    model.fit(interaction_matrix)

    elapsed = time.time() - t1
    log.info("recommender-train: ALS training complete in %.1fs", elapsed)

    # ── Extract item embeddings ──────────────────────────────────────
    item_factors = model.item_factors  # (n_items, als_dim)
    if hasattr(item_factors, 'to_numpy'):
        item_factors = item_factors.to_numpy()
    item_factors = np.array(item_factors, dtype=np.float32)

    # L2-normalize for cosine similarity
    norms = np.linalg.norm(item_factors, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-8)
    item_factors = item_factors / norms

    # ── Map to TMDb IDs ──────────────────────────────────────────────
    log.info("recommender-train: mapping to TMDb IDs...")
    links_path = ml_dir / "links.csv"
    ml_to_tmdb: dict[int, int] = {}

    with open(links_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ml_id = int(row["movieId"])
            tmdb_str = row.get("tmdbId", "").strip()
            if tmdb_str:
                try:
                    ml_to_tmdb[ml_id] = int(float(tmdb_str))
                except (ValueError, TypeError):
                    pass

    # Build final embedding dict: tmdb_id → embedding vector
    tmdb_embeddings: dict[int, np.ndarray] = {}
    unmapped = 0
    for internal_idx in range(n_items):
        ml_id = item_map_rev[internal_idx]
        tmdb_id = ml_to_tmdb.get(ml_id)
        if tmdb_id is not None:
            tmdb_embeddings[tmdb_id] = item_factors[internal_idx]
        else:
            unmapped += 1

    log.info(
        "recommender-train: %d items mapped to TMDb IDs, %d unmapped",
        len(tmdb_embeddings), unmapped,
    )

    # ── Load MovieLens movie metadata (for projection training) ──────
    movies_path = ml_dir / "movies.csv"
    ml_metadata: dict[int, dict] = {}  # ml_id → {title, genres}

    with open(movies_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ml_id = int(row["movieId"])
            title = row.get("title", "")
            genres_str = row.get("genres", "")
            genres = genres_str.split("|") if genres_str and genres_str != "(no genres listed)" else []
            ml_metadata[ml_id] = {"title": title, "genres": genres}

    # Build tmdb_id → metadata mapping
    tmdb_metadata: dict[int, dict] = {}
    for ml_id, tmdb_id in ml_to_tmdb.items():
        if tmdb_id in tmdb_embeddings and ml_id in ml_metadata:
            tmdb_metadata[tmdb_id] = ml_metadata[ml_id]

    # ── Save to disk ─────────────────────────────────────────────────
    _save_als_embeddings(data_dir, tmdb_embeddings, tmdb_metadata)

    stats = {
        "total_ratings": len(ratings) if 'ratings' in dir() else 0,
        "n_users": n_users,
        "n_items": n_items,
        "mapped_to_tmdb": len(tmdb_embeddings),
        "unmapped": unmapped,
        "als_dim": als_dim,
        "als_iterations": als_iterations,
        "training_time_s": round(elapsed, 1),
    }
    log.info("recommender-train: complete — %s", stats)
    return stats


def _ensure_movielens(data_dir: Path) -> Path:
    """Download and extract MovieLens 25M if not cached."""
    ml_dir = data_dir / "ml-25m"
    if (ml_dir / "ratings.csv").exists():
        log.info("recommender-train: MovieLens 25M already cached at %s", ml_dir)
        return ml_dir

    zip_path = data_dir / "ml-25m.zip"
    if not zip_path.exists():
        log.info("recommender-train: downloading MovieLens 25M (~250MB)...")
        t0 = time.time()

        def _progress(block, block_size, total):
            pct = min(100, block * block_size * 100 // max(total, 1))
            if block % 500 == 0:
                log.info("recommender-train: download %d%%", pct)

        urlretrieve(MOVIELENS_URL, str(zip_path), _progress)
        log.info(
            "recommender-train: download complete (%.1fs)",
            time.time() - t0,
        )

    log.info("recommender-train: extracting...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(str(data_dir))

    log.info("recommender-train: extracted to %s", ml_dir)
    return ml_dir


def _save_als_embeddings(
    data_dir: Path,
    tmdb_embeddings: dict[int, np.ndarray],
    tmdb_metadata: dict[int, dict],
) -> None:
    """Save ALS embeddings and metadata to disk."""
    # Save embeddings as numpy arrays
    tmdb_ids = sorted(tmdb_embeddings.keys())
    vectors = np.stack([tmdb_embeddings[tid] for tid in tmdb_ids]).astype(np.float32)

    np.save(str(data_dir / "als_vectors.npy"), vectors)

    # Save TMDb ID index and metadata
    meta_rows = []
    for i, tid in enumerate(tmdb_ids):
        meta = tmdb_metadata.get(tid, {})
        meta_rows.append({
            "tmdb_id": tid,
            "title": meta.get("title", ""),
            "genres": meta.get("genres", []),
            "index": i,
        })

    with gzip.open(data_dir / "als_metadata.json.gz", "wt", encoding="utf-8") as f:
        json.dump(meta_rows, f)

    log.info(
        "recommender-train: saved %d ALS embeddings (%.1f MB) + metadata",
        len(tmdb_ids),
        vectors.nbytes / 1024 / 1024,
    )


def load_als_embeddings(
    data_dir: Path,
) -> tuple[np.ndarray, list[dict], dict[int, int]]:
    """Load pre-trained ALS embeddings from disk.

    Returns:
        vectors: (N, 128) float32 array of item embeddings
        metadata: list of {tmdb_id, title, genres, index} dicts
        tmdb_to_idx: mapping from TMDb ID to vector index
    """
    vectors = np.load(str(data_dir / "als_vectors.npy"))
    with gzip.open(data_dir / "als_metadata.json.gz", "rt", encoding="utf-8") as f:
        metadata = json.load(f)
    tmdb_to_idx = {m["tmdb_id"]: m["index"] for m in metadata}
    return vectors, metadata, tmdb_to_idx


# ── TMDb Metadata Enrichment ─────────────────────────────────────────


def enrich_tmdb_metadata(
    data_dir: Path,
    tmdb_api_key: str,
    jellyfin_items: Optional[list[dict]] = None,
    rate_limit: float = 40.0,
) -> dict[str, Any]:
    """Enrich ALS metadata with TMDb poster paths, overviews, ratings, and dates.

    Two-pass approach:
    1. For items in the Jellyfin library: use Jellyfin-provided metadata (free)
    2. For remaining items: fetch from TMDb API (rate-limited)

    Enrichment is incremental — skips items already enriched in the cache.

    Args:
        data_dir: path to the recommender data directory
        tmdb_api_key: TMDb API v3 key
        jellyfin_items: optional list of Jellyfin library items with ProviderIds
            and ImageTags (from get_library_items_with_providers with extra fields)
        rate_limit: max TMDb API requests per second

    Returns stats dict.
    """
    import httpx

    _, metadata, _ = load_als_embeddings(data_dir)

    # Load existing enrichment cache
    cache_path = data_dir / "tmdb_enrichment_cache.json.gz"
    cache: dict[str, dict] = {}
    if cache_path.exists():
        with gzip.open(cache_path, "rt", encoding="utf-8") as f:
            cache = json.load(f)
        log.info("recommender-train: loaded %d cached TMDb entries", len(cache))

    # Build set of TMDb IDs that need enrichment
    all_tmdb_ids = {m["tmdb_id"] for m in metadata}
    already_enriched = {int(k) for k in cache.keys()}
    needs_enrichment = all_tmdb_ids - already_enriched

    # ── Pass 1: Enrich from Jellyfin library items (free, no API calls) ──
    jf_enriched = 0
    if jellyfin_items:
        for item in jellyfin_items:
            providers = item.get("ProviderIds", {})
            tmdb_str = providers.get("Tmdb", "")
            if not tmdb_str:
                continue
            try:
                tmdb_id = int(tmdb_str)
            except (ValueError, TypeError):
                continue

            if tmdb_id not in needs_enrichment:
                continue

            # Extract metadata from Jellyfin item
            name = item.get("Name", "")
            overview = item.get("Overview", "")
            year = item.get("ProductionYear", "")
            rating = item.get("CommunityRating", 0)
            premiere = item.get("PremiereDate", "")
            # Jellyfin stores poster as ImageTags.Primary → construct path
            # But we need TMDb poster path format, not Jellyfin's
            # We'll get poster from TMDb API in pass 2 if missing
            image_tags = item.get("ImageTags", {})

            cache[str(tmdb_id)] = {
                "poster_path": "",  # Will be filled by TMDb API if available
                "overview": overview,
                "vote_average": float(rating) if rating else 0.0,
                "release_date": premiere[:10] if premiere else str(year),
                "genre_ids": [],
                "source": "jellyfin",
            }
            needs_enrichment.discard(tmdb_id)
            jf_enriched += 1

    log.info("recommender-train: enriched %d items from Jellyfin library", jf_enriched)

    # ── Pass 2: Fetch remaining items from TMDb API ──────────────────
    if not tmdb_api_key:
        log.warning("recommender-train: no TMDb API key, skipping API enrichment (%d items)", len(needs_enrichment))
        _save_enrichment_cache(data_dir, cache)
        _apply_enrichment_to_metadata(data_dir, cache)
        return {"jellyfin_enriched": jf_enriched, "tmdb_enriched": 0, "total_cached": len(cache)}

    to_fetch = sorted(needs_enrichment)
    log.info("recommender-train: fetching TMDb metadata for %d items (rate=%.0f/s)...", len(to_fetch), rate_limit)

    tmdb_enriched = 0
    errors = 0
    min_interval = 1.0 / rate_limit
    t0 = time.time()

    with httpx.Client(
        base_url="https://api.themoviedb.org/3",
        params={"api_key": tmdb_api_key},
        timeout=httpx.Timeout(10.0, connect=5.0),
    ) as client:
        for i, tmdb_id in enumerate(to_fetch):
            try:
                response = client.get(f"/movie/{tmdb_id}")
                if response.status_code == 200:
                    data = response.json()
                    cache[str(tmdb_id)] = {
                        "poster_path": data.get("poster_path", ""),
                        "overview": data.get("overview", ""),
                        "vote_average": float(data.get("vote_average", 0)),
                        "release_date": data.get("release_date", ""),
                        "genre_ids": [g.get("id", 0) for g in data.get("genres", [])],
                        "source": "tmdb",
                    }
                    tmdb_enriched += 1
                elif response.status_code == 404:
                    # Movie not found on TMDb — cache empty to avoid re-fetching
                    cache[str(tmdb_id)] = {
                        "poster_path": "", "overview": "", "vote_average": 0,
                        "release_date": "", "genre_ids": [], "source": "not_found",
                    }
                else:
                    errors += 1
            except Exception as exc:
                errors += 1
                if errors <= 5:
                    log.warning("recommender-train: TMDb fetch error for %d: %s", tmdb_id, exc)

            # Rate limiting
            time.sleep(min_interval)

            # Progress logging
            if (i + 1) % 1000 == 0:
                elapsed = time.time() - t0
                rate = (i + 1) / max(elapsed, 1)
                remaining = (len(to_fetch) - i - 1) / max(rate, 0.1)
                log.info(
                    "recommender-train: TMDb enrichment %d/%d (%.0f/s, ~%.0fm remaining)",
                    i + 1, len(to_fetch), rate, remaining / 60,
                )

            # Save cache periodically (every 5000 items)
            if (i + 1) % 5000 == 0:
                _save_enrichment_cache(data_dir, cache)

    elapsed = time.time() - t0
    log.info(
        "recommender-train: TMDb enrichment complete: %d fetched, %d errors in %.1fs",
        tmdb_enriched, errors, elapsed,
    )

    # ── Save and apply ───────────────────────────────────────────────
    _save_enrichment_cache(data_dir, cache)
    _apply_enrichment_to_metadata(data_dir, cache)

    return {
        "jellyfin_enriched": jf_enriched,
        "tmdb_enriched": tmdb_enriched,
        "errors": errors,
        "total_cached": len(cache),
        "elapsed_s": round(elapsed, 1),
    }


def _save_enrichment_cache(data_dir: Path, cache: dict) -> None:
    """Save enrichment cache to compressed JSON."""
    cache_path = data_dir / "tmdb_enrichment_cache.json.gz"
    with gzip.open(cache_path, "wt", encoding="utf-8") as f:
        json.dump(cache, f)


def _apply_enrichment_to_metadata(data_dir: Path, cache: dict) -> None:
    """Apply enrichment data to the ALS metadata file."""
    meta_path = data_dir / "als_metadata.json.gz"
    if not meta_path.exists():
        return

    with gzip.open(meta_path, "rt", encoding="utf-8") as f:
        metadata = json.load(f)

    enriched_count = 0
    for meta in metadata:
        tmdb_id = str(meta["tmdb_id"])
        enrichment = cache.get(tmdb_id)
        if enrichment:
            meta["poster_path"] = enrichment.get("poster_path", "")
            meta["overview"] = enrichment.get("overview", "")
            meta["vote_average"] = enrichment.get("vote_average", 0)
            meta["release_date"] = enrichment.get("release_date", "")
            meta["genre_ids"] = enrichment.get("genre_ids", [])
            enriched_count += 1

    with gzip.open(meta_path, "wt", encoding="utf-8") as f:
        json.dump(metadata, f)

    log.info("recommender-train: applied enrichment to %d/%d metadata entries", enriched_count, len(metadata))


def load_enrichment_cache(data_dir: Path) -> dict[str, dict]:
    """Load the TMDb enrichment cache. Returns empty dict if not found."""
    cache_path = data_dir / "tmdb_enrichment_cache.json.gz"
    if not cache_path.exists():
        return {}
    with gzip.open(cache_path, "rt", encoding="utf-8") as f:
        return json.load(f)


# ── Content Projection MLP ───────────────────────────────────────────


# All 20 MovieLens genre labels
ALL_GENRES = [
    "Action", "Adventure", "Animation", "Children", "Comedy", "Crime",
    "Documentary", "Drama", "Fantasy", "Film-Noir", "Horror", "IMAX",
    "Musical", "Mystery", "Romance", "Sci-Fi", "Thriller", "War", "Western",
    "(no genres listed)",
]
GENRE_TO_IDX = {g: i for i, g in enumerate(ALL_GENRES)}
N_GENRE_FEATURES = len(ALL_GENRES)


def encode_genre_vector(genres: list[str]) -> np.ndarray:
    """Multi-hot encode a list of genre strings."""
    vec = np.zeros(N_GENRE_FEATURES, dtype=np.float32)
    for g in genres:
        idx = GENRE_TO_IDX.get(g)
        if idx is not None:
            vec[idx] = 1.0
    return vec


def train_projection_mlp(
    data_dir: Path,
    tmdb_api_key: Optional[str] = None,
    plot_encoder_name: str = "all-MiniLM-L6-v2",
    hidden_dims: tuple[int, ...] = (512, 256),
    epochs: int = 100,
    batch_size: int = 1024,
    lr: float = 1e-3,
) -> dict[str, Any]:
    """Train a content projection MLP: metadata features → ALS embedding space.

    Uses MovieLens genre data (always available) and optionally TMDb metadata
    (plot summaries, year, runtime, scores) if a TMDb API key is provided
    or if metadata has been previously cached.

    Returns training stats.
    """
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    vectors, metadata, tmdb_to_idx = load_als_embeddings(data_dir)
    als_dim = vectors.shape[1]

    log.info("recommender-train: preparing projection training data...")
    t0 = time.time()

    # ── Build feature matrix ─────────────────────────────────────────
    # Always available: MovieLens genres (from als_metadata.json.gz)
    # Optional: plot embeddings, year, runtime, TMDb scores

    # Check for cached plot embeddings
    plot_emb_path = data_dir / "plot_embeddings.npy"
    plot_tmdb_ids_path = data_dir / "plot_tmdb_ids.json"
    plot_embeddings: Optional[dict[int, np.ndarray]] = None

    if plot_emb_path.exists() and plot_tmdb_ids_path.exists():
        log.info("recommender-train: loading cached plot embeddings...")
        plot_vecs = np.load(str(plot_emb_path))
        with open(plot_tmdb_ids_path) as f:
            plot_ids = json.load(f)
        plot_embeddings = {tid: plot_vecs[i] for i, tid in enumerate(plot_ids)}
        plot_dim = plot_vecs.shape[1]
        log.info("recommender-train: loaded %d cached plot embeddings (dim=%d)", len(plot_embeddings), plot_dim)
    else:
        plot_dim = 0

    # Determine feature dimension
    feature_dim = N_GENRE_FEATURES  # 20 genre features always available
    if plot_embeddings:
        feature_dim += plot_dim

    log.info("recommender-train: feature dimension = %d (genres=%d, plot=%d)",
             feature_dim, N_GENRE_FEATURES, plot_dim)

    # Build training tensors
    features_list = []
    targets_list = []

    for meta in metadata:
        tmdb_id = meta["tmdb_id"]
        idx = meta["index"]

        # Genre features (always available)
        genre_vec = encode_genre_vector(meta.get("genres", []))

        # Combine features
        if plot_embeddings and tmdb_id in plot_embeddings:
            feat = np.concatenate([genre_vec, plot_embeddings[tmdb_id]])
        elif plot_embeddings:
            # Item has no plot embedding — skip or pad with zeros
            feat = np.concatenate([genre_vec, np.zeros(plot_dim, dtype=np.float32)])
        else:
            feat = genre_vec

        features_list.append(feat)
        targets_list.append(vectors[idx])

    X = torch.tensor(np.stack(features_list), dtype=torch.float32)
    Y = torch.tensor(np.stack(targets_list), dtype=torch.float32)

    log.info("recommender-train: training data: %d items, %d features → %d targets",
             X.shape[0], X.shape[1], Y.shape[1])

    # ── Define MLP ───────────────────────────────────────────────────
    layers = []
    in_dim = feature_dim
    for h_dim in hidden_dims:
        layers.append(nn.Linear(in_dim, h_dim))
        layers.append(nn.ReLU())
        layers.append(nn.Dropout(0.3))
        in_dim = h_dim
    layers.append(nn.Linear(in_dim, als_dim))
    model = nn.Sequential(*layers)

    # ── Train ────────────────────────────────────────────────────────
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    loss_fn = nn.MSELoss()
    dataset = TensorDataset(X, Y)
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

    log.info("recommender-train: training projection MLP (epochs=%d)...", epochs)
    t1 = time.time()

    model.train()
    for epoch in range(epochs):
        epoch_loss = 0.0
        n_batches = 0
        for batch_x, batch_y in loader:
            optimizer.zero_grad()
            pred = batch_x  # forward through model
            pred = model(batch_x)
            # Normalize predictions for cosine similarity
            pred = pred / (pred.norm(dim=1, keepdim=True) + 1e-8)
            loss = loss_fn(pred, batch_y)
            loss.backward()
            optimizer.step()
            epoch_loss += loss.item()
            n_batches += 1

        if (epoch + 1) % 20 == 0 or epoch == 0:
            avg_loss = epoch_loss / max(n_batches, 1)
            log.info("recommender-train: epoch %d/%d, loss=%.6f", epoch + 1, epochs, avg_loss)

    elapsed = time.time() - t1
    log.info("recommender-train: MLP training complete in %.1fs", elapsed)

    # ── Save model ───────────────────────────────────────────────────
    model.eval()
    model_path = data_dir / "projection_mlp.pt"
    torch.save({
        "state_dict": model.state_dict(),
        "feature_dim": feature_dim,
        "als_dim": als_dim,
        "hidden_dims": hidden_dims,
        "genre_count": N_GENRE_FEATURES,
        "plot_dim": plot_dim,
    }, str(model_path))

    log.info("recommender-train: saved projection MLP to %s", model_path)

    stats = {
        "training_items": X.shape[0],
        "feature_dim": feature_dim,
        "als_dim": als_dim,
        "epochs": epochs,
        "final_loss": round(epoch_loss / max(n_batches, 1), 6),
        "training_time_s": round(elapsed, 1),
    }
    return stats


def encode_plots_batch(
    data_dir: Path,
    tmdb_metadata_path: Optional[Path] = None,
    encoder_name: str = "all-MiniLM-L6-v2",
) -> int:
    """Encode movie plot summaries with a sentence transformer and cache to disk.

    If tmdb_metadata_path is provided, reads plot summaries from it.
    Otherwise, uses MovieLens titles (less useful but always available).

    Returns number of plots encoded.
    """
    from sentence_transformers import SentenceTransformer

    vectors, metadata, tmdb_to_idx = load_als_embeddings(data_dir)

    # Load TMDb metadata if available (has plot summaries)
    tmdb_plots: dict[int, str] = {}
    if tmdb_metadata_path and tmdb_metadata_path.exists():
        with open(tmdb_metadata_path) as f:
            tmdb_data = json.load(f)
        for item in tmdb_data:
            tid = item.get("tmdb_id") or item.get("id")
            overview = item.get("overview", "")
            if tid and overview:
                tmdb_plots[int(tid)] = overview

    # Build text descriptions
    tmdb_ids = []
    texts = []
    for meta in metadata:
        tmdb_id = meta["tmdb_id"]
        genres = meta.get("genres", [])
        title = meta.get("title", "")

        if tmdb_id in tmdb_plots:
            text = f"{title}. {', '.join(genres)}. {tmdb_plots[tmdb_id]}"
        else:
            # Fall back to title + genres
            text = f"{title}. {', '.join(genres)}"

        tmdb_ids.append(tmdb_id)
        texts.append(text)

    if not texts:
        return 0

    log.info("recommender-train: encoding %d plots with %s...", len(texts), encoder_name)
    t0 = time.time()

    model = SentenceTransformer(encoder_name)
    embeddings = model.encode(texts, show_progress_bar=True, batch_size=256)
    embeddings = np.array(embeddings, dtype=np.float32)

    elapsed = time.time() - t0
    log.info("recommender-train: encoded %d plots in %.1fs", len(texts), elapsed)

    # Save
    np.save(str(data_dir / "plot_embeddings.npy"), embeddings)
    with open(data_dir / "plot_tmdb_ids.json", "w") as f:
        json.dump(tmdb_ids, f)

    return len(texts)


class ProjectionMLP:
    """Loads and runs the content projection MLP for cold-start inference."""

    def __init__(self, data_dir: Path) -> None:
        self._model = None
        self._config: dict[str, Any] = {}
        self._data_dir = data_dir
        self._encoder = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> bool:
        """Load the saved MLP model. Returns True on success."""
        import torch
        import torch.nn as nn

        model_path = self._data_dir / "projection_mlp.pt"
        if not model_path.exists():
            return False

        try:
            checkpoint = torch.load(str(model_path), map_location="cpu", weights_only=True)
            self._config = {
                "feature_dim": checkpoint["feature_dim"],
                "als_dim": checkpoint["als_dim"],
                "hidden_dims": checkpoint["hidden_dims"],
                "genre_count": checkpoint["genre_count"],
                "plot_dim": checkpoint.get("plot_dim", 0),
            }

            # Rebuild model architecture
            layers = []
            in_dim = self._config["feature_dim"]
            for h_dim in self._config["hidden_dims"]:
                layers.append(nn.Linear(in_dim, h_dim))
                layers.append(nn.ReLU())
                layers.append(nn.Dropout(0.3))
                in_dim = h_dim
            layers.append(nn.Linear(in_dim, self._config["als_dim"]))
            model = nn.Sequential(*layers)
            model.load_state_dict(checkpoint["state_dict"])
            model.eval()
            self._model = model
            log.info("recommender: loaded projection MLP (feature_dim=%d)", self._config["feature_dim"])
            return True
        except Exception as exc:
            log.error("recommender: failed to load projection MLP: %s", exc)
            return False

    def project(
        self,
        genres: list[str],
        plot_embedding: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Project metadata features into the ALS embedding space.

        Returns a normalized 128-dim vector.
        """
        import torch

        if self._model is None:
            raise RuntimeError("Projection MLP not loaded")

        # Build feature vector
        genre_vec = encode_genre_vector(genres)

        if self._config.get("plot_dim", 0) > 0 and plot_embedding is not None:
            feat = np.concatenate([genre_vec, plot_embedding])
        elif self._config.get("plot_dim", 0) > 0:
            feat = np.concatenate([genre_vec, np.zeros(self._config["plot_dim"], dtype=np.float32)])
        else:
            feat = genre_vec

        x = torch.tensor(feat, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            pred = self._model(x)
            pred = pred / (pred.norm(dim=1, keepdim=True) + 1e-8)

        return pred.squeeze(0).numpy()

    def project_batch(
        self,
        genre_lists: list[list[str]],
        plot_embeddings: Optional[np.ndarray] = None,
    ) -> np.ndarray:
        """Project a batch of items. Returns (N, 128) array."""
        import torch

        if self._model is None:
            raise RuntimeError("Projection MLP not loaded")

        features = []
        for i, genres in enumerate(genre_lists):
            genre_vec = encode_genre_vector(genres)
            if self._config.get("plot_dim", 0) > 0 and plot_embeddings is not None:
                feat = np.concatenate([genre_vec, plot_embeddings[i]])
            elif self._config.get("plot_dim", 0) > 0:
                feat = np.concatenate([genre_vec, np.zeros(self._config["plot_dim"], dtype=np.float32)])
            else:
                feat = genre_vec
            features.append(feat)

        X = torch.tensor(np.stack(features), dtype=torch.float32)
        with torch.no_grad():
            pred = self._model(X)
            pred = pred / (pred.norm(dim=1, keepdim=True) + 1e-8)

        return pred.numpy()
