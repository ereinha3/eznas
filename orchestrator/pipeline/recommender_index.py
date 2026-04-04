"""FAISS-based embedding index for the media recommendation engine.

V2: Uses collaborative embeddings learned from MovieLens 25M via implicit ALS
instead of text-based embeddings. Supports cold-start items via a content
projection MLP that maps metadata features into the collaborative space.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np

log = logging.getLogger("recommender")

# ALS embedding dimension (from recommender_train.py)
EMBEDDING_DIM = 128


@dataclass
class IndexedItem:
    """Metadata for a single item in the FAISS index."""

    tmdb_id: int
    imdb_id: str
    title: str
    genres: list[str]
    vote_average: float
    release_date: str
    poster_path: str
    media_type: str  # "movie" or "tv"
    backdrop_path: str = ""
    overview: str = ""
    in_library: bool = False
    jellyfin_id: Optional[str] = None
    source: str = "als"  # "als" = collaborative, "projected" = cold-start MLP


class EmbeddingIndex:
    """Manages the FAISS vector index and metadata lookup.

    Loads pre-trained ALS embeddings for movies in the MovieLens training set,
    and uses a content projection MLP for cold-start items (new movies, TV shows).
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._index: Any = None  # faiss.Index
        self._metadata: list[IndexedItem] = []
        self._tmdb_to_idx: dict[int, int] = {}
        self._vectors: Optional[np.ndarray] = None
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def item_count(self) -> int:
        return len(self._metadata)

    def build_from_als(self) -> dict[str, Any]:
        """Build the FAISS index from pre-trained ALS embeddings.

        Expects als_vectors.npy and als_metadata.json.gz in data_dir
        (created by recommender_train.train_collaborative_embeddings).

        Returns index stats dict.
        """
        import faiss
        from .recommender_train import load_als_embeddings

        log.info("recommender: building index from ALS embeddings...")
        t0 = time.time()

        vectors, als_metadata, tmdb_to_idx = load_als_embeddings(self._data_dir)
        n_items = vectors.shape[0]
        als_dim = vectors.shape[1]

        log.info(
            "recommender: loaded %d ALS embeddings (dim=%d)",
            n_items, als_dim,
        )

        # Build metadata list
        metadata: list[IndexedItem] = []
        idx_map: dict[int, int] = {}

        for meta in als_metadata:
            idx = len(metadata)
            tmdb_id = meta["tmdb_id"]
            idx_map[tmdb_id] = idx
            metadata.append(IndexedItem(
                tmdb_id=tmdb_id,
                imdb_id="",
                title=meta.get("title", ""),
                genres=meta.get("genres", []),
                vote_average=meta.get("vote_average", 0.0),
                release_date=meta.get("release_date", ""),
                poster_path=meta.get("poster_path", ""),
                media_type="movie",
                backdrop_path=meta.get("backdrop_path", ""),
                overview=meta.get("overview", ""),
                source="als",
            ))

        # Build FAISS FlatIP index (exact search — 62K items at 128-dim is fast)
        index = faiss.IndexFlatIP(als_dim)
        index.add(vectors)

        # Save to disk
        faiss.write_index(index, str(self._data_dir / "faiss_movie.index"))
        np.save(str(self._data_dir / "vectors.npy"), vectors)
        self._save_metadata(metadata)

        # Set instance state
        self._index = index
        self._metadata = metadata
        self._tmdb_to_idx = idx_map
        self._vectors = vectors
        self._loaded = True

        elapsed = time.time() - t0
        stats = {
            "movie_count": n_items,
            "tv_count": 0,
            "index_type": "FlatIP",
            "embedding_source": "als",
            "als_dim": als_dim,
        }
        log.info("recommender: index built in %.1fs — %s", elapsed, stats)
        return stats

    def append_projected_items(
        self,
        items: list[IndexedItem],
        vectors: np.ndarray,
    ) -> int:
        """Append cold-start items (projected via MLP) to the index.

        Parameters:
            items: list of IndexedItem with source="projected"
            vectors: (N, 128) float32 array of projected embeddings

        Returns count of items added.
        """
        if not items or vectors.shape[0] == 0:
            return 0
        if not self._loaded:
            log.warning("recommender: cannot append to unloaded index")
            return 0

        import faiss

        # Filter out items already in the index
        new_items = []
        new_vectors = []
        for item, vec in zip(items, vectors):
            if item.tmdb_id not in self._tmdb_to_idx:
                new_items.append(item)
                new_vectors.append(vec)

        if not new_items:
            return 0

        new_vecs = np.stack(new_vectors).astype(np.float32)
        # Ensure normalized
        norms = np.linalg.norm(new_vecs, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-8)
        new_vecs = new_vecs / norms

        # Extend metadata and lookup
        base_idx = len(self._metadata)
        for i, item in enumerate(new_items):
            self._tmdb_to_idx[item.tmdb_id] = base_idx + i
            self._metadata.append(item)

        # Extend vectors and rebuild index
        all_vectors = np.vstack([self._vectors, new_vecs])
        self._vectors = all_vectors

        index = faiss.IndexFlatIP(all_vectors.shape[1])
        index.add(all_vectors)
        self._index = index

        # Save updated state
        faiss.write_index(index, str(self._data_dir / "faiss_movie.index"))
        np.save(str(self._data_dir / "vectors.npy"), all_vectors)
        self._save_metadata(self._metadata)

        log.info(
            "recommender: appended %d projected items, total: %d",
            len(new_items), len(self._metadata),
        )
        return len(new_items)

    def load(self) -> bool:
        """Load a previously saved FAISS index from disk. Returns True on success."""
        import faiss

        index_path = self._data_dir / "faiss_movie.index"
        meta_path = self._data_dir / "metadata.json.gz"

        # Also check legacy path
        if not meta_path.exists():
            meta_path = self._data_dir / "metadata_movies.json.gz"

        if not index_path.exists() or not meta_path.exists():
            return False

        try:
            self._index = faiss.read_index(str(index_path))
            self._metadata = self._load_metadata(meta_path)
            self._tmdb_to_idx = {
                item.tmdb_id: i for i, item in enumerate(self._metadata)
            }
            vectors_path = self._data_dir / "vectors.npy"
            if vectors_path.exists():
                self._vectors = np.load(str(vectors_path))
            self._loaded = True
            log.info(
                "recommender: loaded index (%d items) from disk",
                len(self._metadata),
            )
            return True
        except Exception as exc:
            log.error("recommender: failed to load index: %s", exc)
            return False

    def search(self, query: np.ndarray, k: int = 20) -> list[tuple[int, float]]:
        """Search for k nearest neighbors. Returns list of (metadata_index, score)."""
        if not self._loaded or self._index is None:
            return []

        q = query.reshape(1, -1).astype(np.float32)
        # Normalize query
        norm = np.linalg.norm(q)
        if norm > 0:
            q = q / norm

        distances, indices = self._index.search(q, k)
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0:
                continue
            results.append((int(idx), float(dist)))
        return results

    def get_item(self, idx: int) -> Optional[IndexedItem]:
        if 0 <= idx < len(self._metadata):
            return self._metadata[idx]
        return None

    def get_by_tmdb_id(self, tmdb_id: int) -> Optional[tuple[int, IndexedItem]]:
        idx = self._tmdb_to_idx.get(tmdb_id)
        if idx is None:
            return None
        return idx, self._metadata[idx]

    def get_vector(self, idx: int) -> Optional[np.ndarray]:
        """Get the stored vector for a given metadata index."""
        if self._vectors is not None and 0 <= idx < len(self._vectors):
            return self._vectors[idx]
        if self._index is None:
            return None
        try:
            return self._index.reconstruct(idx)
        except RuntimeError:
            return None

    def mark_owned(
        self,
        tmdb_ids: set[int],
        jellyfin_map: dict[int, str],
    ) -> int:
        """Mark items that exist in the Jellyfin library. Returns count marked."""
        marked = 0
        for tmdb_id in tmdb_ids:
            idx = self._tmdb_to_idx.get(tmdb_id)
            if idx is not None:
                self._metadata[idx].in_library = True
                self._metadata[idx].jellyfin_id = jellyfin_map.get(tmdb_id)
                marked += 1
        return marked

    def _save_metadata(self, metadata: list[IndexedItem]) -> None:
        """Save metadata to compressed JSON."""
        path = self._data_dir / "metadata.json.gz"
        rows = []
        for item in metadata:
            rows.append({
                "tmdb_id": item.tmdb_id,
                "imdb_id": item.imdb_id,
                "title": item.title,
                "genres": item.genres,
                "vote_average": item.vote_average,
                "release_date": item.release_date,
                "poster_path": item.poster_path,
                "backdrop_path": item.backdrop_path,
                "overview": item.overview,
                "media_type": item.media_type,
                "source": item.source,
            })
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(rows, f)

    @staticmethod
    def _load_metadata(path: Path) -> list[IndexedItem]:
        """Load metadata from compressed JSON."""
        with gzip.open(path, "rt", encoding="utf-8") as f:
            rows = json.load(f)
        metadata = []
        for row in rows:
            metadata.append(IndexedItem(
                tmdb_id=row["tmdb_id"],
                imdb_id=row.get("imdb_id", ""),
                title=row.get("title", ""),
                genres=row.get("genres", []),
                vote_average=row.get("vote_average", 0),
                release_date=row.get("release_date", ""),
                poster_path=row.get("poster_path", ""),
                media_type=row.get("media_type", "movie"),
                backdrop_path=row.get("backdrop_path", ""),
                overview=row.get("overview", ""),
                source=row.get("source", "als"),
            ))
        return metadata
