from __future__ import annotations

import json
import os
import secrets
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from .calibration import (
    classify_rating_scope,
    create_calibration_batch,
    import_beets_scope_metadata,
    policy_template,
    threshold_report,
)
from .curator import build_curator_report
from .entity_resolution import language_route
from .models import ALLOWED_MATCHES, AlbumReview, LocalAlbum
from .normalize import canonical_identity
from .personal_policy import PersonalPruningPolicy, build_personal_candidate_report
from .personal_signals import match_apple_signals, match_netease_signals
from .professional import OfficialAwardsSource
from .quarantine import (
    apply_delete_plan,
    create_delete_plan,
    load_batch_plan,
    purge_batch,
    recover_interrupted_batch,
    rollback_batch,
)
from .ratings import DiscogsSource, HttpCache, MusicBrainzSource
from .scanner import scan_albums
from .scoring import ScoringConfig, classify
from .store import ReviewStore, now

DEFAULT_USER_AGENT = "music-library-organizer/0.2 (contact: https://github.com/xqdwww/music-library-organizer)"


class AlbumPruneService:
    def __init__(self, state_root: Path, config: ScoringConfig | None = None):
        self.state_root = state_root.expanduser().resolve()
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.store_path = self.state_root / "album-prune.sqlite3"
        self.config = config or ScoringConfig()

    def scan(
        self,
        library_root: Path,
        *,
        limit: int | None = None,
        album_paths: list[str] | None = None,
        offline: bool = False,
        refresh: bool = False,
        ratings: bool = True,
        professional: bool = False,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> dict[str, Any]:
        albums = scan_albums(library_root, limit=limit, album_paths=album_paths)
        previous_by_path: dict[str, AlbumReview] = {}
        if self.store_path.is_file():
            with ReviewStore(self.store_path) as store:
                previous_by_path = {review.local.path: review for review in store.list_reviews()}
        cache = HttpCache(self.state_root / "rating_cache", offline=offline, refresh=refresh)
        sources: list[Any] = []
        source_status = {
            "musicbrainz": "enabled" if ratings else "preserved_not_refetched",
            "discogs": (
                "enabled_authenticated"
                if ratings and os.environ.get("DISCOGS_TOKEN")
                else "enabled_unauthenticated"
                if ratings
                else "preserved_not_refetched"
            ),
            "lastfm": "disabled_not_a_rating_source",
            "metacritic": "disabled_no_stable_official_api",
            "album_of_the_year": "disabled_no_stable_official_api",
            "allmusic": "disabled_no_stable_official_api",
            "official_awards": "enabled" if professional else "disabled",
        }
        if ratings:
            sources.append(MusicBrainzSource(cache, user_agent))
            discogs = DiscogsSource(cache, user_agent)
            sources.append(discogs)
        errors: list[dict[str, str]] = []
        official_awards = OfficialAwardsSource(cache, user_agent)
        if professional:
            official_awards.load()
            for row in official_awards.fetch_errors:
                errors.append({"album_id": "", **row})
        reviews: list[AlbumReview] = []
        for local in albums:
            previous = previous_by_path.get(local.path)
            if previous is not None:
                self._merge_prior_local_metadata(local, previous.local)
            resolution_trace: list[dict[str, Any]] = (
                list(previous.resolution_trace) if previous is not None and not ratings else []
            )
            if ratings and sources and language_route(local)["bucket"] == "UNKNOWN_CJK":
                try:
                    trace = sources[0].resolve_artist_metadata(local)
                    if isinstance(trace, dict):
                        resolution_trace.append(trace)
                except Exception as exc:
                    errors.append({"album_id": local.album_id, "source": "musicbrainz-artist", "error": str(exc)})
            scope_probe = AlbumReview(local=local, canonical=None)
            scope, reason = classify_rating_scope(scope_probe)
            local.rating_scope = scope
            local.rating_scope_reason = reason
            canonical = previous.canonical if previous is not None and not ratings else None
            unresolved_canonical = None
            evidence = list(previous.evidence) if previous is not None and not ratings else []
            if not local.rating_scope.startswith("INCLUDE"):
                evidence = []
            eligible_sources = sources if local.rating_scope.startswith("INCLUDE") else []
            for source in eligible_sources:
                try:
                    matched = source.lookup_album(local)
                    if matched and matched.match_status in ALLOWED_MATCHES and canonical is None:
                        canonical = matched
                    if matched and matched.match_status in ALLOWED_MATCHES:
                        rating = source.fetch_rating(matched)
                        if rating:
                            evidence.append(rating)
                    elif source.source_name == "musicbrainz" and unresolved_canonical is None:
                        unresolved_canonical = matched
                    resolution_trace.extend(getattr(source, "last_trace", []))
                except Exception as exc:
                    errors.append({"album_id": local.album_id, "source": source.source_name, "error": str(exc)})
                    resolution_trace.extend(getattr(source, "last_trace", []))
            if professional:
                professional_evidence = official_awards.evidence_for(
                    local,
                    canonical or unresolved_canonical,
                )
                if not professional_evidence and official_awards.fetch_errors and previous is not None:
                    professional_evidence = list(previous.professional_evidence)
            else:
                professional_evidence = (
                    list(previous.professional_evidence) if previous is not None else []
                )
            if self._apply_professional_scope_metadata(local, professional_evidence):
                scope, reason = classify_rating_scope(AlbumReview(local=local, canonical=canonical))
                local.rating_scope = scope
                local.rating_scope_reason = reason
                if not scope.startswith("INCLUDE"):
                    evidence = []
            review = classify(
                AlbumReview(
                    local=local,
                    canonical=canonical or unresolved_canonical,
                    evidence=evidence,
                    professional_evidence=professional_evidence,
                    resolution_trace=resolution_trace,
                ),
                self.config,
            )
            reviews.append(review)
        with ReviewStore(self.store_path) as store:
            store.save_reviews(reviews)
        return {
            "status": "CANDIDATES_READY",
            "library_root": str(library_root.expanduser().resolve()),
            "albums_scanned": len(reviews),
            "exact_matches": sum(
                1 for review in reviews if review.canonical and review.canonical.match_status == "EXACT"
            ),
            "canonicalized_matches": sum(
                1 for review in reviews if review.canonical and review.canonical.match_status == "CANONICALIZED"
            ),
            "rating_coverage": sum(1 for review in reviews if review.music_score is not None),
            "strong_low_rated": sum(1 for review in reviews if review.candidate_status == "STRONG_LOW_RATED"),
            "default_checked_count": 0,
            "audio_files_scanned": sum(review.local.track_count for review in reviews),
            "total_library_bytes": sum(review.local.size_bytes for review in reviews),
            "matching": {
                status: sum(
                    1 for review in reviews
                    if (review.canonical.match_status if review.canonical else "NOT_FOUND") == status
                )
                for status in ("EXACT", "CANONICALIZED", "LIKELY_NEEDS_REVIEW", "AMBIGUOUS", "NOT_FOUND")
            },
            "rating_status": {
                "one_rating_source": sum(len(review.evidence) == 1 for review in reviews),
                "two_or_more_rating_sources": sum(len(review.evidence) >= 2 for review in reviews),
                "INSUFFICIENT_DATA": sum(review.rating_status == "INSUFFICIENT_DATA" for review in reviews),
                "SOURCE_CONFLICT": sum(review.rating_status == "SOURCE_CONFLICT" for review in reviews),
                "professional_evidence_count": sum(bool(review.professional_evidence) for review in reviews),
                "professional_score_count": sum(review.professional_score is not None for review in reviews),
                "professional_protected": sum(
                    review.candidate_status == "PROFESSIONAL_PROTECTED" for review in reviews
                ),
            },
            "album_types": self._album_type_counts(reviews),
            "candidate_thresholds": self._threshold_counts(reviews),
            "source_status": source_status,
            "error_count": len(errors),
            "errors": errors[:100],
            "errors_truncated": len(errors) > 100,
        }

    @staticmethod
    def _merge_prior_local_metadata(current: LocalAlbum, previous: LocalAlbum) -> None:
        """Retain enrichment that a filesystem rescan cannot reconstruct."""
        scalar_fields = (
            "release_group_mbid",
            "release_mbid",
            "barcode",
            "catalog_number",
            "discogs_id",
            "composer",
            "work",
            "conductor",
            "orchestra",
            "leader",
            "recording_date",
            "original_release_year",
            "label",
            "edition",
            "live_studio",
            "artist_sort_name",
        )
        list_fields = (
            "soloists",
            "session_personnel",
            "historical_or_catalog_significance",
            "languages",
            "release_countries",
            "scripts",
            "artist_aliases",
            "artist_countries",
            "artist_areas",
        )
        for field in scalar_fields:
            if not getattr(current, field) and getattr(previous, field):
                setattr(current, field, getattr(previous, field))
        for field in list_fields:
            merged = list(dict.fromkeys([*getattr(current, field), *getattr(previous, field)]))
            setattr(current, field, merged)
        if current.album_type == "Other" and previous.album_type != "Other":
            current.album_type = previous.album_type

    @staticmethod
    def _apply_professional_scope_metadata(local: LocalAlbum, evidence: list[Any]) -> bool:
        if not any(item.publication == "Golden Indie Music Awards" for item in evidence):
            return False
        if "TW" not in local.release_countries:
            local.release_countries.append("TW")
        return True

    @staticmethod
    def _album_type(review: AlbumReview) -> str:
        secondary = {value.casefold() for value in (review.canonical.secondary_types if review.canonical else [])}
        primary = (review.canonical.primary_type if review.canonical else None) or review.local.album_type
        if "soundtrack" in secondary or review.local.album_type == "Soundtrack":
            return "Soundtrack"
        if "compilation" in secondary or review.local.album_type == "Compilation":
            return "Compilation"
        if "live" in secondary or review.local.album_type == "Live":
            return "Live"
        if review.local.category in {"Classical", "Jazz"}:
            return review.local.category
        return primary if primary in {"Album", "EP", "Single"} else "Other"

    @classmethod
    def _album_type_counts(cls, reviews: list[AlbumReview]) -> dict[str, int]:
        names = ("Album", "EP", "Single", "Compilation", "Live", "Soundtrack", "Classical", "Jazz", "Other")
        return {name: sum(cls._album_type(review) == name for review in reviews) for name in names}

    @staticmethod
    def _threshold_counts(reviews: list[AlbumReview]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for threshold in (45, 50, 55, 60, 65, 70):
            rows = [review for review in reviews if review.music_score is not None and review.music_score <= threshold]
            result[str(threshold)] = {
                "albums": len(rows),
                "bytes": sum(review.local.size_bytes for review in rows),
                "popular_rock_folk": sum(review.local.category == "Popular/Rock/Folk" for review in rows),
                "jazz": sum(review.local.category == "Jazz" for review in rows),
                "classical": sum(review.local.category == "Classical" for review in rows),
                "other": sum(review.local.category == "Other" for review in rows),
                "strong_candidates": sum(review.candidate_status == "STRONG_LOW_RATED" for review in rows),
                "manual_review": sum(
                    review.candidate_status in {"LOW_RATED_REVIEW", "SINGLE_SOURCE_REVIEW", "MATCH_REVIEW"}
                    for review in rows
                ),
                "single_source": sum(
                    AlbumPruneService._independent_source_count(review) == 1
                    for review in rows
                ),
                "source_conflict": sum(review.rating_status == "SOURCE_CONFLICT" for review in rows),
            }
        return result

    def candidates(self, threshold: float | None = None) -> list[dict[str, Any]]:
        with ReviewStore(self.store_path) as store:
            return [review.to_dict() for review in store.list_reviews(threshold)]

    def apply_personal_policy(
        self,
        batch_id: str,
        output: Path | None = None,
        *,
        strong_threshold: float = 65,
        review_threshold: float = 70,
    ) -> dict[str, Any]:
        verification = self.verify_calibration_batch(batch_id)
        policy = PersonalPruningPolicy(
            calibration_batch_id=batch_id,
            strong_candidate_threshold=strong_threshold,
            review_candidate_threshold=review_threshold,
        )
        with ReviewStore(self.store_path) as store:
            store.save_personal_policy(policy.to_dict())
        policy_output = output or self.state_root / "calibration" / "personal_album_pruning_policy.yaml"
        policy_output.parent.mkdir(parents=True, exist_ok=True)
        policy_output.write_text(policy.to_yaml(verification["reviewed"]), encoding="utf-8")
        report = self.personal_candidate_report(policy)
        report_output = self.state_root / "calibration" / "personal-candidates.json"
        report_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {
            "status": "PERSONAL_POLICY_APPLIED",
            "calibration": verification,
            "policy": policy.to_dict(),
            "candidate_summary": report["summary"],
            "policy_output": str(policy_output),
            "candidate_output": str(report_output),
        }

    def personal_candidate_report(
        self,
        policy: PersonalPruningPolicy | None = None,
    ) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            if policy is None:
                active = store.active_personal_policy()
                if active is None:
                    raise ValueError("no active personal pruning policy")
                policy = PersonalPruningPolicy.from_dict(active)
            _, review_rows, feedback = self._resolved_calibration_state(
                store,
                policy.calibration_batch_id,
            )
            decisions = {
                review.local.album_id: feedback.get(review.local.album_id, feedback.get(original_id, {}))
                for original_id, review in review_rows
            }
            return build_personal_candidate_report(store.list_reviews(), decisions, policy)

    def personal_candidates(self) -> list[dict[str, Any]]:
        return self.personal_candidate_report()["candidates"]

    def _current_curator_reviews(self, library_root: Path | None) -> tuple[list[AlbumReview], dict[str, Any]]:
        with ReviewStore(self.store_path) as store:
            previous = store.list_reviews()
        if library_root is None:
            return previous, {"mode": "STORED_REVIEW_STATE", "albums_scanned": len(previous)}
        current_albums = scan_albums(library_root)
        by_path = {review.local.path: review for review in previous}
        by_fingerprint: dict[str, list[AlbumReview]] = defaultdict(list)
        by_identity: dict[tuple[str, str, int | None], list[AlbumReview]] = defaultdict(list)
        for review in previous:
            by_fingerprint[review.local.fingerprint].append(review)
            identity = (*canonical_identity(review.local.artist, review.local.album), review.local.year)
            by_identity[identity].append(review)
        result: list[AlbumReview] = []
        matched_by = defaultdict(int)
        for local in current_albums:
            prior = by_path.get(local.path)
            basis = "path"
            if prior is None and len(by_fingerprint[local.fingerprint]) == 1:
                prior = by_fingerprint[local.fingerprint][0]
                basis = "fingerprint"
            if prior is None:
                identity = (*canonical_identity(local.artist, local.album), local.year)
                if len(by_identity[identity]) == 1:
                    prior = by_identity[identity][0]
                    basis = "canonical_identity_year"
            if prior is None:
                result.append(AlbumReview(local=local, canonical=None))
                matched_by["new_unmatched"] += 1
                continue
            self._merge_prior_local_metadata(local, prior.local)
            result.append(replace(prior, local=local, checked=False))
            matched_by[basis] += 1
        return result, {
            "mode": "FULL_LIBRARY_READ_ONLY_SCAN",
            "library_root": str(library_root.expanduser().resolve()),
            "albums_scanned": len(result),
            "audio_files_scanned": sum(review.local.track_count for review in result),
            "metadata_reuse": dict(matched_by),
            "media_writes": 0,
        }

    def analyze_curator(
        self,
        *,
        library_root: Path | None = None,
        apple_source: Path | None = None,
        netease_source: Path | None = None,
        output: Path | None = None,
    ) -> dict[str, Any]:
        reviews, scan_summary = self._current_curator_reviews(library_root)
        apple, apple_summary = match_apple_signals(reviews, apple_source)
        netease, netease_summary = match_netease_signals(reviews, netease_source)
        report = build_curator_report(reviews, apple, netease)
        run_id = "cur_" + now().replace(":", "").replace("-", "").replace("+", "_") + "_" + secrets.token_hex(3)
        report["run_id"] = run_id
        report["inputs"] = {
            "library": scan_summary,
            "apple": apple_summary,
            "netease": netease_summary,
        }
        with ReviewStore(self.store_path) as store:
            store.save_curator_report(run_id, report)
        destination = output or self.state_root / "curator" / "latest.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {**report, "output": str(destination)}

    def curator_report(self) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            return store.latest_curator_report()

    def protect(self, album_id: str, reason: str) -> dict[str, str]:
        with ReviewStore(self.store_path) as store:
            canonical_id = store.protect(album_id, reason)
        return {"status": "PROTECTED", "album_id": album_id, "canonical_album_id": canonical_id}

    def select(self, album_ids: list[str]) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            active = store.active_personal_policy()
            if active is None:
                return store.create_selection(album_ids)
        report = self.personal_candidate_report(PersonalPruningPolicy.from_dict(active))
        allowed = {
            row["local"]["album_id"]
            for row in report["candidates"]
            if row["eligible_for_selection"]
        }
        with ReviewStore(self.store_path) as store:
            return store.create_selection(album_ids, allowed)

    def plan(self, selection_id: str, library_root: Path, quarantine_root: Path) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            return create_delete_plan(store, selection_id, library_root, quarantine_root, self.state_root)

    def preview(self, album_ids: list[str], library_root: Path, quarantine_root: Path) -> dict[str, Any]:
        selection = self.select(album_ids)
        try:
            return self.plan(selection["selection_id"], library_root, quarantine_root)
        except Exception:
            with ReviewStore(self.store_path) as store:
                store.discard_unused_selection(selection["selection_id"])
            raise

    def apply(self, batch_id: str, confirmation: str) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            plan = load_batch_plan(store, batch_id)
            return apply_delete_plan(store, plan, confirmation)

    def rollback(self, batch_id: str, confirmation: str) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            return rollback_batch(store, batch_id, confirmation)

    def recover(self, batch_id: str, confirmation: str) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            return recover_interrupted_batch(store, batch_id, confirmation)

    def purge(self, batch_id: str, confirmation: str) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            return purge_batch(store, batch_id, confirmation)

    def batches(self) -> list[dict[str, Any]]:
        with ReviewStore(self.store_path) as store:
            return store.list_batches()

    def create_calibration_sample(self, size: int = 140, seed: int = 20260718) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            return create_calibration_batch(store, self.state_root, size, seed)

    def calibration_sample(self, batch_id: str) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            sample, review_rows, feedback = self._resolved_calibration_state(store, batch_id)
        return {
            **sample,
            "reviews": [
                {
                    **review.to_dict(),
                    "feedback": {
                        **feedback.get(review.local.album_id, feedback.get(original_id, {
                            "user_decision": "UNREVIEWED",
                            "match_feedback": "UNSURE",
                            "rating_feedback": "UNSURE",
                            "marked_at": "",
                            "calibration_batch_id": batch_id,
                        })),
                        "album_id": review.local.album_id,
                        "music_score": review.music_score,
                    },
                }
                for original_id, review in review_rows
            ],
        }

    def calibration_album_paths(self, batch_id: str) -> list[str]:
        path = self.state_root / "calibration" / batch_id / "sample.json"
        if not path.is_file():
            raise KeyError(f"unknown calibration batch: {batch_id}")
        sample = json.loads(path.read_text(encoding="utf-8"))
        with ReviewStore(self.store_path) as store:
            return [review.local.relative_path for _, review in self._resolve_calibration_reviews(store, sample)]

    def _resolved_calibration_state(
        self,
        store: ReviewStore,
        batch_id: str,
    ) -> tuple[dict[str, Any], list[tuple[str, AlbumReview]], dict[str, dict[str, Any]]]:
        path = self.state_root / "calibration" / batch_id / "sample.json"
        if not path.is_file():
            raise KeyError(f"unknown calibration batch: {batch_id}")
        sample = json.loads(path.read_text(encoding="utf-8"))
        review_rows = self._resolve_calibration_reviews(store, sample)
        batch_feedback = store.calibration_feedback(batch_id)
        resolved_feedback: dict[str, dict[str, Any]] = {}
        for original_id, review in review_rows:
            row = batch_feedback.get(review.local.album_id) or batch_feedback.get(original_id)
            if row is not None:
                resolved_feedback[review.local.album_id] = row
                resolved_feedback[original_id] = row
        return sample, review_rows, resolved_feedback

    def verify_calibration_batch(self, batch_id: str) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            sample, review_rows, feedback = self._resolved_calibration_state(store, batch_id)
        expected = len(sample["album_ids"])
        if len(review_rows) != expected:
            raise ValueError(f"calibration batch resolves {len(review_rows)} of {expected} albums")
        rows = []
        for original_id, review in review_rows:
            row = feedback.get(review.local.album_id) or feedback.get(original_id)
            if row is None or row["user_decision"] == "UNREVIEWED":
                raise ValueError(f"calibration batch is not fully reviewed: {original_id}")
            rows.append(row)
        decisions = {
            name: sum(row["user_decision"] == name for row in rows)
            for name in ("KEEP", "DELETE_CANDIDATE", "LATER", "UNREVIEWED")
        }
        thresholds = {}
        for threshold in (60, 65, 70, 75):
            candidates = [row for row in rows if row["music_score"] is not None and row["music_score"] <= threshold]
            thresholds[str(threshold)] = {
                "candidates": len(candidates),
                "delete_candidate": sum(row["user_decision"] == "DELETE_CANDIDATE" for row in candidates),
                "keep": sum(row["user_decision"] == "KEEP" for row in candidates),
                "later": sum(row["user_decision"] == "LATER" for row in candidates),
            }
        return {
            "status": "CALIBRATION_BATCH_VERIFIED",
            "batch_id": batch_id,
            "reviewed": len(rows),
            **{name.casefold(): count for name, count in decisions.items()},
            "scored": sum(row["music_score"] is not None for row in rows),
            "unscored": sum(row["music_score"] is None for row in rows),
            "thresholds": thresholds,
        }

    @staticmethod
    def _resolve_calibration_reviews(
        store: ReviewStore,
        sample: dict[str, Any],
    ) -> list[tuple[str, AlbumReview]]:
        active = store.list_reviews()
        by_id = {review.local.album_id: review for review in active}
        by_path = {review.local.path: review for review in active}
        relative_paths = list(sample.get("album_paths", []))
        rows: list[tuple[str, AlbumReview]] = []
        for index, album_id in enumerate(sample["album_ids"]):
            review = by_id.get(album_id)
            if review is None:
                relative_path = relative_paths[index] if index < len(relative_paths) else None
                try:
                    historical = store.review(album_id)
                except KeyError:
                    historical = None
                absolute_path = historical.local.path if historical else None
                if absolute_path is None and relative_path:
                    review = next(
                        (item for item in active if item.local.relative_path == relative_path),
                        None,
                    )
                else:
                    review = by_path.get(absolute_path) if absolute_path else None
            if review is not None:
                rows.append((album_id, review))
        return rows

    def library_statistics(self) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            reviews = store.list_reviews()
            active_ids = {review.local.album_id for review in reviews}
            attempts = store.rating_attempt_counts(active_ids)
            musicbrainz_attempts = store.rating_attempts("musicbrainz", active_ids)
        resolved_reviews = [
            review for review in reviews if musicbrainz_attempts.get(review.local.album_id) != "ERROR"
        ]
        matching = {
            status: sum(
                1 for review in resolved_reviews
                if (review.canonical.match_status if review.canonical else "NOT_FOUND") == status
            )
            for status in ("EXACT", "CANONICALIZED", "LIKELY_NEEDS_REVIEW", "AMBIGUOUS", "NOT_FOUND")
        }
        matching["UNRESOLVED_EXTERNAL"] = len(reviews) - len(resolved_reviews)
        reporting_groups = {
            name: [review for review in reviews if self._reporting_group(review) == name]
            for name in ("popular_rock_folk", "classical", "jazz", "chinese", "japanese", "other_cjk", "other")
        }
        return {
            "status": "CALIBRATION_LIBRARY_STATISTICS",
            "albums_scanned": len(reviews),
            "audio_files_scanned": sum(review.local.track_count for review in reviews),
            "total_library_bytes": sum(review.local.size_bytes for review in reviews),
            "matching": matching,
            "rating_coverage": {
                "one_source": sum(len(review.evidence) == 1 for review in reviews),
                "two_or_more_sources": sum(len(review.evidence) >= 2 for review in reviews),
                "insufficient_data": sum(review.rating_status == "INSUFFICIENT_DATA" for review in reviews),
                "source_conflict": sum(review.rating_status == "SOURCE_CONFLICT" for review in reviews),
                "professional_evidence": sum(bool(review.professional_evidence) for review in reviews),
                "professional_score": sum(review.professional_score is not None for review in reviews),
                "professional_protected": sum(
                    review.candidate_status == "PROFESSIONAL_PROTECTED" for review in reviews
                ),
                "community_low_professional_high": sum(
                    review.community_score is not None
                    and review.community_score <= 60
                    and review.professional_score is not None
                    and review.professional_score >= 80
                    for review in reviews
                ),
                "community_high_professional_low": sum(
                    review.community_score is not None
                    and review.community_score >= 70
                    and review.professional_score is not None
                    and review.professional_score <= 55
                    for review in reviews
                ),
                "multi_source_low": sum(
                    self._independent_source_count(review) >= 2
                    and review.music_score is not None
                    and review.music_score <= self.config.threshold
                    for review in reviews
                ),
            },
            "album_types": self._album_type_counts(reviews),
            "candidate_thresholds": self._threshold_counts(reviews),
            "default_checked_count": sum(review.checked for review in reviews),
            "rating_attempts": attempts,
            "rating_scope": {
                scope: sum(review.local.rating_scope == scope for review in reviews)
                for scope in sorted({review.local.rating_scope for review in reviews})
            },
            "language_status": {
                status: sum(review.local.language_bucket == status for review in reviews)
                for status in (
                    "ZH_CONFIRMED",
                    "JA_CONFIRMED",
                    "KO_CONFIRMED",
                    "HK_TW_CANTONESE",
                    "MIXED_CJK",
                    "NON_CJK",
                    "UNKNOWN_CJK",
                )
            },
            "coverage_by_category": {
                name: {
                    "albums": len(rows),
                    "matched": sum(
                        bool(review.canonical and review.canonical.match_status in ALLOWED_MATCHES)
                        for review in rows
                    ),
                    "one_source": sum(len(review.evidence) == 1 for review in rows),
                    "two_or_more_sources": sum(len(review.evidence) >= 2 for review in rows),
                    "insufficient_data": sum(review.rating_status == "INSUFFICIENT_DATA" for review in rows),
                    "professional_evidence": sum(bool(review.professional_evidence) for review in rows),
                    "professional_score": sum(review.professional_score is not None for review in rows),
                }
                for name, rows in reporting_groups.items()
            },
        }

    @staticmethod
    def _reporting_group(review: AlbumReview) -> str:
        if review.local.category == "Classical":
            return "classical"
        if review.local.category == "Jazz":
            return "jazz"
        bucket = review.local.language_bucket
        if bucket in {"ZH_CONFIRMED", "HK_TW_CANTONESE"}:
            return "chinese"
        if bucket == "JA_CONFIRMED":
            return "japanese"
        if bucket in {"KO_CONFIRMED", "MIXED_CJK", "UNKNOWN_CJK"}:
            return "other_cjk"
        if review.local.category == "Popular/Rock/Folk":
            return "popular_rock_folk"
        return "other"

    @staticmethod
    def _independent_source_count(review: AlbumReview) -> int:
        rating_sources = {item.source.casefold() for item in review.evidence}
        professional_sources = {
            item.publication.casefold() for item in review.professional_evidence
        }
        return len(rating_sources) + len(professional_sources)

    def assign_library_rating_scope(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        with ReviewStore(self.store_path) as store:
            for review in store.list_reviews():
                scope, reason = classify_rating_scope(review)
                review.local.rating_scope = scope
                review.local.rating_scope_reason = reason
                if not scope.startswith("INCLUDE"):
                    review.evidence = []
                review = classify(review, self.config)
                store.save_reviews([review])
                counts[scope] = counts.get(scope, 0) + 1
        return {"status": "RATING_SCOPE_ASSIGNED", "counts": counts}

    def import_beets_rating_scope(self, beets_database: Path, library_root: Path) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            return import_beets_scope_metadata(
                store,
                beets_database,
                library_root,
                self.config,
            )

    def enrich_existing_library(
        self,
        source_name: str,
        *,
        language_statuses: set[str] | None = None,
        categories: set[str] | None = None,
        album_ids: set[str] | None = None,
        offline: bool = False,
        refresh: bool = False,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> dict[str, Any]:
        cache = HttpCache(self.state_root / "rating_cache", offline=offline, refresh=refresh)
        if source_name == "musicbrainz":
            source: Any = MusicBrainzSource(cache, user_agent)
        elif source_name == "discogs":
            source = DiscogsSource(cache, user_agent)
        elif source_name == "official-awards":
            source = OfficialAwardsSource(cache, user_agent)
            source.load()
        else:
            raise ValueError("source must be musicbrainz, discogs, or official-awards")
        counts: dict[str, int] = {}
        errors: list[dict[str, str]] = []
        with ReviewStore(self.store_path) as store:
            reviews = store.list_reviews()
            if language_statuses:
                reviews = [
                    review for review in reviews
                    if review.local.language_bucket in language_statuses
                ]
            if categories:
                reviews = [review for review in reviews if review.local.category in categories]
            if album_ids:
                reviews = [review for review in reviews if review.local.album_id in album_ids]
            for review in reviews:
                status = "NOT_FOUND"
                error: str | None = None
                if source_name == "official-awards":
                    try:
                        review.professional_evidence = source.evidence_for(review.local, review.canonical)
                        if self._apply_professional_scope_metadata(
                            review.local,
                            review.professional_evidence,
                        ):
                            scope, reason = classify_rating_scope(review)
                            review.local.rating_scope = scope
                            review.local.rating_scope_reason = reason
                            if not scope.startswith("INCLUDE"):
                                review.evidence = []
                        status = "EVIDENCE" if review.professional_evidence else "NOT_FOUND"
                        review = classify(review, self.config)
                        store.save_reviews([review])
                    except Exception as exc:
                        status = "ERROR"
                        error = str(exc)
                        errors.append({"album_id": review.local.album_id, "source": source_name, "error": error})
                    store.save_rating_attempt(review.local.album_id, source_name, status, error)
                    counts[status] = counts.get(status, 0) + 1
                    continue
                if source_name == "musicbrainz" and (
                    review.local.language_bucket in {"UNKNOWN_CJK", "JA_CONFIRMED"}
                    or review.local.category in {"Classical", "Jazz"}
                ):
                    try:
                        trace = source.resolve_artist_metadata(review.local)
                        if isinstance(trace, dict):
                            review.resolution_trace.append(trace)
                        scope, reason = classify_rating_scope(review)
                        review.local.rating_scope = scope
                        review.local.rating_scope_reason = reason
                    except Exception as exc:
                        errors.append({
                            "album_id": review.local.album_id,
                            "source": "musicbrainz-artist",
                            "error": str(exc),
                        })
                if not review.local.rating_scope.startswith("INCLUDE"):
                    status = (
                        "SKIPPED_CHINESE"
                        if review.local.rating_scope == "EXCLUDE_CHINESE"
                        else "SKIPPED_LANGUAGE_REVIEW"
                    )
                    store.save_rating_attempt(review.local.album_id, source_name, status)
                    counts[status] = counts.get(status, 0) + 1
                    continue
                try:
                    matched = source.lookup_album(review.local)
                    existing_is_resolved = bool(
                        review.canonical and review.canonical.match_status in ALLOWED_MATCHES
                    )
                    matched_is_resolved = bool(matched and matched.match_status in ALLOWED_MATCHES)
                    if matched_is_resolved and (source_name == "musicbrainz" or not existing_is_resolved):
                        review.canonical = matched
                    elif matched is not None and not existing_is_resolved:
                        review.canonical = matched
                    if matched is not None:
                        status = matched.match_status
                    rating = (
                        source.fetch_rating(matched)
                        if matched and matched.match_status in ALLOWED_MATCHES
                        else None
                    )
                    review.evidence = [item for item in review.evidence if item.source != source_name]
                    if rating is not None:
                        review.evidence.append(rating)
                        status = "RATED"
                    elif matched and matched.match_status in ALLOWED_MATCHES:
                        status = "MATCHED_NO_RATING"
                    review.resolution_trace.extend(getattr(source, "last_trace", []))
                    review.resolution_trace = review.resolution_trace[-100:]
                    review = classify(review, self.config)
                    store.save_reviews([review])
                except Exception as exc:
                    status = "ERROR"
                    error = str(exc)
                    errors.append({"album_id": review.local.album_id, "source": source_name, "error": error})
                store.save_rating_attempt(review.local.album_id, source_name, status, error)
                counts[status] = counts.get(status, 0) + 1
        return {
            "status": "LIBRARY_RATING_ENRICHMENT_COMPLETE",
            "source": source_name,
            "language_statuses": sorted(language_statuses or []),
            "categories": sorted(categories or []),
            "album_ids_requested": len(album_ids or []),
            "albums_processed": sum(counts.values()),
            "result_counts": counts,
            "error_count": len(errors),
            "errors": errors[:100],
            "errors_truncated": len(errors) > 100,
        }

    def save_calibration_feedback(self, **values: str) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            return store.save_calibration_feedback(**values)

    def calibration_report(self) -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            active = store.active_personal_policy()
            if active is None:
                feedback = store.calibration_feedback()
            else:
                policy = PersonalPruningPolicy.from_dict(active)
                _, review_rows, resolved = self._resolved_calibration_state(store, policy.calibration_batch_id)
                feedback = {
                    review.local.album_id: resolved.get(review.local.album_id, resolved.get(original_id, {}))
                    for original_id, review in review_rows
                }
            return threshold_report(store.list_reviews(), feedback)

    def write_policy_template(self, output: Path, batch_id: str = "") -> dict[str, Any]:
        with ReviewStore(self.store_path) as store:
            reviewed = sum(
                row["user_decision"] != "UNREVIEWED" for row in store.calibration_feedback().values()
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(policy_template(batch_id, reviewed), encoding="utf-8")
        return {"status": "POLICY_TEMPLATE_READY", "output": str(output), "music_score_threshold": None}
