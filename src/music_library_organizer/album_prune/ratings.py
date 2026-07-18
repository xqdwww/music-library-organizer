from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .entity_resolution import query_variants
from .models import (
    MATCH_AMBIGUOUS,
    MATCH_CANONICALIZED,
    MATCH_EXACT,
    MATCH_NOT_FOUND,
    CanonicalAlbum,
    LocalAlbum,
    RatingEvidence,
)
from .normalize import canonical_identity, exact_identity, normalized_text, parse_year

MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def _write_cache(path: Path, envelope: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(envelope, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_response(response: Any) -> bytes:
    body = response.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("public metadata response exceeds 16 MiB")
    return body


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def normalize_rating(raw_score: float, raw_scale: float) -> float:
    if raw_scale <= 0 or raw_score < 0 or raw_score > raw_scale:
        raise ValueError("invalid rating scale")
    return round(raw_score / raw_scale * 100, 2)


@dataclass(slots=True)
class CachedResponse:
    body: dict[str, Any]
    path: Path
    fetched_at: str
    stale: bool


@dataclass(slots=True)
class CachedTextResponse:
    text: str
    path: Path
    fetched_at: str
    stale: bool


class HttpCache:
    def __init__(self, root: Path, *, offline: bool = False, refresh: bool = False, ttl_days: int = 30):
        self.root = root
        self.offline = offline
        self.refresh = refresh
        self.ttl = timedelta(days=ttl_days)
        self._last_request: dict[str, float] = {}

    def _path(self, source: str, url: str, adapter_version: str) -> Path:
        digest = hashlib.sha256(f"{adapter_version}\0{url}".encode()).hexdigest()
        return self.root / source / f"{digest}.json"

    def fetch_json(
        self,
        source: str,
        url: str,
        adapter_version: str,
        headers: dict[str, str],
        min_interval: float = 0,
        max_age_seconds: int | None = None,
        discard_expired: bool = False,
    ) -> CachedResponse:
        path = self._path(source, url, adapter_version)
        cached: dict[str, Any] | None = None
        if path.is_file():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None
        if cached:
            fetched = datetime.fromisoformat(str(cached["fetched_at"]))
            allowed_age = self.ttl
            if max_age_seconds is not None:
                allowed_age = min(allowed_age, timedelta(seconds=max_age_seconds))
            fresh = datetime.now(UTC) - fetched <= allowed_age
            if not fresh and discard_expired:
                path.unlink(missing_ok=True)
                cached = None
            if cached and ((fresh and not self.refresh) or self.offline):
                return CachedResponse(cached["response"], path, str(cached["fetched_at"]), not fresh)
        if self.offline:
            raise LookupError("no cached response available in offline mode")
        elapsed = time.monotonic() - self._last_request.get(source, 0)
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        request = urllib.request.Request(url, headers=headers)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    body_bytes = _read_response(response)
                    status = response.status
                self._last_request[source] = time.monotonic()
                body = json.loads(body_bytes)
                fetched_at = utc_now()
                envelope = {
                    "url": url,
                    "http_status": status,
                    "fetched_at": fetched_at,
                    "adapter_version": adapter_version,
                    "content_sha256": hashlib.sha256(body_bytes).hexdigest(),
                    "response": body,
                }
                _write_cache(path, envelope)
                return CachedResponse(body, path, fetched_at, False)
            except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.5)
        if cached:
            return CachedResponse(cached["response"], path, str(cached["fetched_at"]), True)
        assert last_error is not None
        raise last_error

    def fetch_text(
        self,
        source: str,
        url: str,
        adapter_version: str,
        headers: dict[str, str],
        min_interval: float = 0,
    ) -> CachedTextResponse:
        path = self._path(source, url, adapter_version)
        cached: dict[str, Any] | None = None
        if path.is_file():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None
        if cached and isinstance(cached.get("response"), str):
            fetched = datetime.fromisoformat(str(cached["fetched_at"]))
            fresh = datetime.now(UTC) - fetched <= self.ttl
            if (fresh and not self.refresh) or self.offline:
                return CachedTextResponse(str(cached["response"]), path, str(cached["fetched_at"]), not fresh)
        if self.offline:
            raise LookupError("no cached response available in offline mode")
        elapsed = time.monotonic() - self._last_request.get(source, 0)
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        request = urllib.request.Request(url, headers=headers)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    body_bytes = _read_response(response)
                    status = response.status
                    charset = response.headers.get_content_charset() or "utf-8"
                self._last_request[source] = time.monotonic()
                body = body_bytes.decode(charset, errors="replace").lstrip("\ufeff")
                fetched_at = utc_now()
                envelope = {
                    "url": url,
                    "http_status": status,
                    "fetched_at": fetched_at,
                    "adapter_version": adapter_version,
                    "content_sha256": hashlib.sha256(body_bytes).hexdigest(),
                    "response": body,
                }
                _write_cache(path, envelope)
                return CachedTextResponse(body, path, fetched_at, False)
            except (OSError, urllib.error.URLError, UnicodeError) as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.5)
        if cached and isinstance(cached.get("response"), str):
            return CachedTextResponse(str(cached["response"]), path, str(cached["fetched_at"]), True)
        assert last_error is not None
        raise last_error


class AlbumRatingSource(ABC):
    source_name: str
    adapter_version: str

    @abstractmethod
    def lookup_album(self, local: LocalAlbum) -> CanonicalAlbum | None:
        raise NotImplementedError

    @abstractmethod
    def fetch_rating(self, matched: CanonicalAlbum) -> RatingEvidence | None:
        raise NotImplementedError


def _artist_credit(value: dict[str, Any]) -> str:
    return "".join(
        f"{part.get('name', part.get('artist', {}).get('name', ''))}{part.get('joinphrase', '')}"
        for part in value.get("artist-credit", [])
    ).strip()


def normalized_compilation_artist(value: str) -> bool:
    return normalized_text(value) in {"various artists", "various", "soundtrack", "original soundtrack"}


def _credit_parts(value: str) -> list[str]:
    separators = (" & ", " and ", " with ", ",", ";")
    parts = [normalized_text(value)]
    for separator in separators:
        parts = [piece for part in parts for piece in part.split(separator)]
    return [part.strip().removeprefix("the ") for part in parts if part.strip()]


def _artist_credit_contains(local_names: list[str], remote: str) -> bool:
    remote_parts = _credit_parts(remote)
    for local_name in local_names:
        local_parts = _credit_parts(local_name)
        if local_parts and all(
            any(part == candidate or part in candidate or candidate in part for candidate in remote_parts)
            for part in local_parts
        ):
            return True
    return False


class MusicBrainzSource(AlbumRatingSource):
    source_name = "musicbrainz"
    adapter_version = "musicbrainz-v1"
    base_url = "https://musicbrainz.org/ws/2"

    def __init__(self, cache: HttpCache, user_agent: str):
        if not user_agent or "contact" not in user_agent.casefold():
            raise ValueError("MusicBrainz requires a meaningful User-Agent containing contact information")
        self.cache = cache
        self.headers = {"Accept": "application/json", "User-Agent": user_agent}
        self._responses: dict[str, CachedResponse] = {}
        self.last_trace: list[dict[str, Any]] = []

    def _get(self, path: str, params: dict[str, str] | None = None) -> CachedResponse:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}/{path}" + (f"?{query}" if query else "")
        return self.cache.fetch_json(self.source_name, url, self.adapter_version, self.headers, min_interval=1.1)

    def _canonical(self, row: dict[str, Any], status: str, basis: str) -> CanonicalAlbum:
        identifier = str(row["id"])
        external_ids: dict[str, str] = {}
        for relation in row.get("relations", []):
            resource = str((relation.get("url") or {}).get("resource", ""))
            if relation.get("type") == "wikidata" and resource:
                external_ids["wikidata"] = resource.rstrip("/").rsplit("/", 1)[-1]
        return CanonicalAlbum(
            canonical_album_id=f"musicbrainz:release-group:{identifier}",
            source=self.source_name,
            source_album_id=identifier,
            source_album_url=f"https://musicbrainz.org/release-group/{identifier}",
            artist=_artist_credit(row),
            album=str(row.get("title", "")),
            year=parse_year(str(row.get("first-release-date", ""))),
            primary_type=row.get("primary-type") or row.get("primary_type"),
            secondary_types=list(row.get("secondary-types", row.get("secondary_types", [])) or []),
            match_status=status,
            match_basis=basis,
            release_group_id=identifier,
            relation_type="release-group",
            external_ids=external_ids,
        )

    def _lookup_group(self, identifier: str, status: str, basis: str) -> CanonicalAlbum:
        response = self._get(
            f"release-group/{identifier}",
            {"inc": "artist-credits+ratings+url-rels", "fmt": "json"},
        )
        self._responses[identifier] = response
        return self._canonical(response.body, status, basis)

    def resolve_artist_metadata(self, local: LocalAlbum) -> dict[str, Any] | None:
        """Add exact-alias area evidence before CJK routing.

        This does not select an album or loosen album matching. Ambiguous artist
        searches return no metadata and remain visible for manual routing.
        """
        canonical_artist = canonical_identity(local.artist, "")[0]
        query_names = list(dict.fromkeys([local.artist, canonical_artist]))
        accepted: dict[str, dict[str, Any]] = {}
        local_names = {
            normalized_text(value)
            for value in [local.artist, local.artist_sort_name, canonical_artist]
            if value
        }
        candidate_count = 0
        queries: list[str] = []
        for name in query_names:
            query = f'artist:"{name}"'
            queries.append(query)
            response = self._get("artist", {"query": query, "limit": "10", "fmt": "json"})
            candidate_count += len(response.body.get("artists", []))
            for row in response.body.get("artists", []):
                aliases = [str(item.get("name", "")) for item in row.get("aliases", [])]
                remote_values = [str(row.get("name", "")), str(row.get("sort-name", "")), *aliases]
                remote_names = {
                    normalized_text(value)
                    for value in remote_values
                    if value
                } | {
                    canonical_identity(value, "")[0]
                    for value in remote_values
                    if value
                }
                if local_names & remote_names and row.get("id"):
                    accepted[str(row["id"])] = row
        trace = self._trace(
            "artist_alias_area",
            " | ".join(queries),
            candidate_count,
            "selected" if len(accepted) == 1 else "ambiguous or no exact artist",
            accepted_count=len(accepted),
            match_confidence=1.0 if len(accepted) == 1 else 0.0,
        )
        if len(accepted) != 1:
            return trace
        row = next(iter(accepted.values()))
        aliases = row.get("aliases", [])
        local.artist_aliases = list(dict.fromkeys([
            *local.artist_aliases,
            *(str(row.get(key, "")) for key in ("name", "sort-name") if row.get(key)),
            *(str(item.get("name", "")) for item in aliases if item.get("name")),
        ]))[:8]
        local.artist_sort_name = local.artist_sort_name or str(row.get("sort-name") or "") or None
        for key in ("area", "begin-area"):
            area = row.get(key) or {}
            for value in (area.get("name"), area.get("sort-name")):
                if value and str(value) not in local.artist_areas:
                    local.artist_areas.append(str(value))
        country = row.get("country")
        if country and str(country) not in local.artist_countries:
            local.artist_countries.append(str(country))
        for alias in aliases:
            locale = str(alias.get("locale") or "").casefold()
            language = locale.split("_", 1)[0].split("-", 1)[0]
            if language in {"ja", "zh", "ko"} and language not in local.languages:
                local.languages.append(language)
        trace["selected_candidate"] = str(row["id"])
        trace["match_features"] = ["exact artist name or alias", "artist area", "alias locale"]
        return trace

    def lookup_album(self, local: LocalAlbum) -> CanonicalAlbum | None:
        self.last_trace = []
        if local.release_group_mbid:
            self.last_trace.append(self._trace("identifier_release_group", local.release_group_mbid, 1, "selected"))
            return self._lookup_group(local.release_group_mbid, MATCH_EXACT, "embedded release-group MBID")
        if local.release_mbid:
            response = self._get(f"release/{local.release_mbid}", {"inc": "release-groups", "fmt": "json"})
            group = response.body.get("release-group")
            if group and group.get("id"):
                self.last_trace.append(self._trace("identifier_release", local.release_mbid, 1, "selected"))
                return self._lookup_group(
                    str(group["id"]), MATCH_EXACT, "embedded release MBID resolved to release-group"
                )
        identifier_match = self._identifier_search(local)
        if identifier_match is not None:
            return identifier_match
        for variant in query_variants(local)[:4]:
            query = f'artist:"{variant["artist"]}" AND releasegroup:"{variant["album"]}"'
            response = self._get("release-group", {"query": query, "limit": "10", "fmt": "json"})
            result = self._select_group(local, response.body.get("release-groups", []), query, variant["kind"])
            if result is not None:
                return result
        if local.album_type in {"Compilation", "Soundtrack"}:
            query = f'releasegroup:"{local.album}"'
            response = self._get("release-group", {"query": query, "limit": "10", "fmt": "json"})
            result = self._select_group(local, response.body.get("release-groups", []), query, "title_only_compilation")
            if result is not None:
                return result
        release_version = self._release_version_search(local)
        if release_version is not None:
            return release_version
        self.last_trace.append(self._trace("manual_review", "", 0, "no controlled candidate"))
        return CanonicalAlbum(
            canonical_album_id=local.album_id,
            source=self.source_name,
            source_album_id="",
            source_album_url="",
            artist=local.artist,
            album=local.album,
            year=local.year,
            primary_type=None,
            secondary_types=[],
            match_status=MATCH_NOT_FOUND,
            match_basis="no controlled staged match",
        )

    @staticmethod
    def _trace(resolver: str, query: str, count: int, outcome: str, **extra: Any) -> dict[str, Any]:
        return {
            "resolver_name": resolver,
            "resolver_version": MusicBrainzSource.adapter_version,
            "query": query,
            "candidate_count": count,
            "selected_candidate": extra.pop("selected_candidate", None),
            "match_features": extra.pop("match_features", []),
            "match_confidence": extra.pop("match_confidence", 0.0),
            "rejection_reason": None if outcome == "selected" else outcome,
            "resolved_at": utc_now(),
            **extra,
        }

    def _identifier_search(self, local: LocalAlbum) -> CanonicalAlbum | None:
        searches: list[tuple[str, str]] = []
        if local.barcode:
            searches.append(("barcode", f'barcode:"{local.barcode}"'))
        if local.catalog_number:
            query = f'catno:"{local.catalog_number}"'
            if local.artist:
                query += f' AND artist:"{local.artist}"'
            searches.append(("catalog_number", query))
        for resolver, query in searches:
            response = self._get("release", {"query": query, "limit": "10", "fmt": "json"})
            groups: dict[str, dict[str, Any]] = {}
            for release in response.body.get("releases", []):
                group = release.get("release-group") or {}
                if group.get("id"):
                    groups[str(group["id"])] = group
            self.last_trace.append(self._trace(resolver, query, len(groups), "candidate_generation"))
            if len(groups) == 1:
                identifier = next(iter(groups))
                self.last_trace.append(
                    self._trace(
                        resolver,
                        query,
                        1,
                        "selected",
                        selected_candidate=identifier,
                        match_confidence=1.0,
                    )
                )
                return self._lookup_group(identifier, MATCH_EXACT, f"{resolver} resolved to release-group")
            if len(groups) > 1:
                self.last_trace.append(self._trace(resolver, query, len(groups), "multiple release-groups"))
        return None

    def _release_version_search(self, local: LocalAlbum) -> CanonicalAlbum | None:
        if not local.year:
            return None
        local_names = [local.artist, *local.artist_aliases]
        if local.artist_sort_name:
            local_names.append(local.artist_sort_name)
        local_album = canonical_identity(local.artist, local.album)[1]
        for variant in query_variants(local)[:4]:
            query = (
                f'artist:"{variant["artist"]}" AND release:"{variant["album"]}" '
                f"AND date:{local.year}"
            )
            response = self._get("release", {"query": query, "limit": "10", "fmt": "json"})
            groups: dict[str, dict[str, Any]] = {}
            for release in response.body.get("releases", []):
                title = str(release.get("title", ""))
                artist = _artist_credit(release)
                year = parse_year(str(release.get("date", "")))
                group = release.get("release-group") or {}
                if (
                    group.get("id")
                    and canonical_identity(local.artist, title)[1] == local_album
                    and _artist_credit_contains(local_names, artist)
                    and year
                    and abs(year - local.year) <= 1
                ):
                    groups[str(group["id"])] = group
            self.last_trace.append(
                self._trace(
                    "release_version",
                    query,
                    len(response.body.get("releases", [])),
                    "candidate_generation",
                    accepted_count=len(groups),
                )
            )
            if len(groups) == 1:
                identifier = next(iter(groups))
                result = self._lookup_group(
                    identifier,
                    MATCH_CANONICALIZED,
                    "release-version artist + album + year resolved to release-group",
                )
                result.relation_type = "reissue-release-to-group"
                self.last_trace.append(
                    self._trace(
                        "release_version",
                        query,
                        1,
                        "selected",
                        selected_candidate=identifier,
                        match_features=["release artist", "release title", "release year", "release-group"],
                        match_confidence=0.95,
                    )
                )
                return result
        return None

    def _select_group(
        self,
        local: LocalAlbum,
        rows: list[dict[str, Any]],
        query: str,
        stage: str,
    ) -> CanonicalAlbum | None:
        local_artists = [local.artist, *local.artist_aliases]
        if local.artist_sort_name:
            local_artists.append(local.artist_sort_name)
        exact_locals = {exact_identity(artist, local.album) for artist in local_artists if artist}
        canonical_locals = {canonical_identity(artist, local.album) for artist in local_artists if artist}
        canonical_album = canonical_identity(local.artist, local.album)[1]
        matches: list[tuple[dict[str, Any], str]] = []
        for row in rows:
            artist = _artist_credit(row)
            title = str(row.get("title", ""))
            status = None
            if exact_identity(artist, title) in exact_locals:
                status = MATCH_EXACT
            elif canonical_identity(artist, title) in canonical_locals:
                status = MATCH_CANONICALIZED
            elif (
                stage == "title_only_compilation"
                and canonical_identity(local.artist, title)[1] == canonical_album
                and normalized_compilation_artist(artist)
            ):
                status = MATCH_CANONICALIZED
            if not status:
                continue
            remote_year = parse_year(str(row.get("first-release-date", "")))
            if local.year and remote_year and abs(local.year - remote_year) > 2:
                continue
            matches.append((row, status))
        self.last_trace.append(
            self._trace(stage, query, len(rows), "candidate_generation", accepted_count=len(matches))
        )
        if not matches:
            return None
        unique = {str(row["id"]): (row, status) for row, status in matches}
        if len(unique) != 1:
            first = next(iter(unique.values()))[0]
            result = self._canonical(first, MATCH_AMBIGUOUS, f"{len(unique)} controlled matches")
            result.canonical_album_id = local.album_id
            self.last_trace.append(self._trace(stage, query, len(unique), "ambiguous controlled matches"))
            return result
        row, status = next(iter(unique.values()))
        identifier = str(row["id"])
        self.last_trace.append(self._trace(
            stage,
            query,
            1,
            "selected",
            selected_candidate=identifier,
            match_features=["artist", "album", *( ["year"] if local.year else [])],
            match_confidence=1.0 if status == MATCH_EXACT else 0.9,
        ))
        basis = f"{stage}: controlled artist + album" + (" + year" if local.year else "")
        return self._lookup_group(identifier, status, basis)

    def fetch_rating(self, matched: CanonicalAlbum) -> RatingEvidence | None:
        if not matched.source_album_id:
            return None
        response = self._responses.get(matched.source_album_id)
        if response is None:
            response = self._get(
                f"release-group/{matched.source_album_id}",
                {"inc": "artist-credits+ratings+url-rels", "fmt": "json"},
            )
        rating = response.body.get("rating") or {}
        value = rating.get("value")
        count = rating.get("votes-count", rating.get("votes_count"))
        if value is None or not count:
            return None
        raw = float(value)
        return RatingEvidence(
            source=self.source_name,
            source_album_id=matched.source_album_id,
            source_album_url=matched.source_album_url,
            raw_score=raw,
            raw_scale=5,
            normalized_score_100=normalize_rating(raw, 5),
            rating_count=int(count),
            review_count=None,
            critic_or_community="community",
            matched_artist=matched.artist,
            matched_album=matched.album,
            matched_year=matched.year,
            match_basis=matched.match_basis,
            fetched_at=response.fetched_at,
            response_cache_path=str(response.path),
            adapter_version=self.adapter_version,
            stale=response.stale,
        )


class DiscogsSource(AlbumRatingSource):
    source_name = "discogs"
    adapter_version = "discogs-v2"
    base_url = "https://api.discogs.com"

    def __init__(self, cache: HttpCache, user_agent: str, token: str | None = None):
        self.cache = cache
        self.token = token or os.environ.get("DISCOGS_TOKEN")
        self.headers = {"Accept": "application/json", "User-Agent": user_agent}
        if self.token:
            self.headers["Authorization"] = f"Discogs token={self.token}"
        self._responses: dict[str, CachedResponse] = {}
        self.last_trace: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return True

    def _get(self, path: str, params: dict[str, str] | None = None) -> CachedResponse:
        query = urllib.parse.urlencode(params or {})
        url = f"{self.base_url}/{path}" + (f"?{query}" if query else "")
        return self.cache.fetch_json(
            self.source_name,
            url,
            self.adapter_version,
            self.headers,
            min_interval=2.5,
            max_age_seconds=6 * 60 * 60,
            discard_expired=True,
        )

    def lookup_album(self, local: LocalAlbum) -> CanonicalAlbum | None:
        self.last_trace = []
        if local.discogs_id:
            release_response = self._get(f"releases/{local.discogs_id}")
            master_id = release_response.body.get("master_id")
            if not master_id:
                return None
            identifier = str(master_id)
            response = self._get(f"masters/{identifier}")
            status, basis = MATCH_EXACT, "embedded Discogs release ID"
            row = response.body
            self.last_trace.append(self._trace("identifier_release", local.discogs_id, 1, "selected"))
        else:
            candidates: list[tuple[str, str, str]] = []
            stages: list[tuple[str, dict[str, str]]] = []
            if local.barcode:
                stages.append(("barcode", {"type": "master", "barcode": local.barcode, "per_page": "10"}))
            if local.catalog_number:
                stages.append(("catalog_number", {
                    "type": "master", "catno": local.catalog_number, "artist": local.artist, "per_page": "10"
                }))
            for variant in query_variants(local):
                params = {
                    "type": "master",
                    "artist": str(variant["artist"]),
                    "release_title": str(variant["album"]),
                    "per_page": "10",
                }
                if local.year:
                    params["year"] = str(local.year)
                stages.append((str(variant["kind"]), params))
            if local.album_type in {"Compilation", "Soundtrack"}:
                stages.append(("title_only_compilation", {
                    "type": "master", "release_title": local.album, "per_page": "10"
                }))
            selected_stage = ""
            for stage, params in stages:
                search = self._get("database/search", params)
                candidates = self._discogs_candidates(local, search.body.get("results", []), stage)
                self.last_trace.append(
                    self._trace(
                        stage,
                        urllib.parse.urlencode(params),
                        len(search.body.get("results", [])),
                        "candidate_generation",
                        accepted_count=len(candidates),
                    )
                )
                if len(candidates) == 1:
                    selected_stage = stage
                    break
                if len(candidates) > 1:
                    self.last_trace.append(
                        self._trace(
                            stage,
                            urllib.parse.urlencode(params),
                            len(candidates),
                            "ambiguous controlled matches",
                        )
                    )
                    return None
            if len(candidates) != 1:
                self.last_trace.append(self._trace("manual_review", "", 0, "no controlled candidate"))
                return None
            identifier, status, _ = candidates[0]
            response = self._get(f"masters/{identifier}")
            row = response.body
            basis = f"{selected_stage}: controlled artist + album" + (" + year" if local.year else "")
            self.last_trace.append(
                self._trace(
                    selected_stage,
                    "",
                    1,
                    "selected",
                    selected_candidate=identifier,
                    match_confidence=1.0 if status == MATCH_EXACT else 0.9,
                )
            )
        main_release = row.get("main_release")
        if not main_release:
            return None
        release_response = self._get(f"releases/{main_release}")
        self._responses[str(identifier)] = release_response
        artists = ", ".join(a.get("name", "") for a in row.get("artists", []))
        return CanonicalAlbum(
            canonical_album_id=f"discogs:master:{identifier}",
            source=self.source_name,
            source_album_id=str(identifier),
            source_album_url=str(row.get("uri") or f"https://www.discogs.com/master/{identifier}"),
            artist=artists,
            album=str(row.get("title", "")),
            year=int(row["year"]) if row.get("year") else None,
            primary_type="Album",
            secondary_types=[],
            match_status=status,
            match_basis=basis,
            master_id=str(identifier),
            main_release_id=str(main_release),
            release_id=str(main_release),
            label=", ".join(item.get("name", "") for item in release_response.body.get("labels", [])) or None,
            catalog_number=", ".join(item.get("catno", "") for item in release_response.body.get("labels", [])) or None,
            track_count=len(release_response.body.get("tracklist", [])) or None,
            relation_type="master-main-release",
        )

    @staticmethod
    def _trace(resolver: str, query: str, count: int, outcome: str, **extra: Any) -> dict[str, Any]:
        return {
            "resolver_name": resolver,
            "resolver_version": DiscogsSource.adapter_version,
            "query": query,
            "candidate_count": count,
            "selected_candidate": extra.pop("selected_candidate", None),
            "match_features": extra.pop("match_features", []),
            "match_confidence": extra.pop("match_confidence", 0.0),
            "rejection_reason": None if outcome == "selected" else outcome,
            "resolved_at": utc_now(),
            **extra,
        }

    @staticmethod
    def _discogs_candidates(
        local: LocalAlbum,
        results: list[dict[str, Any]],
        stage: str,
    ) -> list[tuple[str, str, str]]:
        local_artists = [local.artist, *local.artist_aliases]
        if local.artist_sort_name:
            local_artists.append(local.artist_sort_name)
        exact_locals = {exact_identity(artist, local.album) for artist in local_artists if artist}
        canonical_locals = {canonical_identity(artist, local.album) for artist in local_artists if artist}
        canonical_album = canonical_identity(local.artist, local.album)[1]
        candidates = []
        for result in results:
            identifier = str(result.get("id", ""))
            if not identifier:
                continue
            title_value = str(result.get("title", ""))
            search_artist, separator, title = title_value.partition(" - ")
            artists = search_artist if separator else local.artist
            status = None
            if exact_identity(artists, title) in exact_locals:
                status = MATCH_EXACT
            elif canonical_identity(artists, title) in canonical_locals:
                status = MATCH_CANONICALIZED
            elif (
                stage == "title_only_compilation"
                and canonical_identity(local.artist, title)[1] == canonical_album
                and normalized_compilation_artist(artists)
            ):
                status = MATCH_CANONICALIZED
            if not status:
                continue
            remote_year = parse_year(str(result.get("year", "")))
            if local.year and remote_year and abs(local.year - remote_year) > 2:
                continue
            candidates.append((identifier, status, title_value))
        return candidates

    def fetch_rating(self, matched: CanonicalAlbum) -> RatingEvidence | None:
        response = self._responses.get(matched.source_album_id)
        if response is None:
            master = self._get(f"masters/{matched.source_album_id}")
            main_release = master.body.get("main_release")
            if not main_release:
                return None
            response = self._get(f"releases/{main_release}")
        rating = (response.body.get("community") or {}).get("rating") or {}
        average, count = rating.get("average"), rating.get("count")
        if average is None or not count:
            return None
        raw = float(average)
        return RatingEvidence(
            source=self.source_name,
            source_album_id=f"master:{matched.source_album_id}/release:{response.body.get('id')}",
            source_album_url=str(response.body.get("uri") or matched.source_album_url),
            raw_score=raw,
            raw_scale=5,
            normalized_score_100=normalize_rating(raw, 5),
            rating_count=int(count),
            review_count=None,
            critic_or_community="community",
            matched_artist=matched.artist,
            matched_album=matched.album,
            matched_year=matched.year,
            match_basis=f"{matched.match_basis}; Discogs master aligned, main release community rating",
            fetched_at=response.fetched_at,
            response_cache_path=str(response.path),
            adapter_version=self.adapter_version,
            stale=response.stale,
        )


class CritiqueBrainzSource:
    source_name = "critiquebrainz"
    adapter_version = "critiquebrainz-v1"
    base_url = "https://critiquebrainz.org/ws/1"

    def __init__(self, cache: HttpCache, user_agent: str):
        self.cache = cache
        self.headers = {"Accept": "application/json", "User-Agent": user_agent}
        self._responses: dict[str, CachedResponse] = {}

    def _get(self, release_group_id: str) -> CachedResponse:
        params = {
            "entity_id": release_group_id,
            "entity_type": "release_group",
            "review_type": "rating",
            "include_metadata": "true",
            "limit": "50",
        }
        url = f"{self.base_url}/review/?{urllib.parse.urlencode(params)}"
        return self.cache.fetch_json(self.source_name, url, self.adapter_version, self.headers, min_interval=1.0)

    @staticmethod
    def release_group_id(matched: CanonicalAlbum) -> str | None:
        if matched.release_group_id:
            return matched.release_group_id
        if matched.source == "musicbrainz" and matched.source_album_id:
            return matched.source_album_id
        if matched.canonical_album_id.startswith("musicbrainz:release-group:"):
            return matched.canonical_album_id.rsplit(":", 1)[-1]
        return None

    def fetch_rating(self, matched: CanonicalAlbum) -> RatingEvidence | None:
        identifier = self.release_group_id(matched)
        if not identifier:
            return None
        response = self._get(identifier)
        self._responses[identifier] = response
        average = response.body.get("average_rating") or {}
        value = average.get("rating", average.get("average"))
        count = average.get("count", average.get("rating_count"))
        if value is None or not count:
            return None
        raw = float(value)
        return RatingEvidence(
            source=self.source_name,
            source_album_id=identifier,
            source_album_url=f"https://critiquebrainz.org/release-group/{identifier}",
            raw_score=raw,
            raw_scale=5,
            normalized_score_100=normalize_rating(raw, 5),
            rating_count=int(count),
            review_count=len(response.body.get("reviews", [])),
            critic_or_community="community",
            matched_artist=matched.artist,
            matched_album=matched.album,
            matched_year=matched.year,
            match_basis="exact MusicBrainz release-group identifier",
            fetched_at=response.fetched_at,
            response_cache_path=str(response.path),
            adapter_version=self.adapter_version,
            stale=response.stale,
        )

    def professional_evidence(self, matched: CanonicalAlbum) -> list[dict[str, Any]]:
        identifier = self.release_group_id(matched)
        if not identifier:
            return []
        response = self._responses.get(identifier) or self._get(identifier)
        rows = []
        for review in response.body.get("reviews", []):
            source = review.get("source")
            source_url = review.get("source_url")
            license_value = review.get("license") or {}
            if not source or not source_url:
                continue
            rows.append({
                "publication": str(source),
                "source_entity_id": str(review.get("id", "")),
                "source_reference": str(source_url),
                "reviewer": str((review.get("user") or {}).get("display_name", "")),
                "issue_or_edition": "",
                "rating_raw": review.get("rating"),
                "rating_scale": 5 if review.get("rating") is not None else None,
                "award": "",
                "recommendation": "",
                "review_date": str(review.get("published_on") or review.get("created") or ""),
                "recording_identity": f"musicbrainz:release-group:{identifier}",
                "match_basis": "exact MusicBrainz release-group identifier",
                "license": str(license_value.get("id", "")),
                "fetched_at": response.fetched_at,
            })
        return rows
