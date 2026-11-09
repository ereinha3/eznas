"""Apply the central quality target (``stack.yaml`` ``quality``) everywhere.

One value, ``quality.target_resolution``, drives:
  * Radarr/Sonarr quality profiles: no profile may allow a quality above the
    target (so Jellyseerr requests and manual adds can't grab 4K at a 1080p
    target), and cutoffs above the target are lowered;
  * the pipeline's keep-or-replace decision for files already in the library;
  * the enrichment video-upgrade target.
``quality.allow_upgrades`` (optional) sets ``upgradeAllowed`` on the profiles
and lets the pipeline replace a library file with a higher resolution one that
is still within the target.  Raising the target does not re-enable qualities
that an earlier, lower target disabled.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional, Protocol, Tuple

from ..models import StackConfig

RESOLUTION_HEIGHTS = {"720p": 720, "1080p": 1080, "1440p": 1440, "2160p": 2160}


class _ArrLike(Protocol):
    def get_json(self, path: str, **kwargs: Any) -> Any: ...
    def put_json(self, path: str, json: Any, **kwargs: Any) -> Any: ...


def target_height(config: StackConfig) -> Optional[int]:
    target = config.quality.target_resolution
    return RESOLUTION_HEIGHTS[target.value] if target else None


def resolution_height(video: Any) -> int:
    """Nominal resolution of a probed video (handles cropped widescreen).

    1920x804 is a 1080p release, so use the larger of the height and the
    16:9 height implied by the width, then snap to the standard tiers.
    """
    effective = max(int(video.height or 0), round(int(video.width or 0) * 9 / 16))
    for nominal in (2160, 1440, 1080, 720):
        if effective >= nominal * 0.85:
            return nominal
    return 480 if effective >= 400 else effective


def should_replace_existing(
    existing_height: int, incoming_height: int,
    target: Optional[int], allow_upgrades: bool,
) -> bool:
    """Replace a library file only for a real upgrade that stays within target."""
    if target is None or not allow_upgrades:
        return False
    return existing_height < incoming_height <= target


def _item_resolution(item: Dict[str, Any]) -> int:
    children = item.get("items") or []
    if children:
        return max(int((c.get("quality") or {}).get("resolution") or 0) for c in children)
    return int((item.get("quality") or {}).get("resolution") or 0)


def _item_id(item: Dict[str, Any]) -> Optional[int]:
    if item.get("items"):
        return item.get("id")
    return (item.get("quality") or {}).get("id")


def cap_profile(
    profile: Dict[str, Any], max_height: int, allow_upgrades: Optional[bool],
) -> Optional[Dict[str, Any]]:
    """Return an updated copy of ``profile`` capped at ``max_height``, or None.

    None means no change is needed, or the cap would leave the profile with no
    allowed quality (e.g. an Ultra-HD-only profile); that one is left alone.
    """
    updated = deepcopy(profile)
    for item in updated["items"]:
        if item.get("allowed") and _item_resolution(item) > max_height:
            item["allowed"] = False
            for child in item.get("items") or []:
                child["allowed"] = False
    allowed = [i for i in updated["items"] if i.get("allowed")]
    if not allowed:
        return None
    by_id = {_item_id(i): i for i in updated["items"]}
    cutoff_item = by_id.get(updated.get("cutoff"))
    if cutoff_item is None or not cutoff_item.get("allowed"):
        # Best remaining: highest resolution, later items rank higher on ties.
        best = max(enumerate(allowed), key=lambda pair: (_item_resolution(pair[1]), pair[0]))[1]
        updated["cutoff"] = _item_id(best)
    if allow_upgrades is not None:
        updated["upgradeAllowed"] = allow_upgrades
    return None if updated == profile else updated


def ensure_quality_target(api: _ArrLike, config: StackConfig) -> Tuple[bool, str, List[str]]:
    """Cap every quality profile at the configured target. Idempotent."""
    max_height = target_height(config)
    if max_height is None:
        return False, "quality target unset", []
    changed: List[str] = []
    warnings: List[str] = []
    for profile in api.get_json("/qualityprofile"):
        updated = cap_profile(profile, max_height, config.quality.allow_upgrades)
        if updated is not None:
            api.put_json(f"/qualityprofile/{profile['id']}", updated)
            changed.append(str(profile.get("name")))
        elif all(_item_resolution(i) > max_height for i in profile["items"] if i.get("allowed")) and any(
            i.get("allowed") for i in profile["items"]
        ):
            warnings.append(
                f"profile {profile.get('name')!r} only allows qualities above "
                f"{config.quality.target_resolution.value}; left unchanged"
            )
    if changed:
        return True, f"capped quality profiles at {config.quality.target_resolution.value}: {', '.join(changed)}", warnings
    return False, "quality profiles within target", warnings
