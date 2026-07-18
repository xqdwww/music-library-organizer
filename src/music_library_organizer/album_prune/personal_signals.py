from __future__ import annotations

import csv
import json
import math
import plistlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import AlbumReview
from .normalize import canonical_identity


@dataclass(slots=True)
class PersonalSignal:
    source: str = "NONE"
    source_album_key: str = ""
    matched_track_count: int = 0
    source_track_count: int = 0
    local_track_count: int = 0
    match_rate: float = 0.0
    match_confidence: float = 0.0
    play_count: int | None = None
    last_played_at: str | None = None
    rating: float | None = None
    favorite: bool = False
    playlist_count: int = 0
    library_added_date: str | None = None
    observed: bool = False
    ambiguous_release_match: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class NeteaseCommunitySignal:
    source: str = "NONE"
    source_album_id: str = ""
    score: float | None = None
    comment_count: int | None = None
    matched_song_count: int = 0
    source_song_count: int = 0
    local_track_count: int = 0
    song_match_rate: float = 0.0
    source_confidence: str = "NOT_AVAILABLE"
    accepted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class _AppleAggregate:
    artist: str
    album: str
    track_ids: set[str] = field(default_factory=set)
    play_count: int = 0
    last_played_at: str | None = None
    ratings: list[float] = field(default_factory=list)
    favorite: bool = False
    playlist_names: set[str] = field(default_factory=set)
    added_dates: list[str] = field(default_factory=list)


def _iso(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def _number(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "y", "loved", "favorite", "favourite"}


def _field(row: dict[str, Any], *names: str) -> Any:
    normalized = {str(key).strip().casefold().replace(" ", "_"): value for key, value in row.items()}
    for name in names:
        key = name.casefold().replace(" ", "_")
        if key in normalized:
            return normalized[key]
    return None


def _load_apple_rows(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    path = path.expanduser().resolve()
    if path.suffix.casefold() == ".musiclibrary" or path.name.endswith(".musiclibrary"):
        raise ValueError(
            "Music Library.musiclibrary uses Apple's private musicdb format; export the library as XML first"
        )
    if path.suffix.casefold() == ".xml":
        with path.open("rb") as stream:
            payload = plistlib.load(stream)
        tracks = list((payload.get("Tracks") or {}).values())
        playlists = list(payload.get("Playlists") or [])
        return tracks, playlists, "APPLE_MUSIC_XML"
    if path.suffix.casefold() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return payload, [], "APPLE_MUSIC_JSON"
        if not isinstance(payload, dict):
            raise ValueError("Apple JSON must be an object or list")
        return list(payload.get("tracks") or []), list(payload.get("playlists") or []), "APPLE_MUSIC_JSON"
    if path.suffix.casefold() in {".csv", ".tsv"}:
        delimiter = "\t" if path.suffix.casefold() == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream, delimiter=delimiter)), [], "APPLE_MUSIC_TABULAR"
    raise ValueError("Apple personal data must be exported as XML, JSON, CSV, or TSV")


def load_apple_album_signals(path: Path) -> tuple[dict[tuple[str, str], _AppleAggregate], dict[str, Any]]:
    tracks, playlists, source = _load_apple_rows(path)
    playlist_membership: dict[str, set[str]] = defaultdict(set)
    for playlist in playlists:
        name = str(_field(playlist, "Name", "name") or "Unnamed Playlist")
        for item in _field(playlist, "Playlist Items", "playlist_items", "items") or []:
            if not isinstance(item, dict):
                continue
            track_id = _field(item, "Track ID", "track_id", "id")
            if track_id is not None:
                playlist_membership[str(track_id)].add(name)

    albums: dict[tuple[str, str], _AppleAggregate] = {}
    skipped = 0
    for index, row in enumerate(tracks):
        if not isinstance(row, dict):
            skipped += 1
            continue
        artist = str(_field(row, "Album Artist", "album_artist", "Artist", "artist") or "").strip()
        album = str(_field(row, "Album", "album") or "").strip()
        if not artist or not album:
            skipped += 1
            continue
        key = canonical_identity(artist, album)
        aggregate = albums.setdefault(key, _AppleAggregate(artist=artist, album=album))
        track_id = str(_field(row, "Track ID", "track_id", "id") or f"row-{index}")
        aggregate.track_ids.add(track_id)
        aggregate.play_count += _number(_field(row, "Play Count", "play_count"))
        last_played = _iso(_field(row, "Play Date UTC", "play_date_utc", "Last Played", "last_played_at"))
        if last_played and (aggregate.last_played_at is None or last_played > aggregate.last_played_at):
            aggregate.last_played_at = last_played
        rating = _float(_field(row, "Rating", "rating"))
        if rating is not None and rating > 0:
            aggregate.ratings.append(max(0.0, min(100.0, rating)))
        aggregate.favorite = aggregate.favorite or _bool(_field(row, "Loved", "Favorite", "favorite"))
        aggregate.playlist_names.update(playlist_membership.get(track_id, set()))
        added = _iso(_field(row, "Date Added", "date_added", "library_added_date"))
        if added:
            aggregate.added_dates.append(added)
    return albums, {
        "source": source,
        "source_path": str(path.expanduser().resolve()),
        "tracks": len(tracks),
        "albums": len(albums),
        "playlists": len(playlists),
        "skipped_tracks": skipped,
    }


def match_apple_signals(
    reviews: list[AlbumReview],
    source: Path | None,
) -> tuple[dict[str, PersonalSignal], dict[str, Any]]:
    if source is None:
        return {}, {"status": "NOT_PROVIDED", "albums": 0, "matched_albums": 0}
    albums, summary = load_apple_album_signals(source)
    local_keys: dict[tuple[str, str], list[AlbumReview]] = defaultdict(list)
    for review in reviews:
        local_keys[canonical_identity(review.local.artist, review.local.album)].append(review)
    result: dict[str, PersonalSignal] = {}
    for key, local_rows in local_keys.items():
        aggregate = albums.get(key)
        if aggregate is None:
            continue
        for review in local_rows:
            source_tracks = len(aggregate.track_ids)
            local_tracks = review.local.track_count
            matched = min(source_tracks, local_tracks)
            match_rate = matched / max(1, local_tracks)
            ambiguous = len(local_rows) > 1
            result[review.local.album_id] = PersonalSignal(
                source=str(summary["source"]),
                source_album_key="\0".join(key),
                matched_track_count=matched,
                source_track_count=source_tracks,
                local_track_count=local_tracks,
                match_rate=round(match_rate, 4),
                match_confidence=round(match_rate * (0.8 if ambiguous else 1.0), 4),
                play_count=aggregate.play_count,
                last_played_at=aggregate.last_played_at,
                rating=round(sum(aggregate.ratings) / len(aggregate.ratings), 2) if aggregate.ratings else None,
                favorite=aggregate.favorite,
                playlist_count=len(aggregate.playlist_names),
                library_added_date=min(aggregate.added_dates) if aggregate.added_dates else None,
                observed=match_rate >= 0.5,
                ambiguous_release_match=ambiguous,
            )
    return result, {**summary, "status": "IMPORTED", "matched_albums": len(result)}


def _load_netease_rows(path: Path) -> list[dict[str, Any]]:
    path = path.expanduser().resolve()
    if path.suffix.casefold() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("albums") or payload.get("results") or []
        if not isinstance(payload, list):
            raise ValueError("Netease JSON must contain an album list")
        return [row for row in payload if isinstance(row, dict)]
    if path.suffix.casefold() in {".csv", ".tsv"}:
        delimiter = "\t" if path.suffix.casefold() == ".tsv" else ","
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream, delimiter=delimiter))
    raise ValueError("Netease community data must be JSON, CSV, or TSV")


def match_netease_signals(
    reviews: list[AlbumReview],
    source: Path | None,
) -> tuple[dict[str, NeteaseCommunitySignal], dict[str, Any]]:
    if source is None:
        return {}, {"status": "NOT_PROVIDED", "albums": 0, "accepted_albums": 0}
    source_rows = _load_netease_rows(source)
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in source_rows:
        artist = str(_field(row, "artist", "album_artist") or "").strip()
        album = str(_field(row, "album", "album_name") or "").strip()
        if artist and album:
            by_key[canonical_identity(artist, album)] = row
    result: dict[str, NeteaseCommunitySignal] = {}
    for review in reviews:
        row = by_key.get(canonical_identity(review.local.artist, review.local.album))
        if row is None:
            continue
        matched = _number(_field(row, "matched_song_count", "matched_songs"))
        source_total = _number(_field(row, "source_song_count", "total_songs"), review.local.track_count)
        local_total = max(1, review.local.track_count)
        required = max(1, math.ceil(local_total * 2 / 3))
        rate = matched / local_total
        score = _float(_field(row, "netease_score", "score", "rating"))
        if score is not None and score <= 10:
            score *= 10
        accepted = matched >= required and rate >= 2 / 3 and score is not None
        result[review.local.album_id] = NeteaseCommunitySignal(
            source="NETEASE_IMPORT",
            source_album_id=str(_field(row, "album_id", "source_album_id", "id") or ""),
            score=round(max(0.0, min(100.0, score)), 2) if score is not None else None,
            comment_count=_number(_field(row, "comment_count", "netease_comment_count")),
            matched_song_count=matched,
            source_song_count=source_total,
            local_track_count=local_total,
            song_match_rate=round(rate, 4),
            source_confidence="ALBUM_TRACK_COVERAGE_ACCEPTED" if accepted else "INSUFFICIENT_TRACK_COVERAGE",
            accepted=accepted,
        )
    return result, {
        "status": "IMPORTED",
        "source_path": str(source.expanduser().resolve()),
        "albums": len(source_rows),
        "matched_albums": len(result),
        "accepted_albums": sum(row.accepted for row in result.values()),
    }
