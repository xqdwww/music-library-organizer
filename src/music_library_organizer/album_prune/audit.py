from __future__ import annotations

import re

from .models import ALLOWED_MATCHES, AlbumReview
from .normalize import canonical_identity, exact_identity
from .ratings import normalized_compilation_artist

EDITION = re.compile(
    r"deluxe|expanded|anniversary|remaster|bonus|japan(?:ese)? edition|mono|stereo|super deluxe",
    re.IGNORECASE,
)
DISC = re.compile(r"(?:cd|disc|disk|vol(?:ume)?)[ ._-]*\d+", re.IGNORECASE)
NON_LATIN = re.compile(r"[^\x00-\x7f]")


def classify_unresolved_root_cause(review: AlbumReview, error: str | None) -> str:
    local = review.local
    if local.artist in {"", "Unknown Artist"} or local.album in {"", "Unknown Album"}:
        return "MISSING_LOCAL_METADATA"
    if local.scan_warnings:
        return "BAD_LOCAL_METADATA"
    if local.category == "Classical":
        return "CLASSICAL_ENTITY_MODEL_MISMATCH"
    if local.category == "Jazz":
        return "JAZZ_SESSION_MODEL_MISMATCH"
    if local.album_type == "Soundtrack":
        return "SOUNDTRACK_OR_VARIOUS_ARTISTS"
    if local.album_type == "Compilation" or local.artist.casefold() in {"various artists", "soundtrack"}:
        return "COMPILATION_STRUCTURE"
    if local.duplicate_local_versions > 1 or DISC.search(local.relative_path):
        return "MULTIDISC_BOXSET"
    if EDITION.search(local.album):
        return "TITLE_EDITION_SUFFIX"
    if local.rating_scope == "REVIEW_CJK_LANGUAGE":
        return "LANGUAGE_BUCKET_ERROR"
    if NON_LATIN.search(f"{local.artist} {local.album}"):
        return "NON_LATIN_NORMALIZATION"
    if not local.year:
        return "YEAR_MISMATCH"
    if error and "no cached response" in error.casefold():
        return "REMOTE_RATE_LIMIT_OR_FAILURE"
    return "UNKNOWN"


def match_invariant_violation(review: AlbumReview) -> str | None:
    matched = review.canonical
    if matched is None or matched.match_status not in ALLOWED_MATCHES:
        return None
    basis = matched.match_basis.casefold()
    if any(marker in basis for marker in ("embedded ", "barcode resolved", "catalog_number resolved")):
        return None
    artists = [review.local.artist, *review.local.artist_aliases]
    if review.local.artist_sort_name:
        artists.append(review.local.artist_sort_name)
    exact_locals = {exact_identity(artist, review.local.album) for artist in artists if artist}
    canonical_locals = {canonical_identity(artist, review.local.album) for artist in artists if artist}
    remote_exact = exact_identity(matched.artist, matched.album)
    remote_canonical = canonical_identity(matched.artist, matched.album)
    compilation_match = (
        "title_only_compilation" in basis
        and remote_canonical[1] == canonical_identity(review.local.artist, review.local.album)[1]
        and normalized_compilation_artist(matched.artist)
    )
    if remote_exact not in exact_locals and remote_canonical not in canonical_locals and not compilation_match:
        return "artist_album_identity_mismatch"
    if review.local.year and matched.year and abs(review.local.year - matched.year) > 2:
        return "year_mismatch"
    return None
