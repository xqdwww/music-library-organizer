from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .models import ALLOWED_MATCHES, EXCLUDED_SECONDARY_TYPES, AlbumReview, RatingEvidence
from .professional import professional_summary


@dataclass(slots=True)
class ScoringConfig:
    threshold: float = 60
    conflict_span: float = 20
    critic_share: float = 0.60
    community_share: float = 0.40
    maximum_source_share: float = 0.50
    community_group_weight: float = 0.25
    critic_group_weight: float = 0.30
    professional_group_weight: float = 0.45
    source_weights: dict[str, float] = field(
        default_factory=lambda: {
            "musicbrainz": 1.0,
            "discogs": 1.0,
        }
    )
    professional_source_weights: dict[str, float] = field(
        default_factory=lambda: {
            "golden indie music awards": 1.0,
        }
    )

    @classmethod
    def load(cls, path: Path | None) -> ScoringConfig:
        if path is None:
            return cls()
        value = json.loads(path.read_text(encoding="utf-8"))
        known = {name: value[name] for name in cls.__dataclass_fields__ if name in value}
        return cls(**known)


def evidence_factor(evidence: RatingEvidence) -> float:
    if evidence.critic_or_community == "critic":
        count = evidence.review_count or 0
        if count >= 4:
            return 1.0
        if count >= 2:
            return 0.70
        return 0.40
    count = evidence.rating_count or 0
    if count >= 100:
        return 1.0
    if count >= 30:
        return 0.80
    if count >= 10:
        return 0.55
    return 0.30


def _weights(evidence: list[RatingEvidence], config: ScoringConfig) -> list[float]:
    groups: dict[str, list[int]] = {"critic": [], "community": []}
    for index, item in enumerate(evidence):
        groups.setdefault(item.critic_or_community, []).append(index)
    both = bool(groups.get("critic")) and bool(groups.get("community"))
    shares = {
        "critic": config.critic_share if both else (1.0 if groups.get("critic") else 0.0),
        "community": config.community_share if both else (1.0 if groups.get("community") else 0.0),
    }
    result = [0.0] * len(evidence)
    for group, indexes in groups.items():
        if not indexes:
            continue
        raw = [
            config.source_weights.get(evidence[index].source, 1.0) * evidence_factor(evidence[index])
            for index in indexes
        ]
        total = sum(raw)
        for index, value in zip(indexes, raw, strict=True):
            result[index] = shares.get(group, 0.0) * value / total
    if len(evidence) > 1:
        for _ in range(len(evidence)):
            over = [index for index, value in enumerate(result) if value > config.maximum_source_share]
            if not over:
                break
            excess = sum(result[index] - config.maximum_source_share for index in over)
            for index in over:
                result[index] = config.maximum_source_share
            under = [index for index, value in enumerate(result) if value < config.maximum_source_share]
            capacity = sum(config.maximum_source_share - result[index] for index in under)
            if not under or capacity <= 0:
                break
            for index in under:
                result[index] += excess * (config.maximum_source_share - result[index]) / capacity
    total = sum(result)
    return [value / total for value in result] if total else result


def aggregate_music_score(evidence: list[RatingEvidence], config: ScoringConfig) -> tuple[float | None, str]:
    valid = [item for item in evidence if 0 <= item.normalized_score_100 <= 100]
    if not valid:
        return None, "INSUFFICIENT_DATA"
    if (
        max(item.normalized_score_100 for item in valid) - min(item.normalized_score_100 for item in valid)
        > config.conflict_span
    ):
        return round(sum(item.normalized_score_100 for item in valid) / len(valid), 2), "SOURCE_CONFLICT"
    weights = _weights(valid, config)
    score = round(sum(item.normalized_score_100 * weight for item, weight in zip(valid, weights, strict=True)), 2)
    return score, "RATED" if len(valid) >= 2 else "SINGLE_SOURCE"


def _group_score(evidence: list[RatingEvidence], group: str, config: ScoringConfig) -> float | None:
    rows = [item for item in evidence if item.critic_or_community == group]
    return aggregate_music_score(rows, config)[0]


def aggregate_review_scores(review: AlbumReview, config: ScoringConfig) -> tuple[float | None, str]:
    review.community_score = _group_score(review.evidence, "community", config)
    review.critic_score = _group_score(review.evidence, "critic", config)
    summary = professional_summary(
        review.professional_evidence,
        config.professional_source_weights,
    )
    for key, value in summary.items():
        setattr(review, key, value)
    groups = {
        "community": review.community_score,
        "critic": review.critic_score,
        "professional": review.professional_score,
    }
    available = {key: value for key, value in groups.items() if value is not None}
    if not available:
        return None, "INSUFFICIENT_DATA"
    if len(available) == 1 and "professional" not in available:
        return aggregate_music_score(review.evidence, config)
    weights = {
        "community": config.community_group_weight,
        "critic": config.critic_group_weight,
        "professional": config.professional_group_weight,
    }
    total_weight = sum(weights[key] for key in available)
    if total_weight <= 0:
        return None, "INSUFFICIENT_DATA"
    score = round(sum(float(value) * weights[key] for key, value in available.items()) / total_weight, 2)
    if max(available.values()) - min(available.values()) > config.conflict_span:
        return score, "SOURCE_CONFLICT"
    independent_sources = len({item.source for item in review.evidence}) + review.professional_source_count
    return score, "RATED" if independent_sources >= 2 else "SINGLE_SOURCE"


def classify(review: AlbumReview, config: ScoringConfig) -> AlbumReview:
    canonical = review.canonical
    score, rating_status = aggregate_review_scores(review, config)
    review.music_score = score
    review.rating_status = rating_status
    review.checked = False
    if review.protected:
        review.candidate_status = "PROTECTED"
        return review
    if review.protection_reasons and (review.professional_score or 0) >= 80:
        review.candidate_status = "PROFESSIONAL_PROTECTED"
        review.exclusion_reason = ", ".join(review.protection_reasons)
        return review
    if not review.local.safe_directory:
        review.candidate_status = "MATCH_REVIEW"
        review.exclusion_reason = "; ".join(review.local.scan_warnings) or "unsafe album directory"
        return review
    if review.local.duplicate_local_versions > 1:
        review.candidate_status = "MATCH_REVIEW"
        review.exclusion_reason = "multiple local versions"
        return review
    if canonical is None or canonical.match_status not in ALLOWED_MATCHES:
        review.candidate_status = "MATCH_REVIEW"
        return review
    secondary = {value.casefold() for value in canonical.secondary_types}
    if (canonical.primary_type or "").casefold() != "album" or secondary & EXCLUDED_SECONDARY_TYPES:
        review.candidate_status = "EXCLUDED_TYPE"
        review.exclusion_reason = ", ".join(canonical.secondary_types) or canonical.primary_type or "unknown type"
        return review
    independent_sources = len({item.source for item in review.evidence}) + review.professional_source_count
    if rating_status == "SOURCE_CONFLICT":
        review.candidate_status = "SOURCE_CONFLICT"
    elif score is None:
        review.candidate_status = "INSUFFICIENT_DATA"
    elif independent_sources >= 2 and score <= config.threshold:
        review.candidate_status = "STRONG_LOW_RATED"
    elif independent_sources == 1:
        if review.professional_source_count:
            review.candidate_status = "SINGLE_SOURCE_REVIEW" if score <= config.threshold else "NOT_LOW_RATED"
        elif (
            review.evidence[0].critic_or_community == "critic"
            and (review.evidence[0].review_count or 0) >= 4
            and score <= 50
        ):
            review.candidate_status = "SINGLE_SOURCE_REVIEW"
        elif score <= config.threshold:
            review.candidate_status = "SINGLE_SOURCE_REVIEW"
        else:
            review.candidate_status = "INSUFFICIENT_DATA"
    elif score <= config.threshold:
        review.candidate_status = "LOW_RATED_REVIEW"
    else:
        review.candidate_status = "NOT_LOW_RATED"
    return review
