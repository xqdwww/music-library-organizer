from __future__ import annotations

import hashlib
import math
import os
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import AlbumReview
from .normalize import canonical_identity, exact_identity
from .personal_signals import NeteaseCommunitySignal, PersonalSignal

KEEP = "KEEP"
REVIEW = "REVIEW"
LOW_PERSONAL_VALUE = "LOW_PERSONAL_VALUE"
DUPLICATE_VALUE = "DUPLICATE_VALUE"
PROTECTED_COLLECTION = "PROTECTED_COLLECTION"

LOSSLESS_FORMATS = {"alac", "ape", "dff", "dsf", "flac", "wav", "aif", "aiff"}
EDITION_MARKER = re.compile(
    r"\b(deluxe|remaster(?:ed)?|anniversary|expanded|collector'?s|mono|stereo|box set|complete|edition)\b",
    re.IGNORECASE,
)
MULTIPART_MARKER = re.compile(
    r"(?:\b(?:disc|disk|cd|part|vol(?:ume)?)[ ._:-]*\d+\b|\bbonus\b|\be\d{2,3}\b|^\d+\s*[-–]\s*\d+$)",
    re.IGNORECASE,
)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC)


def _years_ago(value: str | None, now: datetime) -> float | None:
    parsed = _parse_date(value)
    if parsed is None:
        return None
    return max(0.0, (now - parsed.astimezone(UTC)).days / 365.25)


def personal_usage_score(signal: PersonalSignal, now: datetime | None = None) -> tuple[float, list[str]]:
    if not signal.observed:
        return 50.0, ["personal usage data unavailable; neutral score used"]
    now = now or datetime.now(UTC)
    play_count = signal.play_count or 0
    if play_count >= 100:
        play_points = 50
    elif play_count >= 50:
        play_points = 45
    elif play_count >= 20:
        play_points = 38
    elif play_count >= 5:
        play_points = 28
    elif play_count >= 1:
        play_points = 15
    else:
        play_points = 0
    age = _years_ago(signal.last_played_at, now)
    if age is None:
        recency_points = 0
    elif age <= 0.25:
        recency_points = 25
    elif age <= 1:
        recency_points = 20
    elif age <= 3:
        recency_points = 10
    elif age <= 5:
        recency_points = 5
    else:
        recency_points = 0
    rating_points = 15 * (signal.rating or 0) / 100
    favorite_points = 10 if signal.favorite else 0
    playlist_points = min(10, signal.playlist_count * 3)
    score = min(100.0, play_points + recency_points + rating_points + favorite_points + playlist_points)
    reasons = [f"play_count={play_count}"]
    reasons.append(f"last_played_years={age:.2f}" if age is not None else "last_played=never_or_unknown")
    if signal.rating is not None:
        reasons.append(f"personal_rating={signal.rating:g}")
    if signal.favorite:
        reasons.append("favorite=true")
    if signal.playlist_count:
        reasons.append(f"playlist_count={signal.playlist_count}")
    return round(score, 2), reasons


def collector_value(review: AlbumReview) -> tuple[float, list[str], bool]:
    reasons: list[str] = []
    protected = bool(review.protected or review.candidate_status in {"PROTECTED", "PROFESSIONAL_PROTECTED"})
    score = 50.0
    if protected:
        reasons.append("existing permanent protection")
        score = 100.0
    for reason in review.protection_reasons:
        if reason not in reasons:
            reasons.append(reason)
        score = max(score, 90.0)
        protected = True
    for evidence in review.professional_evidence:
        label = evidence.award or evidence.recommendation or evidence.reference_recording_status
        if label:
            reasons.append(f"{evidence.publication}: {label}")
            score = max(score, 90.0)
            protected = True
        elif evidence.match_confidence >= 0.85:
            reasons.append(f"matched professional review: {evidence.publication}")
            score = max(score, 75.0)
    if review.local.historical_or_catalog_significance:
        reasons.extend(review.local.historical_or_catalog_significance)
        score = max(score, 85.0)
        protected = True
    if review.local.category == "Classical":
        score += 10
        identity = review.local.classical_identity
        if identity.get("conductor") or identity.get("orchestra") or identity.get("recording_year"):
            reasons.append("identified classical recording/performer edition")
            score += 5
    if review.local.category == "Jazz":
        score += 10
        identity = review.local.jazz_identity
        if identity.get("leader") or identity.get("recording_date") or identity.get("label"):
            reasons.append("identified jazz session/release context")
            score += 5
    if review.local.catalog_number or review.local.barcode:
        reasons.append("catalog or barcode identity retained")
        score += 5
    return min(100.0, score), list(dict.fromkeys(reasons)), protected


def public_quality_score(
    review: AlbumReview,
    netease: NeteaseCommunitySignal,
) -> tuple[float, list[dict[str, Any]]]:
    values: list[tuple[float, float]] = []
    evidence: list[dict[str, Any]] = []
    if review.music_score is not None:
        values.append((review.music_score, 0.8))
        evidence.append({"source": "existing_music_score", "score": review.music_score})
    if netease.accepted and netease.score is not None:
        values.append((netease.score, 0.2))
        evidence.append({
            "source": "netease_community",
            "score": netease.score,
            "comment_count": netease.comment_count,
            "song_match_rate": netease.song_match_rate,
        })
    if not values:
        return 50.0, evidence
    total_weight = sum(weight for _, weight in values)
    return round(sum(score * weight for score, weight in values) / total_weight, 2), evidence


def _duplicate_groups(reviews: list[AlbumReview]) -> dict[str, dict[str, Any]]:
    parent = {review.local.album_id: review.local.album_id for review in reviews}

    def find(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    reason_by_pair: dict[tuple[str, str], set[str]] = defaultdict(set)
    strong_keys: dict[tuple[str, str], list[AlbumReview]] = defaultdict(list)
    fallback_keys: dict[tuple[str, str], list[AlbumReview]] = defaultdict(list)
    for review in reviews:
        canonical = review.canonical
        if canonical and canonical.release_group_id:
            strong_keys[("musicbrainz_release_group", canonical.release_group_id)].append(review)
        if canonical and canonical.master_id:
            strong_keys[("discogs_master", canonical.master_id)].append(review)
        fallback_keys[canonical_identity(review.local.artist, review.local.album)].append(review)

    for key, rows in strong_keys.items():
        if len(rows) < 2:
            continue
        for row in rows[1:]:
            union(rows[0].local.album_id, row.local.album_id)
            reason_by_pair[tuple(sorted((rows[0].local.album_id, row.local.album_id)))].add(key[0].upper())

    for rows in fallback_keys.values():
        if len(rows) < 2:
            continue
        categories = {row.local.category for row in rows}
        edition_evidence = any(
            EDITION_MARKER.search(f"{row.local.album} {row.local.edition or ''} {row.local.relative_path}")
            for row in rows
        )
        exact_titles = len({exact_identity(row.local.artist, row.local.album) for row in rows}) == 1
        if categories & {"Classical", "Jazz"} and not edition_evidence:
            continue
        if not edition_evidence and not exact_titles:
            continue
        for row in rows[1:]:
            union(rows[0].local.album_id, row.local.album_id)
            reason_by_pair[tuple(sorted((rows[0].local.album_id, row.local.album_id)))].add(
                "NORMALIZED_EDITION_IDENTITY"
            )

    grouped: dict[str, list[AlbumReview]] = defaultdict(list)
    for review in reviews:
        grouped[find(review.local.album_id)].append(review)
    result: dict[str, dict[str, Any]] = {}
    for rows in grouped.values():
        if len(rows) < 2:
            continue
        if _multipart_collection(rows):
            continue
        ids = sorted(row.local.album_id for row in rows)
        group_id = "dup_" + hashlib.sha256("\0".join(ids).encode()).hexdigest()[:16]
        reasons: set[str] = set()
        for index, left in enumerate(ids):
            for right in ids[index + 1 :]:
                reasons.update(reason_by_pair.get((left, right), set()))
        for row in rows:
            result[row.local.album_id] = {
                "duplicate_group_id": group_id,
                "duplicate_reason": sorted(reasons) or ["CANONICAL_RELEASE_IDENTITY"],
                "group_size": len(rows),
            }
    return result


def _multipart_collection(rows: list[AlbumReview]) -> bool:
    paths = [Path(row.local.relative_path) for row in rows]
    if any(left in right.parents for left in paths for right in paths if left != right):
        return True
    common = Path(os.path.commonpath([str(path) for path in paths]))
    if len(common.parts) >= 2:
        return True
    markers = [
        bool(MULTIPART_MARKER.search(f"{row.local.album} {Path(row.local.relative_path).name}"))
        for row in rows
    ]
    return all(markers) or (len(rows) >= 4 and sum(markers) / len(rows) >= 0.75)


def _preferred_versions(
    reviews: list[AlbumReview],
    duplicates: dict[str, dict[str, Any]],
    personal: dict[str, PersonalSignal],
) -> set[str]:
    groups: dict[str, list[AlbumReview]] = defaultdict(list)
    for review in reviews:
        value = duplicates.get(review.local.album_id)
        if value:
            groups[value["duplicate_group_id"]].append(review)
    preferred: set[str] = set()
    for rows in groups.values():
        def key(review: AlbumReview) -> tuple[Any, ...]:
            signal_score, _ = personal_usage_score(personal.get(review.local.album_id, PersonalSignal()))
            protected = bool(review.protected or review.protection_reasons or review.professional_evidence)
            lossless = bool(set(review.local.formats) & LOSSLESS_FORMATS)
            return (
                protected,
                signal_score,
                len(review.professional_evidence),
                lossless,
                review.local.track_count,
                review.local.size_bytes,
                review.local.album_id,
            )

        preferred.add(max(rows, key=key).local.album_id)
    return preferred


def _low_personal_value(
    signal: PersonalSignal,
    collector_protected: bool,
    preferred: bool,
    score: float,
    now: datetime,
) -> bool:
    if not signal.observed or signal.match_confidence < 0.6 or collector_protected or preferred:
        return False
    last_played_age = _years_ago(signal.last_played_at, now)
    added_age = _years_ago(signal.library_added_date, now)
    return bool(
        (signal.play_count or 0) == 0
        and (last_played_age is None or last_played_age > 5)
        and signal.rating is None
        and not signal.favorite
        and signal.playlist_count == 0
        and (added_age is None or added_age > 5)
        and score <= 45
    )


def build_curator_report(
    reviews: list[AlbumReview],
    personal: dict[str, PersonalSignal] | None = None,
    netease: dict[str, NeteaseCommunitySignal] | None = None,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    personal = personal or {}
    netease = netease or {}
    generated_at = generated_at or datetime.now(UTC)
    duplicates = _duplicate_groups(reviews)
    preferred = _preferred_versions(reviews, duplicates, personal)
    rows: list[dict[str, Any]] = []
    for review in reviews:
        album_id = review.local.album_id
        personal_signal = personal.get(album_id, PersonalSignal(local_track_count=review.local.track_count))
        netease_signal = netease.get(album_id, NeteaseCommunitySignal(local_track_count=review.local.track_count))
        usage_score, usage_reasons = personal_usage_score(personal_signal, generated_at)
        quality_score, quality_evidence = public_quality_score(review, netease_signal)
        collector_score, collector_reasons, collector_protected = collector_value(review)
        duplicate = duplicates.get(album_id)
        is_preferred = album_id in preferred
        redundancy_score = 100.0 if duplicate and not is_preferred else 0.0
        value_score = round(
            max(0.0, min(100.0, usage_score * 0.55 + quality_score * 0.20 + collector_score * 0.25
                         - redundancy_score * 0.15)),
            2,
        )
        reasons: list[str] = []
        if collector_protected:
            recommendation = PROTECTED_COLLECTION
            reasons.append("collector or professional protection")
        elif duplicate and not is_preferred:
            recommendation = DUPLICATE_VALUE
            reasons.append("non-preferred release in a duplicate/edition group")
        elif _low_personal_value(personal_signal, collector_protected, is_preferred, value_score, generated_at):
            recommendation = LOW_PERSONAL_VALUE
            reasons.append("observed zero-use album older than five years with no personal or collector signal")
        elif personal_signal.observed and usage_score >= 65:
            recommendation = KEEP
            reasons.append("strong observed personal usage")
        elif personal_signal.observed:
            recommendation = REVIEW
            reasons.append("observed personal usage is not strong enough for automatic retention")
        elif review.music_score is not None and review.music_score <= 70 and not collector_protected:
            recommendation = REVIEW
            reasons.append("personal data unavailable; existing low public score is review-only evidence")
        else:
            recommendation = KEEP
            reasons.append("no current evidence justifies review")
        rows.append({
            "album_id": album_id,
            "album": review.local.album,
            "artist": review.local.artist,
            "year": review.local.year,
            "path": review.local.path,
            "relative_path": review.local.relative_path,
            "size_bytes": review.local.size_bytes,
            "track_count": review.local.track_count,
            "safe_directory": review.local.safe_directory,
            "formats": review.local.formats,
            "category": review.local.category,
            "album_type": review.local.album_type,
            "language_bucket": review.local.language_bucket,
            "personal_value_score": value_score,
            "personal_usage_score": usage_score,
            "personal_signal": personal_signal.to_dict(),
            "personal_signal_reasons": usage_reasons,
            "public_music_quality_score": quality_score,
            "public_music_quality_evidence": quality_evidence,
            "music_score": review.music_score,
            "professional_evidence": [
                {
                    "publication": item.publication,
                    "award": item.award,
                    "recommendation": item.recommendation,
                    "reference_recording_status": item.reference_recording_status,
                    "source_url": item.source_url,
                }
                for item in review.professional_evidence
            ],
            "collector_value_score": collector_score,
            "collector_protection_reason": collector_reasons,
            "collector_protected": collector_protected,
            "netease_community_signal": netease_signal.to_dict(),
            "redundancy_score": redundancy_score,
            "duplicate_group_id": duplicate["duplicate_group_id"] if duplicate else None,
            "duplicate_reason": duplicate["duplicate_reason"] if duplicate else [],
            "preferred_release_candidate": is_preferred,
            "recommendation": recommendation,
            "recommendation_reason": reasons,
            "checked": False,
        })
    storage_review_pool = [
        row for row in rows
        if row["recommendation"] == KEEP
        and not row["personal_signal"]["observed"]
        and not row["collector_protected"]
        and row["duplicate_group_id"] is None
        and row["safe_directory"]
    ]
    storage_review_count = math.ceil(len(storage_review_pool) * 0.10)
    storage_review_ids = {
        row["album_id"]
        for row in sorted(storage_review_pool, key=lambda item: (-item["size_bytes"], item["album_id"]))[
            :storage_review_count
        ]
    }
    for row in rows:
        if row["album_id"] not in storage_review_ids:
            continue
        row["recommendation"] = REVIEW
        row["recommendation_reason"] = [
            "personal data unavailable; top-decile storage footprint without collector protection"
        ]
    counts = {
        name: sum(row["recommendation"] == name for row in rows)
        for name in (KEEP, REVIEW, LOW_PERSONAL_VALUE, DUPLICATE_VALUE, PROTECTED_COLLECTION)
    }
    duplicate_groups = len({row["duplicate_group_id"] for row in rows if row["duplicate_group_id"]})
    candidate_names = {REVIEW, LOW_PERSONAL_VALUE, DUPLICATE_VALUE}
    candidate_rows = [row for row in rows if row["recommendation"] in candidate_names]
    return {
        "status": "PERSONAL_LIBRARY_CURATOR_ANALYSIS_READY",
        "generated_at": generated_at.isoformat(),
        "model": {
            "version": 1,
            "personal_usage_weight": 0.55,
            "public_quality_weight": 0.20,
            "collector_value_weight": 0.25,
            "non_preferred_duplicate_penalty": 15,
            "unobserved_storage_review_fraction": 0.10,
            "storage_is_review_priority_not_value_score": True,
            "missing_personal_signal": "NEUTRAL_NOT_ZERO",
            "automatic_selection": False,
            "automatic_deletion": False,
        },
        "summary": {
            "albums": len(rows),
            "audio_files": sum(review.local.track_count for review in reviews),
            "total_bytes": sum(review.local.size_bytes for review in reviews),
            "keep_count": counts[KEEP],
            "review_count": counts[REVIEW],
            "low_personal_value_count": counts[LOW_PERSONAL_VALUE],
            "duplicate_value_count": counts[DUPLICATE_VALUE],
            "duplicate_groups": duplicate_groups,
            "protected_count": counts[PROTECTED_COLLECTION],
            "review_candidate_count": len(candidate_rows),
            "review_candidate_bytes": sum(row["size_bytes"] for row in candidate_rows),
            "default_checked_count": 0,
        },
        "albums": sorted(
            rows,
            key=lambda row: (
                row["recommendation"] not in candidate_names,
                row["personal_value_score"],
                -row["size_bytes"],
                row["artist"].casefold(),
            ),
        ),
    }
