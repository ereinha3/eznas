"""NFO file generation for Jellyfin metadata identification.

Writes tvshow.nfo and movie.nfo files containing TVDB/TMDB/IMDB IDs
so Jellyfin can correctly identify media without relying on folder
name fuzzy matching.

Without NFO files, Jellyfin can merge similarly-named shows
(e.g., "Hellsing" and "Hellsing Ultimate" become one series).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent

log = logging.getLogger(__name__)


def write_tvshow_nfo(
    series_dir: Path,
    *,
    title: str,
    tvdb_id: Optional[int] = None,
    tmdb_id: Optional[int] = None,
    imdb_id: Optional[str] = None,
    year: Optional[int] = None,
    overwrite: bool = False,
) -> bool:
    """Write a tvshow.nfo file for a TV series folder.

    Args:
        series_dir: The series root folder (e.g., /data/tv/Hellsing Ultimate/)
        title: Series title
        tvdb_id: TheTVDB numeric ID
        tmdb_id: TheMovieDB numeric ID
        imdb_id: IMDB string ID (e.g., "tt0495212")
        year: Original air year
        overwrite: If True, replace existing NFO

    Returns:
        True if file was written, False if skipped or failed.
    """
    nfo_path = series_dir / "tvshow.nfo"

    if nfo_path.exists() and not overwrite:
        return False

    root = Element("tvshow")
    SubElement(root, "title").text = title

    if year:
        SubElement(root, "year").text = str(year)

    if tvdb_id:
        uid = SubElement(root, "uniqueid")
        uid.set("type", "tvdb")
        uid.set("default", "true")
        uid.text = str(tvdb_id)

    if tmdb_id:
        uid = SubElement(root, "uniqueid")
        uid.set("type", "tmdb")
        uid.text = str(tmdb_id)

    if imdb_id:
        uid = SubElement(root, "uniqueid")
        uid.set("type", "imdb")
        uid.text = imdb_id

    try:
        indent(root, space="  ")
        tree = ElementTree(root)
        tree.write(str(nfo_path), encoding="utf-8", xml_declaration=True)
        log.info("[nfo] wrote %s for '%s'", nfo_path, title)
        return True
    except OSError as exc:
        log.error("[nfo] failed to write %s: %s", nfo_path, exc)
        return False


def write_movie_nfo(
    movie_dir: Path,
    *,
    title: str,
    tmdb_id: Optional[int] = None,
    imdb_id: Optional[str] = None,
    year: Optional[int] = None,
    overwrite: bool = False,
) -> bool:
    """Write a movie.nfo file for a movie folder.

    Args:
        movie_dir: The movie folder (e.g., /data/movies/Hereditary (2018)/)
        title: Movie title
        tmdb_id: TheMovieDB numeric ID
        imdb_id: IMDB string ID
        year: Release year
        overwrite: If True, replace existing NFO

    Returns:
        True if file was written, False if skipped or failed.
    """
    nfo_path = movie_dir / "movie.nfo"

    if nfo_path.exists() and not overwrite:
        return False

    root = Element("movie")
    SubElement(root, "title").text = title

    if year:
        SubElement(root, "year").text = str(year)

    if tmdb_id:
        uid = SubElement(root, "uniqueid")
        uid.set("type", "tmdb")
        uid.set("default", "true")
        uid.text = str(tmdb_id)

    if imdb_id:
        uid = SubElement(root, "uniqueid")
        uid.set("type", "imdb")
        uid.text = imdb_id

    try:
        indent(root, space="  ")
        tree = ElementTree(root)
        tree.write(str(nfo_path), encoding="utf-8", xml_declaration=True)
        log.info("[nfo] wrote %s for '%s'", nfo_path, title)
        return True
    except OSError as exc:
        log.error("[nfo] failed to write %s: %s", nfo_path, exc)
        return False
