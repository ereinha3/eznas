#!/usr/bin/env python3
"""Sync studio-based collections in Jellyfin with curated poster artwork.

Queries all movies in the library, groups by studio metadata, and
creates/updates Jellyfin collections so new movies are automatically
included.  After sync, uploads curated poster images from ThePosterDB
for each collection.

Designed to run periodically (e.g., nightly via pipeline).

Usage:
    python3 scripts/jellyfin_studio_collections.py              # dry-run
    python3 scripts/jellyfin_studio_collections.py --execute     # sync
"""
import argparse
import base64
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Set

# ---------------------------------------------------------------------------
# Curated studio list — genre-focused studios only, no generic conglomerates.
#
# Maps TMDb studio name(s) -> (collection display name, ThePosterDB asset ID).
# Multiple TMDb names can map to the same collection (e.g. Disney variants).
# ---------------------------------------------------------------------------

STUDIO_COLLECTIONS: Dict[str, tuple] = {
    # Studio name (as in TMDb metadata)     -> (collection name, TPDb asset ID)
    "A24":                                     ("A24", 53731),
    "Studio Ghibli":                           ("Studio Ghibli", 32223),
    "Pixar":                                   ("Pixar", 38226),
    "Marvel Studios":                          ("Marvel Studios", 5912),
    "DreamWorks Animation":                    ("DreamWorks Animation", 52723),
    "Walt Disney Animation Studios":           ("Walt Disney Animation", 6016),
    "Walt Disney Pictures":                    ("Walt Disney Animation", 6016),
    "Walt Disney Productions":                 ("Walt Disney Animation", 6016),
    "Walt Disney Feature Animation":           ("Walt Disney Animation", 6016),
    "Blumhouse Productions":                   ("Blumhouse", 70087),
    "Laika":                                   ("Laika", 16548),
    "LAIKA":                                   ("Laika", 16548),
    "Laika Entertainment":                     ("Laika", 16548),
    "Illumination":                            ("Illumination", 8158),
    "Illumination Entertainment":              ("Illumination", 8158),
    "DC Films":                                ("DC", 584348),
    "DC":                                      ("DC", 584348),
    "DC Studios":                              ("DC", 584348),
    "DC Entertainment":                        ("DC", 584348),
}

# Deduplicated poster map: collection display name -> TPDb asset ID
COLLECTION_POSTERS: Dict[str, int] = {
    v[0]: v[1] for v in STUDIO_COLLECTIONS.values()
}

TPDB_URL = "https://theposterdb.com/api/assets/{asset_id}"


def get_token(base_url: str, username: str, password: str) -> str:
    data = json.dumps({"Username": username, "Pw": password}).encode()
    req = urllib.request.Request(
        f"{base_url}/Users/AuthenticateByName",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Emby-Authorization": (
                'MediaBrowser Client="studio-sync", '
                'Device="cli", DeviceId="studio-sync", Version="1.0"'
            ),
        },
    )
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp["AccessToken"]


def api_get(base_url: str, path: str, token: str) -> dict:
    req = urllib.request.Request(f"{base_url}{path}")
    req.add_header("X-Emby-Token", token)
    return json.loads(urllib.request.urlopen(req).read())


def get_movies_by_studio(base_url: str, token: str) -> Dict[str, List[str]]:
    """Returns {collection_name: [movie_id, ...]}."""
    data = api_get(
        base_url,
        "/Items?IncludeItemTypes=Movie&Recursive=true&Fields=Studios&Limit=2000",
        token,
    )
    result: Dict[str, Set[str]] = {}
    for item in data.get("Items", []):
        studios = [s.get("Name", "") for s in item.get("Studios", [])]
        for studio_name, (collection_name, _asset_id) in STUDIO_COLLECTIONS.items():
            if studio_name in studios:
                result.setdefault(collection_name, set()).add(item["Id"])
    return {k: sorted(v) for k, v in result.items()}


def get_existing_collections(base_url: str, token: str) -> Dict[str, dict]:
    """Returns {name: {id, child_ids}}."""
    data = api_get(
        base_url,
        "/Items?IncludeItemTypes=BoxSet&Recursive=true&Fields=ImageTags",
        token,
    )
    collections = {}
    for item in data.get("Items", []):
        name = item["Name"]
        cid = item["Id"]
        children = api_get(
            base_url,
            f"/Items?ParentId={cid}&Recursive=true",
            token,
        )
        child_ids = {c["Id"] for c in children.get("Items", [])}
        has_primary = bool(item.get("ImageTags", {}).get("Primary"))
        collections[name] = {
            "id": cid,
            "child_ids": child_ids,
            "has_primary": has_primary,
        }
    return collections


def create_collection(
    base_url: str, token: str, name: str, movie_ids: List[str]
) -> str:
    ids_str = ",".join(movie_ids)
    url = f"{base_url}/Collections?Name={urllib.parse.quote(name)}&Ids={ids_str}"
    req = urllib.request.Request(url, method="POST")
    req.add_header("X-Emby-Token", token)
    resp = json.loads(urllib.request.urlopen(req).read())
    return resp.get("Id", "?")


def add_to_collection(
    base_url: str, token: str, collection_id: str, movie_ids: List[str]
) -> None:
    ids_str = ",".join(movie_ids)
    url = f"{base_url}/Collections/{collection_id}/Items?Ids={ids_str}"
    req = urllib.request.Request(url, method="POST")
    req.add_header("X-Emby-Token", token)
    urllib.request.urlopen(req)


def remove_from_collection(
    base_url: str, token: str, collection_id: str, movie_ids: List[str]
) -> None:
    ids_str = ",".join(movie_ids)
    url = f"{base_url}/Collections/{collection_id}/Items?Ids={ids_str}"
    req = urllib.request.Request(url, method="DELETE")
    req.add_header("X-Emby-Token", token)
    urllib.request.urlopen(req)


def upload_poster(
    base_url: str, token: str, collection_id: str, collection_name: str,
    asset_id: int, dry_run: bool = True,
) -> bool:
    """Fetch poster from ThePosterDB, convert to JPEG, and upload to Jellyfin.

    Jellyfin's image upload endpoint expects base64-encoded image data.
    We resize to 1000x1500 (standard poster ratio) and convert to JPEG
    for consistency and smaller payload.
    """
    url = TPDB_URL.format(asset_id=asset_id)
    if dry_run:
        print(f"    POSTER (dry-run): would upload TPDb #{asset_id} to {collection_name}")
        return True

    try:
        from PIL import Image
    except ImportError:
        print("    POSTER ERROR: Pillow not installed (pip install Pillow)")
        return False

    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0 (EZNAS Studio Sync)")
        img_data = urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc:
        print(f"    POSTER ERROR: failed to fetch TPDb #{asset_id}: {exc}")
        return False

    # Convert to 1000x1500 JPEG
    try:
        img = Image.open(io.BytesIO(img_data))
        img = img.convert("RGB").resize((1000, 1500), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=90)
        jpeg_data = buf.getvalue()
    except Exception as exc:
        print(f"    POSTER ERROR: image conversion failed for {collection_name}: {exc}")
        return False

    # Upload as base64-encoded JPEG
    try:
        b64_data = base64.b64encode(jpeg_data)
        upload_url = f"{base_url}/Items/{collection_id}/Images/Primary"
        req = urllib.request.Request(upload_url, data=b64_data, method="POST")
        req.add_header("X-Emby-Token", token)
        req.add_header("Content-Type", "image/jpeg")
        urllib.request.urlopen(req)
        print(f"    POSTER: uploaded TPDb #{asset_id} to {collection_name} ({len(jpeg_data)} bytes)")
        return True
    except Exception as exc:
        print(f"    POSTER ERROR: upload failed for {collection_name}: {exc}")
        return False


def sync_collections(
    base_url: str, token: str, dry_run: bool = True,
    force_posters: bool = False,
) -> None:
    desired = get_movies_by_studio(base_url, token)
    existing = get_existing_collections(base_url, token)

    # Track collection IDs for poster upload
    collection_ids: Dict[str, str] = {}

    for collection_name, movie_ids in sorted(desired.items()):
        if len(movie_ids) < 2:
            print(f"  SKIP {collection_name}: only {len(movie_ids)} movie(s)")
            continue

        if collection_name in existing:
            coll = existing[collection_name]
            collection_ids[collection_name] = coll["id"]
            current_ids = coll["child_ids"]
            to_add = [mid for mid in movie_ids if mid not in current_ids]
            to_remove = [mid for mid in current_ids if mid not in set(movie_ids)]

            if to_add or to_remove:
                print(
                    f"  UPDATE {collection_name}: "
                    f"+{len(to_add)} -{len(to_remove)} "
                    f"(total: {len(movie_ids)})"
                )
                if not dry_run:
                    if to_add:
                        add_to_collection(base_url, token, coll["id"], to_add)
                    if to_remove:
                        remove_from_collection(
                            base_url, token, coll["id"], to_remove
                        )
            else:
                print(f"  OK {collection_name}: {len(movie_ids)} movies (no changes)")
        else:
            print(f"  CREATE {collection_name}: {len(movie_ids)} movies")
            if not dry_run:
                cid = create_collection(base_url, token, collection_name, movie_ids)
                print(f"    -> ID: {cid}")
                collection_ids[collection_name] = cid

    # Upload posters for collections that need them
    print()
    print("--- Poster sync ---")
    # Refresh existing collections to get current image state
    if not dry_run:
        existing = get_existing_collections(base_url, token)

    for collection_name, asset_id in sorted(COLLECTION_POSTERS.items()):
        cid = collection_ids.get(collection_name)
        if not cid and collection_name in existing:
            cid = existing[collection_name]["id"]

        if not cid:
            continue

        # Check if poster already exists
        has_primary = existing.get(collection_name, {}).get("has_primary", False)
        if has_primary and not force_posters and not dry_run:
            print(f"  OK {collection_name}: poster already set")
            continue

        upload_poster(base_url, token, cid, collection_name, asset_id, dry_run)


def main():
    parser = argparse.ArgumentParser(description="Sync studio collections")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--force-posters", action="store_true",
                        help="Re-upload posters even if already set")
    parser.add_argument("--url", default="http://localhost:8096")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="adminadmin123")
    args = parser.parse_args()

    token = get_token(args.url, args.username, args.password)

    if not args.execute:
        print("DRY RUN -- pass --execute to sync")

    sync_collections(
        args.url, token,
        dry_run=not args.execute,
        force_posters=args.force_posters,
    )


if __name__ == "__main__":
    main()
