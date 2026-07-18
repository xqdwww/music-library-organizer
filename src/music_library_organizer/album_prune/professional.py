from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from typing import Any

from .models import ALLOWED_MATCHES, CanonicalAlbum, LocalAlbum, ProfessionalEvidence
from .normalize import canonical_identity, normalized_text
from .ratings import HttpCache, utc_now

GOLDEN_INDIE_URL = (
    "https://file.moc.gov.tw/001/Upload/510/relfile/16168/246583/"
    "c73a5ba3-9dca-4398-a6d2-029f7d97b199.csv"
)


@dataclass(frozen=True, slots=True)
class AwardRecord:
    source: str
    year: int
    category: str
    artist: str
    album: str
    source_url: str
    source_entity_id: str
    evidence_type: str = "AWARD_WINNER"
    protection_reason: str = "AWARD_WINNER"


def parse_golden_indie_awards(csv_text: str) -> list[AwardRecord]:
    result: list[AwardRecord] = []
    for row in csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff"))):
        category = str(row.get("組別") or "").strip()
        work = str(row.get("得獎作品") or "")
        if not _golden_album_evidence(category, work):
            continue
        roc_year = str(row.get("年度") or "").strip()
        if not roc_year.isdigit():
            continue
        artist = str(row.get("得獎者") or "").strip()
        album = _extract_chinese_album(work)
        if not artist or not album or artist == "從缺" or album == "從缺":
            continue
        year = int(roc_year) + 1911
        entity_id = f"golden-indie:{roc_year}:{normalized_text(category)}:{normalized_text(album)}"
        result.append(
            AwardRecord(
                "Golden Indie Music Awards",
                year,
                category,
                artist,
                album,
                "https://data.gov.tw/en/datasets/58040",
                entity_id,
            )
        )
    return result


def _extract_chinese_album(value: str) -> str:
    match = re.search(r"《([^》]+)》", value)
    return (match.group(1) if match else value).strip(" 《》\t")


def _golden_album_evidence(category: str, work: str) -> bool:
    if not re.search(r"《[^》]+》", work):
        return False
    excluded = ("歌曲", "單曲", "樂手", "現場", "貢獻")
    return not any(marker in category for marker in excluded)


def _identity_names(local: LocalAlbum) -> list[str]:
    values = [
        value
        for value in (
            local.artist,
            *local.artist_aliases,
            local.artist_sort_name,
            local.conductor,
            local.orchestra,
            *local.soloists,
            local.leader,
        )
        if value
    ]
    expanded = list(values)
    for value in values:
        expanded.extend(part.strip() for part in re.split(r"\s*(?:&|,|;| feat\.? | and )\s*", value) if part.strip())
    return list(dict.fromkeys(expanded))


def _artist_overlap(local: LocalAlbum, remote: str) -> bool:
    normalized_remote = normalized_text(remote)
    return any(
        len(name := normalized_text(value)) >= 3 and (name in normalized_remote or normalized_remote in name)
        for value in _identity_names(local)
    )


def _record_matches(
    local: LocalAlbum,
    canonical: CanonicalAlbum | None,
    record: AwardRecord,
) -> tuple[bool, list[str], float]:
    local_title = canonical_identity(local.artist, local.album)[1]
    remote_title = canonical_identity(record.artist, record.album)[1]
    if local_title != remote_title:
        return False, [], 0.0
    features = ["canonical album title"]
    if not _artist_overlap(local, record.artist):
        return False, features, 0.0
    features.append("artist/performer identity")
    controlled_canonical = bool(canonical and canonical.match_status in ALLOWED_MATCHES)
    release_year = (
        canonical.year if controlled_canonical else None
    ) or local.original_release_year or local.year
    if not release_year:
        return False, features, 0.0
    if abs(release_year - record.year) > 3:
        return False, features, 0.0
    features.append("release/award year window")
    return True, features, 0.98


class OfficialAwardsSource:
    adapter_version = "official-awards-v2"

    def __init__(self, cache: HttpCache, user_agent: str):
        self.cache = cache
        self.headers = {"Accept": "text/csv,*/*;q=0.8", "User-Agent": user_agent}
        self.records: list[AwardRecord] = []
        self.fetch_errors: list[dict[str, str]] = []
        self.fetched_at = ""
        self.stale = False

    def load(self) -> None:
        self.records = []
        self.fetch_errors = []
        try:
            response = self.cache.fetch_text(
                "golden-indie",
                GOLDEN_INDIE_URL,
                self.adapter_version,
                self.headers,
                min_interval=1.0,
            )
            self.records.extend(parse_golden_indie_awards(response.text))
            self.fetched_at = response.fetched_at
            self.stale = response.stale
        except Exception as exc:
            self.fetch_errors.append({"source": "golden-indie", "error": str(exc)})

    def evidence_for(
        self,
        local: LocalAlbum,
        canonical: CanonicalAlbum | None,
    ) -> list[ProfessionalEvidence]:
        result: list[ProfessionalEvidence] = []
        for record in self.records:
            matched, features, confidence = _record_matches(local, canonical, record)
            if not matched:
                continue
            raw = "\0".join(
                (record.source, str(record.year), record.category, record.artist, record.album, record.source_url)
            )
            result.append(
                ProfessionalEvidence(
                    publication=record.source,
                    publication_type="official_music_award",
                    source_title=f"{record.year} {record.category}: {record.album}",
                    source_url=record.source_url,
                    source_entity_id=record.source_entity_id,
                    evidence_type=record.evidence_type,
                    issue=str(record.year),
                    award=record.category,
                    recommendation=(
                        "official historic recording induction"
                        if record.evidence_type == "HISTORIC_RECORDING"
                        else "official award winner"
                    ),
                    review_date=str(record.year),
                    recording_identity=(
                        f"musicbrainz:release-group:{canonical.release_group_id}"
                        if canonical and canonical.release_group_id
                        else f"local:{local.album_id}"
                    ),
                    match_features=features,
                    match_confidence=confidence,
                    fetched_at=self.fetched_at or utc_now(),
                    adapter_version=self.adapter_version,
                    raw_evidence_hash=hashlib.sha256(raw.encode()).hexdigest(),
                    normalized_score_100=95.0,
                    conversion_rule="official album-category winner -> 95; award retained as raw evidence",
                    protection_reason=record.protection_reason,
                    stale=self.stale,
                )
            )
        return deduplicate_professional_evidence(result)


def deduplicate_professional_evidence(
    evidence: list[ProfessionalEvidence],
) -> list[ProfessionalEvidence]:
    unique: dict[tuple[str, str, str], ProfessionalEvidence] = {}
    for item in evidence:
        key = (item.publication.casefold(), item.source_entity_id, item.recording_identity)
        existing = unique.get(key)
        if existing is None or item.match_confidence > existing.match_confidence:
            unique[key] = item
    return list(unique.values())


def professional_summary(
    evidence: list[ProfessionalEvidence],
    source_weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    rows = deduplicate_professional_evidence(evidence)
    publications = {item.publication.casefold() for item in rows}
    publication_scores: dict[str, list[float]] = {}
    for item in rows:
        if item.normalized_score_100 is not None:
            publication_scores.setdefault(item.publication.casefold(), []).append(
                item.normalized_score_100
            )
    weighted_scores: list[tuple[float, float]] = []
    for publication, values in publication_scores.items():
        weight = max(0.0, (source_weights or {}).get(publication, 1.0))
        if weight:
            weighted_scores.append((sum(values) / len(values), weight))
    total_weight = sum(weight for _, weight in weighted_scores)
    score = (
        round(sum(value * weight for value, weight in weighted_scores) / total_weight, 2)
        if total_weight
        else None
    )
    mean_match_confidence = (
        sum(item.match_confidence for item in rows) / len(rows) if rows else 0.0
    )
    source_factor = min(1.0, 0.75 + 0.10 * max(0, len(publications) - 1))
    confidence = round(mean_match_confidence * source_factor, 3) if rows else 0.0
    return {
        "professional_score": score,
        "professional_confidence": confidence,
        "professional_source_count": len(publications),
        "professional_recommendation_count": sum(bool(item.recommendation) for item in rows),
        "professional_award_count": sum(bool(item.award) for item in rows),
        "protection_reasons": sorted({item.protection_reason for item in rows if item.protection_reason}),
    }
