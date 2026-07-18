from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .models import ALLOWED_MATCHES, EXCLUDED_SECONDARY_TYPES, AlbumReview


@dataclass(frozen=True, slots=True)
class PersonalPruningPolicy:
    calibration_batch_id: str
    strong_candidate_threshold: float = 65
    review_candidate_threshold: float = 70
    minimum_independent_sources: int = 2
    ambiguous_match_excluded: bool = True
    source_conflict_excluded_from_strong: bool = True
    insufficient_data_excluded_from_score_candidates: bool = True
    professional_protected_excluded: bool = True
    automatic_selection: bool = False
    automatic_deletion: bool = False
    enabled: bool = True
    version: int = 2

    def __post_init__(self) -> None:
        if not self.calibration_batch_id:
            raise ValueError("calibration_batch_id is required")
        if self.strong_candidate_threshold >= self.review_candidate_threshold:
            raise ValueError("strong threshold must be below review threshold")
        if self.minimum_independent_sources < 1:
            raise ValueError("minimum_independent_sources must be positive")
        if self.automatic_selection or self.automatic_deletion:
            raise ValueError("personal policy may not enable automatic selection or deletion")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> PersonalPruningPolicy:
        known = {name: value[name] for name in cls.__dataclass_fields__ if name in value}
        return cls(**known)

    def to_yaml(self, reviewed_count: int) -> str:
        def boolean(value: bool) -> str:
            return str(value).lower()

        return f'''version: {self.version}
enabled: {boolean(self.enabled)}

candidate_policy:
  strong_candidate_threshold: {self.strong_candidate_threshold:g}
  review_candidate_threshold: {self.review_candidate_threshold:g}
  minimum_independent_sources: {self.minimum_independent_sources}
  ambiguous_match_excluded: {boolean(self.ambiguous_match_excluded)}
  source_conflict_excluded_from_strong: {boolean(self.source_conflict_excluded_from_strong)}
  insufficient_data_excluded_from_score_candidates: {boolean(self.insufficient_data_excluded_from_score_candidates)}
  professional_protected_excluded: {boolean(self.professional_protected_excluded)}
  automatic_selection: {boolean(self.automatic_selection)}
  automatic_deletion: {boolean(self.automatic_deletion)}

calibration:
  batch_id: "{self.calibration_batch_id}"
  reviewed_count: {reviewed_count}
  selected_strong_threshold: {self.strong_candidate_threshold:g}
  selected_review_threshold: {self.review_candidate_threshold:g}
'''


def independent_source_count(review: AlbumReview) -> int:
    community_or_critic = {item.source.casefold() for item in review.evidence}
    professional = {item.publication.casefold() for item in review.professional_evidence}
    return len(community_or_critic) + len(professional)


def _professionally_protected(review: AlbumReview) -> bool:
    return bool(
        review.protected
        or review.candidate_status in {"PROTECTED", "PROFESSIONAL_PROTECTED"}
        or (review.protection_reasons and (review.professional_score or 0) >= 80)
    )


def _controlled_album(review: AlbumReview) -> bool:
    canonical = review.canonical
    if canonical is None or canonical.match_status not in ALLOWED_MATCHES:
        return False
    if not review.local.safe_directory or review.local.duplicate_local_versions > 1:
        return False
    secondary = {value.casefold() for value in canonical.secondary_types}
    return (canonical.primary_type or "").casefold() == "album" and not secondary & EXCLUDED_SECONDARY_TYPES


def build_personal_candidate_report(
    reviews: list[AlbumReview],
    feedback: dict[str, dict[str, Any]],
    policy: PersonalPruningPolicy,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    strong_ids: set[str] = set()
    review_ids: set[str] = set()
    explicit_ids: set[str] = set()
    later_ids: set[str] = set()
    protected_ids: set[str] = set()
    ambiguous_ids: set[str] = set()
    insufficient_ids: set[str] = set()

    for review in reviews:
        album_id = review.local.album_id
        decision = feedback.get(album_id, {}).get("user_decision", "UNREVIEWED")
        protected = _professionally_protected(review)
        match_status = review.canonical.match_status if review.canonical else "NOT_FOUND"
        insufficient = review.music_score is None or review.rating_status == "INSUFFICIENT_DATA"
        if protected:
            protected_ids.add(album_id)
        if match_status == "AMBIGUOUS":
            ambiguous_ids.add(album_id)
        if insufficient:
            insufficient_ids.add(album_id)

        groups: list[str] = []
        if decision == "DELETE_CANDIDATE" and not (
            policy.professional_protected_excluded and protected
        ):
            groups.append("USER_SELECTED_CANDIDATE")
            explicit_ids.add(album_id)
        elif decision == "LATER":
            groups.append("LATER")
            later_ids.add(album_id)

        machine_allowed = decision not in {"KEEP", "LATER"}
        machine_allowed = machine_allowed and _controlled_album(review)
        machine_allowed = machine_allowed and independent_source_count(review) >= policy.minimum_independent_sources
        machine_allowed = machine_allowed and not (policy.professional_protected_excluded and protected)
        machine_allowed = machine_allowed and not (
            policy.ambiguous_match_excluded and match_status == "AMBIGUOUS"
        )
        machine_allowed = machine_allowed and not (
            policy.insufficient_data_excluded_from_score_candidates and insufficient
        )
        score = review.music_score
        if machine_allowed and score is not None and score <= policy.strong_candidate_threshold:
            if not (
                policy.source_conflict_excluded_from_strong
                and review.rating_status == "SOURCE_CONFLICT"
            ):
                groups.append("STRONG_PERSONAL_CANDIDATE")
                strong_ids.add(album_id)
        elif (
            machine_allowed
            and score is not None
            and policy.strong_candidate_threshold < score <= policy.review_candidate_threshold
        ):
            groups.append("PERSONAL_REVIEW_CANDIDATE")
            review_ids.add(album_id)

        if not groups:
            continue
        primary_group = (
            "USER_SELECTED_CANDIDATE"
            if "USER_SELECTED_CANDIDATE" in groups
            else "LATER"
            if "LATER" in groups
            else groups[0]
        )
        value = review.to_dict()
        value.update({
            "base_candidate_status": review.candidate_status,
            "candidate_status": primary_group,
            "candidate_groups": groups,
            "user_decision": decision,
            "independent_source_count": independent_source_count(review),
            "eligible_for_selection": bool(groups and "LATER" not in groups and not protected),
            "checked": False,
        })
        rows.append(value)

    unique_candidate_ids = strong_ids | review_ids | explicit_ids
    by_id = {review.local.album_id: review for review in reviews}
    return {
        "status": "PERSONAL_CANDIDATES_READY",
        "policy": policy.to_dict(),
        "summary": {
            "strong_low_score": len(strong_ids),
            "review_65_to_70": len(review_ids),
            "explicit_user_candidates": len(explicit_ids),
            "later": len(later_ids),
            "protected_excluded": len(protected_ids),
            "ambiguous_excluded": len(ambiguous_ids),
            "insufficient_data_excluded": len(insufficient_ids),
            "total_unique_candidates": len(unique_candidate_ids),
            "estimated_reclaim_bytes": sum(by_id[album_id].local.size_bytes for album_id in unique_candidate_ids),
            "default_checked_count": 0,
        },
        "candidates": sorted(
            rows,
            key=lambda row: (
                row["candidate_status"] == "LATER",
                row["music_score"] is None,
                row["music_score"] if row["music_score"] is not None else 101,
                row["local"]["artist"].casefold(),
            ),
        ),
    }
