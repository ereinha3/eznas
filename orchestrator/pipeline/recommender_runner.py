"""Standalone runner for the recommendation engine container.

Runs the recommendation cycle on a configurable interval, independent of
the main pipeline worker. Designed to be the CMD for Dockerfile.recommender.

Usage:
    python -m orchestrator.pipeline.recommender_runner

Environment:
    ORCH_ROOT             — Config root directory (default: /config)
    RECOMMENDER_INTERVAL  — Seconds between cycles (default: 3600)
    RECOMMENDER_DATA_DIR  — FAISS index / cache directory (default: /data/recommender)
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("recommender")

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    log.info("Received signal %s, shutting down...", signum)
    _shutdown = True


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    orch_root = Path(os.environ.get("ORCH_ROOT", "/config"))
    interval = int(os.environ.get("RECOMMENDER_INTERVAL", "3600"))
    data_dir = Path(os.environ.get("RECOMMENDER_DATA_DIR", "/data/recommender"))

    log.info("Recommender runner starting")
    log.info("  ORCH_ROOT=%s", orch_root)
    log.info("  RECOMMENDER_INTERVAL=%ds", interval)
    log.info("  RECOMMENDER_DATA_DIR=%s", data_dir)

    from ..storage import ConfigRepository
    from .recommender import RecommenderEngine

    repo = ConfigRepository(orch_root, read_only=True)
    engine = RecommenderEngine(repo, data_dir)

    while not _shutdown:
        try:
            config = repo.load_stack()
            rec_cfg = config.services.pipeline.recommender

            if not rec_cfg.enabled:
                log.info("Recommender disabled in config, sleeping %ds...", interval)
                _sleep(interval)
                continue

            # Check if a cycle is due (interval-based, same as enrichment pattern)
            state = repo.load_recommender_state()
            last_run = state.get("last_run", 0)
            if time.time() - last_run < rec_cfg.refresh_interval_hours * 3600:
                remaining = int(
                    rec_cfg.refresh_interval_hours * 3600 - (time.time() - last_run)
                )
                log.info(
                    "Next cycle in %dh %dm, sleeping %ds...",
                    remaining // 3600,
                    (remaining % 3600) // 60,
                    min(interval, remaining),
                )
                _sleep(min(interval, remaining))
                continue

            log.info("Starting recommendation cycle...")
            # Use a writable repo for the actual cycle
            writable_repo = ConfigRepository(orch_root, read_only=False)
            cycle_engine = RecommenderEngine(writable_repo, data_dir)
            cycle_engine.maybe_run(config)

        except FileNotFoundError:
            log.warning("stack.yaml not found at %s, retrying in %ds...", orch_root, interval)
        except Exception as exc:
            log.error("Recommender cycle error: %s", exc, exc_info=True)

        _sleep(interval)

    log.info("Recommender runner stopped.")


def _sleep(seconds: int) -> None:
    """Interruptible sleep."""
    end = time.time() + seconds
    while not _shutdown and time.time() < end:
        time.sleep(min(5, end - time.time()))


if __name__ == "__main__":
    main()
