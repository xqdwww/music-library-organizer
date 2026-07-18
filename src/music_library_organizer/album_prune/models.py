from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

MATCH_EXACT = "EXACT"
MATCH_CANONICALIZED = "CANONICALIZED"
MATCH_REVIEW = "LIKELY_NEEDS_REVIEW"
MATCH_AMBIGUOUS = "AMBIGUOUS"
MATCH_NOT_FOUND = "NOT_FOUND"

ALLOWED_MATCHES = {MATCH_EXACT, MATCH_CANONICALIZED}
EXCLUDED_SECONDARY_TYPES = {
    "audiobook",
    "compilation",
    "demo",
    "dj-mix",
    "field recording",
    "interview",
    "live",
    "mixtape/street",
    "remix",
    "soundtrack",
    "spokenword",
}


@dataclass(slots=True)
class LocalAlbum:
    album_id: str
    path: str
    relative_path: str
    artist: str
    album: str
    year: int | None
    track_count: int
    file_count: int
    size_bytes: int
    formats: list[str]
    fingerprint: str
    release_group_mbid: str | None = None
    release_mbid: str | None = None
    barcode: str | None = None
    catalog_number: str | None = None
    discogs_id: str | None = None
    duplicate_local_versions: int = 1
    safe_directory: bool = True
    scan_warnings: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    category: str = "Other"
    album_type: str = "Other"
    composer: str | None = None
    work: str | None = None
    conductor: str | None = None
    orchestra: str | None = None
    soloists: list[str] = field(default_factory=list)
    leader: str | None = None
    session_personnel: list[str] = field(default_factory=list)
    recording_date: str | None = None
    original_release_year: int | None = None
    label: str | None = None
    edition: str | None = None
    live_studio: str | None = None
    professional_evidence: list[dict[str, Any]] = field(default_factory=list)
    historical_or_catalog_significance: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    release_countries: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    artist_aliases: list[str] = field(default_factory=list)
    artist_sort_name: str | None = None
    artist_countries: list[str] = field(default_factory=list)
    artist_areas: list[str] = field(default_factory=list)
    disc_total: int | None = None
    tag_track_total: int | None = None
    acoustid_available: bool = False
    language_bucket: str = "UNKNOWN_CJK"
    language_evidence: dict[str, Any] = field(default_factory=dict)
    classical_identity: dict[str, Any] = field(default_factory=dict)
    jazz_identity: dict[str, Any] = field(default_factory=dict)
    rating_scope: str = "UNCLASSIFIED"
    rating_scope_reason: str = ""


@dataclass(slots=True)
class CanonicalAlbum:
    canonical_album_id: str
    source: str
    source_album_id: str
    source_album_url: str
    artist: str
    album: str
    year: int | None
    primary_type: str | None
    secondary_types: list[str]
    match_status: str
    match_basis: str
    release_id: str | None = None
    release_group_id: str | None = None
    master_id: str | None = None
    main_release_id: str | None = None
    label: str | None = None
    catalog_number: str | None = None
    barcode: str | None = None
    track_count: int | None = None
    relation_type: str | None = None
    external_ids: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RatingEvidence:
    source: str
    source_album_id: str
    source_album_url: str
    raw_score: float
    raw_scale: float
    normalized_score_100: float
    rating_count: int | None
    review_count: int | None
    critic_or_community: str
    matched_artist: str
    matched_album: str
    matched_year: int | None
    match_basis: str
    fetched_at: str
    response_cache_path: str
    adapter_version: str
    stale: bool = False


@dataclass(slots=True)
class ProfessionalEvidence:
    publication: str
    publication_type: str
    source_title: str
    source_url: str
    source_entity_id: str
    evidence_type: str
    reviewer: str = ""
    issue: str = ""
    edition: str = ""
    raw_rating: str | float | None = None
    raw_scale: str | float | None = None
    star_rating: float | None = None
    award: str = ""
    recommendation: str = ""
    ranking: str = ""
    reference_recording_status: str = ""
    review_date: str = ""
    recording_identity: str = ""
    match_features: list[str] = field(default_factory=list)
    match_confidence: float = 0.0
    fetched_at: str = ""
    adapter_version: str = ""
    raw_evidence_hash: str = ""
    normalized_score_100: float | None = None
    conversion_rule: str = ""
    protection_reason: str = ""
    stale: bool = False


@dataclass(slots=True)
class AlbumReview:
    local: LocalAlbum
    canonical: CanonicalAlbum | None
    evidence: list[RatingEvidence] = field(default_factory=list)
    professional_evidence: list[ProfessionalEvidence] = field(default_factory=list)
    community_score: float | None = None
    critic_score: float | None = None
    professional_score: float | None = None
    professional_confidence: float = 0.0
    professional_source_count: int = 0
    professional_recommendation_count: int = 0
    professional_award_count: int = 0
    protection_reasons: list[str] = field(default_factory=list)
    music_score: float | None = None
    rating_status: str = "INSUFFICIENT_DATA"
    candidate_status: str = "INSUFFICIENT_DATA"
    checked: bool = False
    protected: bool = False
    exclusion_reason: str | None = None
    resolution_trace: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
