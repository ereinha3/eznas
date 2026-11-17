"""The queue-driven grab path must be able to build search queries.

``_grab_from_queue`` is what ``maybe_grab_next`` calls every tick. It once
called ``_build_queries`` without the stack config, so every tick raised
TypeError and enrichment never searched for anything.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from orchestrator.models import StackConfig
from orchestrator.pipeline.enrichment import EnrichmentCandidate, EnrichmentEngine


def _candidate(video_below_target: bool) -> EnrichmentCandidate:
    return EnrichmentCandidate(
        path=Path("/data/movies/Film (2020)/Film (2020).mkv"),
        title="Film",
        year=2020,
        original_language="jpn",
        missing_languages=["eng"],
        arr_id=1,
        service="radarr",
        category="movies",
        video_below_target=video_below_target,
    )


def _engine() -> EnrichmentEngine:
    repo = MagicMock()
    repo.load_secrets.return_value = {
        "prowlarr": {"api_key": "k"},
        "qbittorrent": {"username": "u", "password": "p"},
    }
    engine = EnrichmentEngine(repo)
    engine._get_qbt_names = lambda *a, **k: set()  # type: ignore[method-assign]
    engine.searched: list[list[str]] = []  # type: ignore[attr-defined]

    def search(host, key, queries):
        engine.searched.append(queries)  # type: ignore[attr-defined]
        return []

    engine._search_prowlarr = search  # type: ignore[method-assign]
    return engine


def test_grab_from_queue_searches_candidates(stack_config: StackConfig) -> None:
    engine = _engine()
    # A video upgrade exercises the branch of _build_queries that reads config.
    engine._queue = [_candidate(video_below_target=True), _candidate(video_below_target=False)]
    state: dict = {}

    grabbed = engine._grab_from_queue(stack_config, stack_config.services.pipeline.enrichment, state)

    assert grabbed == 0
    assert len(engine.searched) == 2  # type: ignore[attr-defined]
    assert all(q and all("Film" in s for s in q) for q in engine.searched)  # type: ignore[attr-defined]
    # The no-result path recorded a search miss for backoff.
    assert len(state["search_misses"]) == 1
