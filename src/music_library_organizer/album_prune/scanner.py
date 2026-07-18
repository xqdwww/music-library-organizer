from __future__ import annotations

import hashlib
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from mutagen import File

from .entity_resolution import build_classical_identity, build_jazz_identity, language_route
from .models import LocalAlbum
from .normalize import canonical_identity, parse_year

AUDIO_EXTENSIONS = {
    ".aac",
    ".aif",
    ".aiff",
    ".alac",
    ".ape",
    ".dsf",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".wma",
}
DISC_DIR = re.compile(r"^(?:cd|disc|disk|vol(?:ume)?)[ ._-]*\d+$", re.IGNORECASE)
YEAR_SUFFIX = re.compile(r"\s*[\[(](?:19|20)\d{2}[\])]\s*$")


def _first(metadata: Any, *keys: str) -> str | None:
    if metadata is None:
        return None
    for key in keys:
        try:
            values = metadata.get(key, [])
        except Exception:
            continue
        if values:
            value = str(values[0]).strip()
            if value:
                return value
    return None


def _metadata(path: Path) -> dict[str, str]:
    try:
        audio = File(path, easy=True)
    except Exception:
        return {}
    aliases = {
        "artist": ("albumartist", "album artist", "artist"),
        "track_artist": ("artist",),
        "artist_sort": ("albumartistsort", "artist sort", "artistsort"),
        "artist_aliases": ("artistalias", "artist alias"),
        "disc_total": ("disctotal", "totaldiscs"),
        "track_total": ("tracktotal", "totaltracks"),
        "acoustid_id": ("acoustid_id", "acoustid id"),
        "album": ("album",),
        "date": ("originaldate", "date", "year"),
        "release_group_mbid": ("musicbrainz_releasegroupid", "musicbrainz release group id"),
        "release_mbid": ("musicbrainz_albumid", "musicbrainz release id"),
        "barcode": ("barcode",),
        "catalog_number": ("catalognumber", "catalog number"),
        "discogs_id": ("discogs_release_id", "discogs release id"),
        "genre": ("genre",),
        "composer": ("composer",),
        "work": ("work", "musicbrainz_workid"),
        "conductor": ("conductor",),
        "orchestra": ("orchestra", "ensemble"),
        "soloists": ("performer", "soloists"),
        "personnel": ("personnel", "musician credits"),
        "recording_date": ("recordingdate", "originaldate", "date"),
        "label": ("label", "organization"),
        "edition": ("edition", "version"),
        "album_type": ("releasetype", "musicbrainz_albumtype", "albumtype"),
        "language": ("language",),
        "country": ("country", "releasecountry"),
        "script": ("script",),
    }
    return {name: value for name, keys in aliases.items() if (value := _first(audio, *keys))}


def _split_values(value: str | None) -> list[str]:
    if not value:
        return []
    return sorted({item.strip() for item in re.split(r"[;,/]", value) if item.strip()})


def _category(genres: list[str], metadata: dict[str, str], directory: Path) -> str:
    genre_text = " ".join(genres).casefold()
    classical_fields = " ".join(
        metadata.get(key) or ""
        for key in ("composer", "work", "conductor", "orchestra", "soloists")
    ).casefold()
    path_text = directory.as_posix().casefold()
    if (
        any(token in genre_text for token in ("classical", "古典", "symphony", "concerto", "opera", "chamber"))
        or bool(classical_fields.strip())
        or "古典" in path_text
        or any(token in path_text for token in ("symphony", "concerto", "chamber music"))
    ):
        return "Classical"
    text = f"{genre_text} {path_text}"
    if any(token in text for token in ("jazz", "爵士", "bebop", "bop", "swing")):
        return "Jazz"
    if any(token in text for token in ("pop", "rock", "folk", "流行", "摇滚", "民谣")):
        return "Popular/Rock/Folk"
    return "Other"


def _album_type(value: str | None, album: str) -> str:
    text = f"{value or ''} {album}".casefold()
    for result, tokens in (
        ("Soundtrack", ("soundtrack", "ost", "原声")),
        ("Compilation", ("compilation", "best of", "精选")),
        ("Live", ("live", "演唱会", "现场")),
        ("Single", ("single", "单曲")),
        ("EP", (" ep", "ep ")),
    ):
        if any(token in text for token in tokens):
            return result
    return "Album" if value and "album" in value.casefold() else "Other"


def _mode(values: list[str]) -> str | None:
    populated = [value for value in values if value]
    return Counter(populated).most_common(1)[0][0] if populated else None


def _album_directory(path: Path, root: Path) -> Path:
    parent = path.parent
    if DISC_DIR.match(parent.name) and parent.parent != root:
        return parent.parent
    return parent


def scan_albums(
    root: Path,
    limit: int | None = None,
    album_paths: list[str] | None = None,
) -> list[LocalAlbum]:
    root = root.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("library root must be a regular directory")
    scan_roots = [] if album_paths else [root]
    if album_paths:
        scan_roots = []
        for relative_value in album_paths:
            relative = Path(relative_value)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"unsafe album path: {relative_value}")
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"album path escapes library: {relative_value}") from exc
            if not candidate.is_dir() or candidate.is_symlink():
                raise ValueError(f"album path is unavailable: {relative_value}")
            scan_roots.append(candidate)
    grouped: dict[Path, list[tuple[Path, dict[str, str]]]] = defaultdict(list)
    seen_files: set[Path] = set()
    for scan_root in scan_roots:
        for directory, names, files in os.walk(scan_root, followlinks=False):
            names[:] = sorted(name for name in names if not (Path(directory) / name).is_symlink())
            for name in sorted(files):
                path = Path(directory) / name
                if path.suffix.lower() not in AUDIO_EXTENSIONS or path.is_symlink() or not path.is_file():
                    continue
                if path in seen_files:
                    continue
                seen_files.add(path)
                grouped[_album_directory(path, root)].append((path, _metadata(path)))
    directories = sorted(grouped, key=lambda item: item.as_posix().casefold())
    if limit is not None:
        directories = directories[:limit]
    albums: list[LocalAlbum] = []
    for directory in directories:
        rows = grouped[directory]
        relative = directory.relative_to(root).as_posix()
        artist = _mode([row.get("artist", "") for _, row in rows])
        album = _mode([row.get("album", "") for _, row in rows])
        parts = directory.relative_to(root).parts
        if not artist:
            artist = parts[-2] if len(parts) >= 2 else "Unknown Artist"
        if not album:
            album = YEAR_SUFFIX.sub("", directory.name).strip() or "Unknown Album"
        year_values = [parse_year(row.get("date")) for _, row in rows]
        year = (
            Counter(value for value in year_values if value).most_common(1)[0][0]
            if any(year_values)
            else parse_year(directory.name)
        )
        warnings: list[str] = []
        child_groups = [other for other in grouped if other != directory and directory in other.parents]
        safe_directory = not child_groups and directory != root
        if child_groups:
            warnings.append("directory also contains nested album groups")
        if len(parts) < 2:
            warnings.append("album is not below an artist/category directory")
            safe_directory = False
        stat_rows: list[str] = []
        audio_size = 0
        for path, _ in rows:
            stat = path.stat()
            audio_size += stat.st_size
            stat_rows.append(f"{path.relative_to(directory).as_posix()}\0{stat.st_size}\0{stat.st_mtime_ns}")
        library_files = (
            [path for path in directory.rglob("*") if path.is_file() and not path.is_symlink()]
            if safe_directory
            else []
        )
        size = sum(path.stat().st_size for path in library_files) if safe_directory else audio_size
        file_count = len(library_files) if safe_directory else len(rows)
        fingerprint = hashlib.sha256("\n".join(sorted(stat_rows)).encode()).hexdigest()
        identity = canonical_identity(artist, album)
        album_id = (
            "local:"
            + hashlib.sha256(f"{identity[0]}\0{identity[1]}\0{year or ''}\0{relative}".encode()).hexdigest()[:24]
        )
        common_metadata = {key: _mode([row.get(key, "") for _, row in rows]) for key in (
            "genre", "composer", "work", "conductor", "orchestra", "soloists", "personnel",
            "recording_date", "label", "edition", "album_type",
            "language", "country", "script", "artist_sort", "artist_aliases",
            "disc_total", "track_total", "acoustid_id",
        )}
        genres = _split_values(common_metadata["genre"])
        album_type = _album_type(common_metadata["album_type"], album)
        live_studio = "live" if album_type == "Live" else "studio" if album_type == "Album" else None
        album_value = LocalAlbum(
            album_id=album_id,
            path=str(directory),
            relative_path=relative,
            artist=artist,
            album=album,
            year=year,
            track_count=len(rows),
            file_count=file_count,
            size_bytes=size,
            formats=sorted({path.suffix.lower()[1:] for path, _ in rows}),
            fingerprint=fingerprint,
            release_group_mbid=_mode([row.get("release_group_mbid", "") for _, row in rows]),
            release_mbid=_mode([row.get("release_mbid", "") for _, row in rows]),
            barcode=_mode([row.get("barcode", "") for _, row in rows]),
            catalog_number=_mode([row.get("catalog_number", "") for _, row in rows]),
            discogs_id=_mode([row.get("discogs_id", "") for _, row in rows]),
            safe_directory=safe_directory,
            scan_warnings=warnings,
            genres=genres,
            category=_category(genres, common_metadata, directory),
            album_type=album_type,
            composer=common_metadata["composer"],
            work=common_metadata["work"],
            conductor=common_metadata["conductor"],
            orchestra=common_metadata["orchestra"],
            soloists=_split_values(common_metadata["soloists"]),
            leader=artist if _category(genres, common_metadata, directory) == "Jazz" else None,
            session_personnel=_split_values(common_metadata["personnel"]),
            recording_date=common_metadata["recording_date"],
            original_release_year=parse_year(common_metadata["recording_date"]),
            label=common_metadata["label"],
            edition=common_metadata["edition"],
            live_studio=live_studio,
            languages=_split_values(common_metadata["language"]),
            release_countries=_split_values(common_metadata["country"]),
            scripts=_split_values(common_metadata["script"]),
            artist_aliases=_split_values(common_metadata["artist_aliases"]),
            artist_sort_name=common_metadata["artist_sort"],
            disc_total=int(common_metadata["disc_total"]) if (common_metadata["disc_total"] or "").isdigit() else None,
            tag_track_total=(
                int(common_metadata["track_total"])
                if (common_metadata["track_total"] or "").isdigit()
                else None
            ),
            acoustid_available=bool(common_metadata["acoustid_id"]),
        )
        route = language_route(album_value)
        album_value.language_bucket = str(route["bucket"])
        album_value.language_evidence = route
        if album_value.category == "Classical":
            album_value.classical_identity = build_classical_identity(album_value)
        if album_value.category == "Jazz":
            album_value.jazz_identity = build_jazz_identity(album_value)
        albums.append(album_value)
    identities = Counter((canonical_identity(album.artist, album.album), album.year) for album in albums)
    for album in albums:
        album.duplicate_local_versions = identities[(canonical_identity(album.artist, album.album), album.year)]
    return albums
