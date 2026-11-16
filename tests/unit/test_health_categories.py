"""Stall detection must only act on torrents in pipeline-managed categories.

Other apps share qBittorrent (e.g. Bookshelf's ``audiobooks`` category). The
health monitor deletes stalled torrents *with their files*, so touching a
category it does not manage destroys another app's downloads.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from orchestrator.models import StackConfig
from orchestrator.pipeline.health import DownloadHealthMonitor


def _dead(h: str, category: str) -> dict:
    """A torrent in the 'dead' state: stalled, 0 seeds, no progress."""
    return {
        "hash": h, "name": f"torrent-{h}", "category": category,
        "state": "stalledDL", "num_seeds": 0, "downloaded": 100,
    }


@pytest.fixture
def monitor() -> DownloadHealthMonitor:
    repo = MagicMock()
    repo.load_state.return_value = {
        "secrets": {
            "qbittorrent": {"username": "u", "password": "p"},
            "sonarr": {"api_key": "s"},
            "radarr": {"api_key": "r"},
        }
    }
    mon = DownloadHealthMonitor(repo)
    mon.removed = []  # type: ignore[attr-defined]
    mon._remove_from_qbt = lambda host, creds, cfg, h: mon.removed.append(h)  # type: ignore[method-assign]
    mon._get_arr_queue = lambda *a, **k: []  # type: ignore[method-assign]
    mon._handle_stall_in_arr = lambda *a, **k: False  # type: ignore[method-assign]
    return mon


def _run(mon: DownloadHealthMonitor, config: StackConfig, torrents: list[dict]) -> dict:
    mon._get_qbt_torrents = lambda *a, **k: torrents  # type: ignore[method-assign]
    # Seed a prior snapshot so this check is the 2nd consecutive no-progress check.
    now = int(time.time())
    state = {"torrent_progress": {
        t["hash"]: {"downloaded": t["downloaded"], "checked_at": now - 60, "stall_count": 1}
        for t in torrents
    }}
    mon._run_check(config, config.services.pipeline.backfill, state)
    return state


def test_unmanaged_category_is_never_removed(monitor, stack_config):
    _run(monitor, stack_config, [_dead("aaa", "audiobooks"), _dead("bbb", "")])
    assert monitor.removed == []


def test_managed_categories_are_still_removed(monitor, stack_config):
    torrents = [
        _dead("m1", "movies"),
        _dead("t1", "tv"),
        _dead("t2", "tv-sonarr"),  # arr-suffixed categories normalise to managed ones
        _dead("an", "anime"),
        _dead("ab", "audiobooks"),
    ]
    _run(monitor, stack_config, torrents)
    assert sorted(monitor.removed) == ["an", "m1", "t1", "t2"]


def test_unmanaged_torrent_is_not_tracked(monitor, stack_config):
    """No progress bookkeeping for torrents the monitor will never act on."""
    monitor._get_qbt_torrents = lambda *a, **k: [_dead("ab", "audiobooks")]  # type: ignore[method-assign]
    state: dict = {}
    monitor._run_check(stack_config, stack_config.services.pipeline.backfill, state)
    assert "ab" not in state.get("torrent_progress", {})
