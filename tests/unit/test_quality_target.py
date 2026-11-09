"""Central quality target (stack.yaml ``quality``) applied to Radarr/Sonarr and the pipeline."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from orchestrator.clients.quality_target import (
    cap_profile,
    ensure_quality_target,
    resolution_height,
    should_replace_existing,
)
from orchestrator.models import StackConfig
from orchestrator.pipeline.remux import VideoQuality


def _q(qid: int, name: str, res: int, allowed: bool = True) -> dict[str, Any]:
    return {"quality": {"id": qid, "name": name, "resolution": res}, "items": [], "allowed": allowed}


def _any_profile() -> dict[str, Any]:
    # Shape of the live Radarr "Any" profile: upgrades on, 2160p allowed.
    return {
        "id": 1, "name": "Any", "upgradeAllowed": True, "cutoff": 4,
        "items": [
            _q(1, "SDTV", 480, allowed=False),
            _q(4, "HDTV-720p", 720),
            {"name": "WEB 1080p", "id": 1003, "allowed": True,
             "items": [_q(3, "WEBDL-1080p", 1080), _q(15, "WEBRip-1080p", 1080)]},
            _q(7, "Bluray-1080p", 1080),
            _q(30, "Remux-1080p", 1080),
            {"name": "WEB 2160p", "id": 1004, "allowed": True,
             "items": [_q(18, "WEBDL-2160p", 2160), _q(17, "WEBRip-2160p", 2160)]},
            _q(31, "Remux-2160p", 2160),
            _q(22, "BR-DISK", 1080, allowed=False),
        ],
    }


def _uhd_profile() -> dict[str, Any]:
    return {"id": 5, "name": "Ultra-HD", "upgradeAllowed": False, "cutoff": 31,
            "items": [_q(30, "Remux-1080p", 1080, allowed=False), _q(31, "Remux-2160p", 2160)]}


def _hd_profile() -> dict[str, Any]:
    return {"id": 4, "name": "HD-1080p", "upgradeAllowed": False, "cutoff": 7,
            "items": [_q(4, "HDTV-720p", 720, allowed=False), _q(7, "Bluray-1080p", 1080)]}


def _allowed_names(profile: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in profile["items"]:
        if not item.get("allowed"):
            continue
        children = item.get("items") or []
        names |= {c["quality"]["name"] for c in children} if children else {item["quality"]["name"]}
    return names


def test_cap_profile_disables_qualities_above_target() -> None:
    capped = cap_profile(_any_profile(), 1080, allow_upgrades=None)
    assert capped is not None
    assert _allowed_names(capped) == {"HDTV-720p", "WEBDL-1080p", "WEBRip-1080p", "Bluray-1080p", "Remux-1080p"}
    assert capped["cutoff"] == 4            # cutoff already within target: untouched
    assert capped["upgradeAllowed"] is True  # toggle unset: untouched


def test_cap_profile_moves_cutoff_down_and_sets_upgrade_toggle() -> None:
    profile = _any_profile()
    profile["cutoff"] = 31  # Remux-2160p
    capped = cap_profile(profile, 1080, allow_upgrades=False)
    assert capped is not None
    assert capped["cutoff"] == 30  # best remaining: Remux-1080p
    assert capped["upgradeAllowed"] is False


def test_cap_profile_returns_none_when_nothing_changes_or_profile_would_be_empty() -> None:
    assert cap_profile(_hd_profile(), 1080, allow_upgrades=None) is None
    assert cap_profile(_uhd_profile(), 1080, allow_upgrades=None) is None  # would allow nothing


class _FakeArr:
    def __init__(self, profiles: list[dict[str, Any]]) -> None:
        self.profiles = profiles
        self.puts: list[tuple[str, dict[str, Any]]] = []

    def get_json(self, path: str, **_: Any) -> Any:
        assert path == "/qualityprofile"
        return deepcopy(self.profiles)

    def put_json(self, path: str, json: Any, **_: Any) -> Any:
        self.puts.append((path, deepcopy(json)))
        pid = int(path.rsplit("/", 1)[1])
        self.profiles = [json if p["id"] == pid else p for p in self.profiles]
        return json


def _config(sample_config: dict[str, Any], **quality: Any) -> StackConfig:
    cfg = deepcopy(sample_config)
    cfg.setdefault("quality", {}).update(quality)
    return StackConfig.model_validate(cfg)


def test_ensure_quality_target_caps_profiles_and_is_idempotent(sample_config: dict[str, Any]) -> None:
    api = _FakeArr([_any_profile(), _uhd_profile(), _hd_profile()])
    config = _config(sample_config, target_resolution="1080p")

    changed, msg, warnings = ensure_quality_target(api, config)
    assert changed is True
    assert [p for p, _ in api.puts] == ["/qualityprofile/1"]
    assert "Any" in msg
    assert any("Ultra-HD" in w for w in warnings)  # left alone, reported

    api.puts.clear()
    changed, _, _ = ensure_quality_target(api, config)
    assert changed is False and api.puts == []


def test_ensure_quality_target_noop_when_unset(sample_config: dict[str, Any]) -> None:
    api = _FakeArr([_any_profile()])
    changed, _, _ = ensure_quality_target(api, _config(sample_config, target_resolution=None))
    assert changed is False and api.puts == []


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [(1920, 1080, 1080), (1920, 804, 1080), (1920, 1036, 1080), (3840, 1608, 2160), (1280, 534, 720), (720, 480, 480)],
)
def test_resolution_height_handles_cropped_widescreen(width: int, height: int, expected: int) -> None:
    assert resolution_height(VideoQuality(width=width, height=height)) == expected


@pytest.mark.parametrize(
    ("existing", "incoming", "target", "allow", "expected"),
    [
        (1080, 2160, 1080, True, False),   # above target: keep existing (the Paprika case)
        (1080, 2160, 1080, False, False),
        (720, 1080, 1080, True, True),     # upgrade within target, toggle on
        (720, 1080, 1080, False, False),   # toggle off: never replace
        (1080, 1080, 1080, True, False),   # not better
        (1080, 2160, 2160, True, True),    # room for 2160p
        (720, 1080, None, True, False),    # no target configured: old behaviour
    ],
)
def test_should_replace_existing(existing: int, incoming: int, target: int | None, allow: bool, expected: bool) -> None:
    assert should_replace_existing(existing, incoming, target, allow) is expected


def test_enrichment_target_follows_central_quality(sample_config: dict[str, Any]) -> None:
    from orchestrator.pipeline.enrichment import effective_target_resolution

    config = _config(sample_config, target_resolution="2160p")
    assert effective_target_resolution(config, config.services.pipeline.enrichment) == "2160p"
    config = _config(sample_config, target_resolution=None)
    assert effective_target_resolution(config, config.services.pipeline.enrichment) == config.services.pipeline.enrichment.target_resolution


def _movie_plans(config: StackConfig, tmp: "Path", existing_h: int, incoming_h: int) -> list[Any]:
    from pathlib import Path
    from unittest.mock import patch

    from orchestrator.pipeline.remux import StreamInfo
    from orchestrator.pipeline.worker import PipelineWorker, TorrentInfo

    tmp = Path(tmp)
    src = tmp / "dl" / "Paprika.2006.2160p.REMUX.mkv"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"\0" * 1024 * 1024)
    library = tmp / "pool" / "media" / "movies" / "Paprika (2006)"
    library.mkdir(parents=True)
    existing = library / "Paprika (2006).mkv"
    existing.write_bytes(b"\0" * 1024)

    def fake_probe(path: Path) -> VideoQuality:
        return VideoQuality(width=1920 if path == existing and existing_h == 1080 else 0,
                            height=existing_h if path == existing else incoming_h)

    worker = PipelineWorker(config)
    torrent = TorrentInfo(hash="p", name=src.name, category=config.download_policy.categories.radarr,
                          download_path=src.parent, files=[src])
    with patch("orchestrator.pipeline.remux.probe_streams") as probe_streams, \
         patch("orchestrator.pipeline.worker.probe_video_quality", side_effect=fake_probe):
        probe_streams.return_value = StreamInfo(audio_languages={"eng"}, subtitle_languages={"eng"},
                                                has_video=True, audio_count=1, subtitle_count=1)
        return worker.build_plans(torrent, library_path=library)


def _pipeline_config(sample_config: dict[str, Any], temp_dir: Any, **quality: Any) -> StackConfig:
    cfg = deepcopy(sample_config)
    cfg["paths"]["pool"] = str(temp_dir / "pool")
    cfg["paths"]["scratch"] = str(temp_dir / "scratch")
    cfg.setdefault("quality", {}).update(quality)
    return StackConfig.model_validate(cfg)


def test_pipeline_keeps_existing_when_incoming_exceeds_target(sample_config: dict[str, Any], temp_dir: Any) -> None:
    config = _pipeline_config(sample_config, temp_dir, target_resolution="1080p", allow_upgrades=True)
    assert _movie_plans(config, temp_dir, existing_h=1080, incoming_h=2160) == []


def test_pipeline_replaces_on_upgrade_within_target(sample_config: dict[str, Any], temp_dir: Any) -> None:
    config = _pipeline_config(sample_config, temp_dir, target_resolution="1080p", allow_upgrades=True)
    plans = _movie_plans(config, temp_dir, existing_h=720, incoming_h=1080)
    assert len(plans) == 1 and plans[0].replace_existing is True


def test_pipeline_never_replaces_with_toggle_off(sample_config: dict[str, Any], temp_dir: Any) -> None:
    config = _pipeline_config(sample_config, temp_dir, target_resolution="1080p")
    assert _movie_plans(config, temp_dir, existing_h=720, incoming_h=1080) == []
