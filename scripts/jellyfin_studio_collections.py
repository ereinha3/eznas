#!/usr/bin/env python3
"""Sync studio-based collections in Jellyfin.

Queries all movies in the library, groups by studio metadata, and
creates/updates Jellyfin collections so new movies are automatically
included.  Designed to run periodically (e.g., nightly via pipeline).

Usage:
    python3 scripts/jellyfin_studio_collections.py              # dry-run
    python3 scripts/jellyfin_studio_collections.py --execute     # sync
"""
import argparse
import json
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, List, Set

# Studios to create collections for.
# Maps studio name (as it appears in TMDb metadata) -> collection display name.
STUDIO_COLLECTIONS: Dict[str, str] = {
    "Marvel Studios": "Marvel Studios",
    "Pixar": "Pixar",
    "Studio Ghibli": "Studio Ghibli",
    "Walt Disney Pictures": "Walt Disney",
    "Walt Disney Animation Studios": "Walt Disney",
    "Warner Bros. Pictures": "Warner Bros.",
    "A24": "A24",
    "DreamWorks Animation": "DreamWorks Animation",
}


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
        for studio_name, collection_name in STUDIO_COLLECTIONS.items():
            if studio_name in studios:
                result.setdefault(collection_name, set()).add(item["Id"])
    return {k: sorted(v) for k, v in result.items()}


def get_existing_collections(base_url: str, token: str) -> Dict[str, dict]:
    """Returns {name: {id, child_ids}}."""
    data = api_get(
        base_url,
        "/Items?IncludeItemTypes=BoxSet&Recursive=true",
        token,
    )
    collections = {}
    for item in data.get("Items", []):
        name = item["Name"]
        cid = item["Id"]
        # Get children
        children = api_get(
            base_url,
            f"/Items?ParentId={cid}&Recursive=true",
            token,
        )
        child_ids = {c["Id"] for c in children.get("Items", [])}
        collections[name] = {"id": cid, "child_ids": child_ids}
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


def sync_collections(
    base_url: str, token: str, dry_run: bool = True
) -> None:
    desired = get_movies_by_studio(base_url, token)
    existing = get_existing_collections(base_url, token)

    for collection_name, movie_ids in sorted(desired.items()):
        if len(movie_ids) < 2:
            continue  # Skip single-movie collections

        if collection_name in existing:
            coll = existing[collection_name]
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
                print(f"    → ID: {cid}")


def main():
    parser = argparse.ArgumentParser(description="Sync studio collections")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--url", default="http://localhost:8096")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="adminadmin123")
    args = parser.parse_args()

    token = get_token(args.url, args.username, args.password)

    if not args.execute:
        print("DRY RUN — pass --execute to sync")

    sync_collections(args.url, token, dry_run=not args.execute)


if __name__ == "__main__":
    main()
