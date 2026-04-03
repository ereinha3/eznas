#!/usr/bin/env python3
"""Live test: train ALS on MovieLens 25M and generate recommendations.

Downloads MovieLens 25M (~250MB), trains implicit ALS (~5 min),
trains projection MLP, then runs a full recommendation cycle against
live Jellyfin/Sonarr services.

Usage:
    sudo .venv/bin/python3 test_recommender_live.py
"""

import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ.setdefault("ORCH_ROOT", str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("test_recommender")

from orchestrator.storage import ConfigRepository
from orchestrator.pipeline.recommender import RecommenderEngine
from orchestrator.pipeline.recommender_train import (
    train_collaborative_embeddings,
    train_projection_mlp,
)


def main():
    orch_root = Path(os.environ["ORCH_ROOT"])
    repo = ConfigRepository(orch_root)
    config = repo.load_stack()
    rec_cfg = config.services.pipeline.recommender

    appdata = config.paths.appdata
    data_dir = Path(appdata) / "pipeline" / "recommender"
    data_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("RECOMMENDER V2 LIVE TEST — Collaborative Embeddings")
    log.info("  Data dir: %s", data_dir)
    log.info("=" * 60)

    # ── Step 1: Train ALS on MovieLens 25M ───────────────────────────
    als_path = data_dir / "als_vectors.npy"
    if als_path.exists():
        log.info("\nSTEP 1: ALS embeddings already exist, skipping training")
        import numpy as np
        vecs = np.load(str(als_path))
        log.info("  Loaded %d embeddings (dim=%d)", vecs.shape[0], vecs.shape[1])
    else:
        log.info("\nSTEP 1: Training ALS on MovieLens 25M...")
        t0 = time.time()
        stats = train_collaborative_embeddings(data_dir=data_dir)
        log.info("  Training complete in %.1fs", time.time() - t0)
        log.info("  Stats: %s", stats)

    # ── Step 2: Train projection MLP ─────────────────────────────────
    mlp_path = data_dir / "projection_mlp.pt"
    if mlp_path.exists():
        log.info("\nSTEP 2: Projection MLP already exists, skipping")
    else:
        log.info("\nSTEP 2: Training projection MLP (genre-only, no plot embeddings)...")
        t0 = time.time()
        mlp_stats = train_projection_mlp(data_dir=data_dir, epochs=100)
        log.info("  MLP training complete in %.1fs", time.time() - t0)
        log.info("  Stats: %s", mlp_stats)

    # ── Step 3: Run full recommendation cycle ────────────────────────
    log.info("\nSTEP 3: Running full recommendation cycle...")
    repo.save_recommender_state({})  # Reset state
    engine = RecommenderEngine(repo, data_dir)

    t0 = time.time()
    try:
        result = engine.force_rebuild(config)
        log.info("  Cycle complete in %.1fs", time.time() - t0)
        log.info("  Index stats: %s", result.get("index_stats"))
        log.info("  Owned count: %d", result.get("owned_count", 0))
        log.info("  User count: %d", result.get("user_count", 0))
    except Exception as exc:
        log.error("  Cycle failed: %s", exc, exc_info=True)
        sys.exit(1)

    # ── Step 4: Show recommendations ─────────────────────────────────
    log.info("\nSTEP 4: Recommendations per user...")
    state = repo.load_recommender_state()
    for user_id, user_recs in state.get("recommendations", {}).items():
        username = state.get("user_profiles", {}).get(user_id, {}).get("username", user_id)
        for_you = user_recs.get("for_you", [])
        byw = user_recs.get("because_you_watched", [])

        log.info("\n  USER: %s (%d for_you, %d byw sections)", username, len(for_you), len(byw))
        log.info("  --- Recommended for You (top 10) ---")
        for i, rec in enumerate(for_you[:10]):
            owned = " [OWNED]" if rec.get("in_library") else ""
            src = f" [{rec.get('media_type', '?')}]"
            log.info("    %2d. %s (%.4f)%s%s — %s",
                     i + 1, rec["title"], rec["score"], src, owned,
                     ", ".join(rec.get("genres", [])))

        if byw:
            section = byw[0]
            log.info("  --- Because You Watched: %s ---", section["seed_title"])
            for i, rec in enumerate(section.get("items", [])[:5]):
                owned = " [OWNED]" if rec.get("in_library") else ""
                log.info("      %d. %s (%.4f)%s", i + 1, rec["title"], rec["score"], owned)

    # ── Step 5: Similarity search ────────────────────────────────────
    log.info("\nSTEP 5: Similarity search (Fight Club, tmdb=550)...")
    similar = engine.get_similar(550, k=10)
    if similar:
        log.info("  Items similar to Fight Club:")
        for i, item in enumerate(similar):
            log.info("    %2d. %s (%.4f) — %s",
                     i + 1, item["title"], item["score"],
                     ", ".join(item.get("genres", [])))
    else:
        log.info("  Fight Club not in index")

    # ── Step 6: Verify persistence ───────────────────────────────────
    log.info("\nSTEP 6: Persistence check...")
    fresh = RecommenderEngine(repo, data_dir)
    status = fresh.get_status()
    log.info("  user_count=%d, total_recs=%d", status["user_count"], status["total_recommendations"])

    log.info("\n" + "=" * 60)
    log.info("LIVE TEST COMPLETE")
    log.info("=" * 60)


if __name__ == "__main__":
    main()
