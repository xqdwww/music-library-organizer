from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from music_library_organizer.album_prune.calibration import (
    classify_rating_scope,
    create_read_only_baseline,
    policy_template,
    stratified_sample,
    threshold_report,
    verify_read_only_baseline,
)
from music_library_organizer.album_prune.calibration_webui import (
    HTML as CALIBRATION_HTML,
)
from music_library_organizer.album_prune.calibration_webui import (
    CalibrationHandler,
)
from music_library_organizer.album_prune.models import (
    MATCH_EXACT,
    AlbumReview,
    CanonicalAlbum,
    ProfessionalEvidence,
    RatingEvidence,
)
from music_library_organizer.album_prune.normalize import canonical_identity, normalized_text
from music_library_organizer.album_prune.personal_policy import (
    PersonalPruningPolicy,
    build_personal_candidate_report,
)
from music_library_organizer.album_prune.quarantine import (
    apply_delete_plan,
    create_delete_plan,
    purge_batch,
    recover_interrupted_batch,
    rollback_batch,
)
from music_library_organizer.album_prune.ratings import (
    MAX_RESPONSE_BYTES,
    CachedResponse,
    DiscogsSource,
    MusicBrainzSource,
    _read_response,
    normalize_rating,
)
from music_library_organizer.album_prune.scanner import scan_albums
from music_library_organizer.album_prune.scoring import ScoringConfig, aggregate_music_score, classify
from music_library_organizer.album_prune.service import AlbumPruneService
from music_library_organizer.album_prune.store import ReviewStore
from music_library_organizer.album_prune.web_security import validate_loopback_request
from music_library_organizer.album_prune.webui import HTML as REVIEW_HTML


def evidence(source: str, score: float, count: int = 100, group: str = "community") -> RatingEvidence:
    return RatingEvidence(
        source=source,
        source_album_id=f"{source}-id",
        source_album_url=f"https://example.test/{source}",
        raw_score=score / 20,
        raw_scale=5,
        normalized_score_100=score,
        rating_count=count if group == "community" else None,
        review_count=count if group == "critic" else None,
        critic_or_community=group,
        matched_artist="Artist",
        matched_album="Album",
        matched_year=2001,
        match_basis="fixture exact match",
        fetched_at="2026-07-17T00:00:00+00:00",
        response_cache_path="fixture.json",
        adapter_version="fixture-v1",
    )


def canonical(status: str = MATCH_EXACT, secondary: list[str] | None = None) -> CanonicalAlbum:
    return CanonicalAlbum(
        canonical_album_id="musicbrainz:release-group:fixture",
        source="musicbrainz",
        source_album_id="fixture",
        source_album_url="https://musicbrainz.org/release-group/fixture",
        artist="Artist",
        album="Album",
        year=2001,
        primary_type="Album",
        secondary_types=secondary or [],
        match_status=status,
        match_basis="fixture",
    )


class FakeCache:
    def __init__(self, payloads: list[dict]):
        self.payloads = iter(payloads)

    def fetch_json(self, *args: object, **kwargs: object) -> CachedResponse:
        return CachedResponse(next(self.payloads), Path("fixture.json"), "2026-07-17T00:00:00+00:00", False)


class AlbumPruneScoringTests(unittest.TestCase):
    def test_normalization_handles_unicode_punctuation_and_editions(self) -> None:
        self.assertEqual(normalized_text("Ａｒｔｉｓｔ　名字"), "artist 名字")
        self.assertEqual(canonical_identity("The Police", "Album（Deluxe Edition）"), ("police", "album"))
        self.assertEqual(canonical_identity("宇多田 ヒカル", "初恋"), ("宇多田 ヒカル", "初恋"))

    def test_rating_scale_normalization(self) -> None:
        self.assertEqual(normalize_rating(3.1, 5), 62)
        self.assertEqual(normalize_rating(7.5, 10), 75)
        self.assertEqual(normalize_rating(82, 100), 82)
        with self.assertRaises(ValueError):
            normalize_rating(6, 5)

    def test_aggregation_and_conflict(self) -> None:
        score, status = aggregate_music_score([evidence("a", 50), evidence("b", 60)], ScoringConfig())
        self.assertEqual((score, status), (55, "RATED"))
        score, status = aggregate_music_score([evidence("a", 40), evidence("b", 70)], ScoringConfig())
        self.assertEqual(status, "SOURCE_CONFLICT")
        self.assertEqual(score, 55)

    def test_critic_and_community_groups_do_not_use_popularity(self) -> None:
        rows = [evidence("critic", 40, 8, "critic"), evidence("community", 60, 500)]
        score, status = aggregate_music_score(rows, ScoringConfig())
        self.assertEqual(status, "RATED")
        self.assertEqual(score, 50)  # The per-source 50% cap overrides the 60/40 target with two sources.

    def test_classification_requires_controlled_match_and_two_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Artist/Album"
            root.mkdir(parents=True)
            (root / "01.flac").write_bytes(b"audio")
            local = scan_albums(Path(temporary))[0]
            review = classify(
                AlbumReview(local=local, canonical=canonical(), evidence=[evidence("a", 50), evidence("b", 55)]),
                ScoringConfig(),
            )
            self.assertEqual(review.candidate_status, "STRONG_LOW_RATED")
            self.assertFalse(review.checked)
            review = classify(AlbumReview(local=local, canonical=canonical("LIKELY_NEEDS_REVIEW")), ScoringConfig())
            self.assertEqual(review.candidate_status, "MATCH_REVIEW")

    def test_excluded_soundtrack_never_becomes_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Artist/Album"
            path.mkdir(parents=True)
            (path / "01.flac").write_bytes(b"audio")
            local = scan_albums(Path(temporary))[0]
            review = classify(
                AlbumReview(
                    local=local,
                    canonical=canonical(secondary=["Soundtrack"]),
                    evidence=[evidence("a", 30), evidence("b", 40)],
                ),
                ScoringConfig(),
            )
            self.assertEqual(review.candidate_status, "EXCLUDED_TYPE")


class AlbumPruneSourceTests(unittest.TestCase):
    def test_musicbrainz_exact_lookup_and_rating_fixture(self) -> None:
        search = {
            "release-groups": [
                {
                    "id": "rg1",
                    "title": "Album",
                    "first-release-date": "2001-01-01",
                    "artist-credit": [{"name": "Artist"}],
                }
            ]
        }
        lookup = {
            "id": "rg1",
            "title": "Album",
            "first-release-date": "2001-01-01",
            "primary-type": "Album",
            "secondary-types": [],
            "artist-credit": [{"name": "Artist"}],
            "rating": {"value": 2.7, "votes-count": 20},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Artist/Album"
            path.mkdir(parents=True)
            (path / "01.flac").write_bytes(b"audio")
            local = scan_albums(Path(temporary))[0]
        source = MusicBrainzSource(FakeCache([search, lookup]), "test (contact: test@example.com)")
        matched = source.lookup_album(local)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.match_status, MATCH_EXACT)
        self.assertEqual(matched.release_group_id, "rg1")
        self.assertEqual(source.last_trace[-1]["selected_candidate"], "rg1")
        rating = source.fetch_rating(matched)
        self.assertEqual(rating.normalized_score_100, 54)
        self.assertEqual(rating.rating_count, 20)

    def test_musicbrainz_ambiguous_fixture_is_not_selectable_match(self) -> None:
        row = {
            "title": "Album",
            "first-release-date": "2001",
            "artist-credit": [{"name": "Artist"}],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Artist/Album"
            path.mkdir(parents=True)
            (path / "01.flac").write_bytes(b"audio")
            local = scan_albums(Path(temporary))[0]
        source = MusicBrainzSource(
            FakeCache([{"release-groups": [{**row, "id": "one"}, {**row, "id": "two"}]}]),
            "test (contact: test@example.com)",
        )
        matched = source.lookup_album(local)
        self.assertEqual(matched.match_status, "AMBIGUOUS")

    def test_discogs_uses_main_release_rating_and_ignores_popularity(self) -> None:
        search = {"results": [{"id": 303567, "title": "Adele (3) - 21", "year": "2011"}]}
        master = {
            "id": 303567,
            "title": "21",
            "year": 2011,
            "main_release": 2664589,
            "uri": "https://www.discogs.com/master/303567-Adele-21",
            "artists": [{"name": "Adele (3)"}],
        }
        release = {
            "id": 2664589,
            "uri": "https://www.discogs.com/release/2664589-Adele-21",
            "community": {
                "want": 999,
                "have": 888,
                "rating": {"count": 2492, "average": 4.43},
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "Adele/21 [2011]"
            path.mkdir(parents=True)
            (path / "01.flac").write_bytes(b"audio")
            local = scan_albums(Path(temporary))[0]
        source = DiscogsSource(FakeCache([search, master, release]), "test (contact: test@example.com)")
        matched = source.lookup_album(local)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.master_id, "303567")
        self.assertEqual(matched.main_release_id, "2664589")
        self.assertEqual(matched.relation_type, "master-main-release")
        rating = source.fetch_rating(matched)
        self.assertEqual(rating.normalized_score_100, 88.6)
        self.assertEqual(rating.rating_count, 2492)
        self.assertFalse(hasattr(rating, "want"))
        self.assertFalse(hasattr(rating, "have"))


class AlbumPruneSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = self.root / "library"
        self.album = self.library / "Artist/Album"
        self.album.mkdir(parents=True)
        (self.album / "01.flac").write_bytes(b"one")
        (self.album / "cover.jpg").write_bytes(b"cover")
        self.state = self.root / "state"
        self.quarantine = self.root / "quarantine"
        self.store_path = self.state / "review.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def ready_store(self) -> tuple[ReviewStore, str]:
        local = scan_albums(self.library)[0]
        review = classify(
            AlbumReview(local=local, canonical=canonical(), evidence=[evidence("a", 45), evidence("b", 55)]),
            ScoringConfig(),
        )
        store = ReviewStore(self.store_path)
        store.save_reviews([review])
        selection = store.create_selection([local.album_id])
        return store, selection["selection_id"]

    def test_dry_plan_is_read_only_and_defaults_unchecked(self) -> None:
        store, selection_id = self.ready_store()
        before = (self.album / "01.flac").read_bytes()
        plan = create_delete_plan(store, selection_id, self.library, self.quarantine, self.state)
        self.assertEqual(plan["status"], "DELETE_PLAN_READY")
        self.assertTrue((self.album / "01.flac").is_file())
        self.assertEqual((self.album / "01.flac").read_bytes(), before)
        self.assertFalse(store.review(plan["albums"][0]["album_id"]).checked)
        store.close()

    def test_preview_selection_and_plan_contains_required_read_only_fields(self) -> None:
        store, selection_id = self.ready_store()
        selected = store.selection(selection_id)
        plan = create_delete_plan(store, selection_id, self.library, self.quarantine, self.state)
        self.assertEqual(len(selected["album_ids"]), 1)
        self.assertEqual(plan["album_count"], 1)
        self.assertEqual(plan["file_count"], 2)
        self.assertEqual(plan["size_bytes"], len(b"one") + len(b"cover"))
        self.assertEqual(plan["quarantine_batch_root"], str(self.quarantine.resolve() / plan["batch_id"]))
        self.assertTrue((self.album / "01.flac").is_file())
        self.assertTrue((self.album / "cover.jpg").is_file())
        self.assertFalse(Path(plan["quarantine_batch_root"]).exists())
        store.close()

    def test_service_preview_creates_selection_and_plan_without_quarantine(self) -> None:
        service = AlbumPruneService(self.state)
        local = scan_albums(self.library)[0]
        review = classify(
            AlbumReview(local=local, canonical=canonical(), evidence=[evidence("a", 45), evidence("b", 55)]),
            ScoringConfig(),
        )
        with ReviewStore(service.store_path) as store:
            store.save_reviews([review])
        plan = service.preview([local.album_id], self.library, self.quarantine)
        with ReviewStore(service.store_path) as store:
            self.assertEqual(store.connection.execute("SELECT count(*) FROM selections").fetchone()[0], 1)
            self.assertEqual(store.connection.execute("SELECT count(*) FROM batches").fetchone()[0], 1)
        self.assertEqual(plan["album_count"], 1)
        self.assertEqual(plan["file_count"], 2)
        self.assertFalse(Path(plan["quarantine_batch_root"]).exists())

    def test_service_preview_failure_discards_unused_selection(self) -> None:
        service = AlbumPruneService(self.state)
        local = scan_albums(self.library)[0]
        review = classify(
            AlbumReview(local=local, canonical=canonical(), evidence=[evidence("a", 45), evidence("b", 55)]),
            ScoringConfig(),
        )
        with ReviewStore(service.store_path) as store:
            store.save_reviews([review])
        with patch(
            "music_library_organizer.album_prune.service.create_delete_plan",
            side_effect=OSError("fixture plan failure"),
        ):
            with self.assertRaisesRegex(OSError, "fixture plan failure"):
                service.preview([local.album_id], self.library, self.quarantine)
        with ReviewStore(service.store_path) as store:
            self.assertEqual(store.connection.execute("SELECT count(*) FROM selections").fetchone()[0], 0)
            self.assertEqual(store.connection.execute("SELECT count(*) FROM batches").fetchone()[0], 0)


    def _review(self, root: Path, artist: str, album: str, score: float | None, category: str) -> AlbumReview:
        path = root / artist / album
        path.mkdir(parents=True)
        (path / "01.flac").write_bytes(b"audio")
        local = scan_albums(root, album_paths=[f"{artist}/{album}"])[0]
        local.category = category
        rows = [] if score is None else [evidence("fixture", score)]
        review = classify(AlbumReview(local=local, canonical=canonical(), evidence=rows), ScoringConfig())
        review.local.rating_scope, review.local.rating_scope_reason = classify_rating_scope(review)
        return review

    def test_full_scan_baseline_is_read_only_and_samples_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "musicfinal"
            album = root / "Artist/Album"
            album.mkdir(parents=True)
            track = album / "01.flac"
            track.write_bytes(b"audio")
            (album / "cover.jpg").write_bytes(b"cover")
            baseline_path = Path(temporary) / "state/baseline.json"
            before = track.stat()
            baseline = create_read_only_baseline(root, baseline_path, seed=7)
            reviews = scan_albums(root)
            verification = verify_read_only_baseline(root, baseline_path)
            after = track.stat()
            self.assertEqual(len(reviews), 1)
            self.assertEqual(baseline["audio_files"], 1)
            self.assertEqual(verification["status"], "READ_ONLY_VERIFIED")
            self.assertEqual(
                (before.st_ino, before.st_size, before.st_mtime_ns),
                (after.st_ino, after.st_size, after.st_mtime_ns),
            )
            self.assertFalse((Path(temporary) / "quarantine").exists())

    def test_no_ratings_rescan_preserves_existing_external_evidence(self) -> None:
        service = AlbumPruneService(self.state)
        local = scan_albums(self.library)[0]
        local.artist_aliases = ["Performer Alias"]
        local.category = "Classical"
        professional = ProfessionalEvidence(
            publication="Fixture Award",
            publication_type="official_music_award",
            source_title="Fixture winner",
            source_url="https://example.test/award",
            source_entity_id="fixture-award",
            evidence_type="AWARD_WINNER",
            match_confidence=0.98,
            normalized_score_100=95,
            protection_reason="AWARD_WINNER",
        )
        original = classify(
            AlbumReview(
                local=local,
                canonical=canonical(),
                evidence=[evidence("musicbrainz", 80)],
                professional_evidence=[professional],
            ),
            ScoringConfig(),
        )
        with ReviewStore(service.store_path) as store:
            store.save_reviews([original])

        result = service.scan(self.library, ratings=False, professional=False)

        with ReviewStore(service.store_path) as store:
            rescanned = store.list_reviews()[0]
        self.assertEqual(result["source_status"]["musicbrainz"], "preserved_not_refetched")
        self.assertEqual(rescanned.canonical.canonical_album_id, original.canonical.canonical_album_id)
        self.assertEqual([item.source for item in rescanned.evidence], ["musicbrainz"])
        self.assertEqual([item.publication for item in rescanned.professional_evidence], ["Fixture Award"])
        self.assertIn("Performer Alias", rescanned.local.artist_aliases)
        self.assertEqual(rescanned.local.category, "Other")

    def test_no_ratings_rescan_drops_western_scores_when_chinese_is_confirmed(self) -> None:
        chinese_library = self.root / "chinese-library"
        album = chinese_library / "王菲/唱游"
        album.mkdir(parents=True)
        (album / "01.flac").write_bytes(b"audio")
        service = AlbumPruneService(self.state)
        local = scan_albums(chinese_library)[0]
        local.release_countries = ["CN"]
        original = classify(
            AlbumReview(
                local=local,
                canonical=canonical(),
                evidence=[evidence("musicbrainz", 80)],
            ),
            ScoringConfig(),
        )
        with ReviewStore(service.store_path) as store:
            store.save_reviews([original])

        service.scan(chinese_library, ratings=False, professional=False)

        with ReviewStore(service.store_path) as store:
            rescanned = store.list_reviews()[0]
        self.assertEqual(rescanned.local.language_bucket, "ZH_CONFIRMED")
        self.assertEqual(rescanned.local.rating_scope, "EXCLUDE_CHINESE")
        self.assertEqual(rescanned.evidence, [])
        self.assertIsNone(rescanned.music_score)

    def test_stratified_sample_is_reproducible_and_not_low_score_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reviews = []
            categories = ["Popular/Rock/Folk", "Jazz", "Classical", "Other"]
            for index in range(24):
                score = (35, 60, 85, None)[index % 4]
                reviews.append(self._review(root, f"Artist{index}", f"Album{index}", score, categories[index % 4]))
            first = stratified_sample(reviews, size=16, seed=42)
            second = stratified_sample(reviews, size=16, seed=42)
            self.assertEqual([row.local.album_id for row in first], [row.local.album_id for row in second])
            self.assertEqual({row.local.category for row in first}, set(categories))
            self.assertTrue(any(row.music_score is None or row.music_score > 70 for row in first))

    def test_calibration_batch_survives_album_id_drift_after_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = AlbumPruneService(root / "state")
            original = self._review(root, "Artist", "Album", 75, "Popular/Rock/Folk")
            original.local.rating_scope = "INCLUDE_NON_CHINESE"
            with ReviewStore(service.store_path) as store:
                store.save_reviews([original])
            batch = service.create_calibration_sample(size=1, seed=1)
            service.save_calibration_feedback(
                album_id=original.local.album_id,
                user_decision="LATER",
                match_feedback="UNSURE",
                rating_feedback="UNSURE",
                calibration_batch_id=batch["calibration_batch_id"],
            )
            replacement = self._review(root, "Artist Renamed", "Album", 75, "Popular/Rock/Folk")
            replacement.local.path = original.local.path
            replacement.local.relative_path = original.local.relative_path
            replacement.local.album_id = "local:replacement"
            replacement.local.rating_scope = "INCLUDE_NON_CHINESE"
            with ReviewStore(service.store_path) as store:
                store.save_reviews([replacement])
            resolved = service.calibration_sample(batch["calibration_batch_id"])
            self.assertEqual(len(resolved["reviews"]), 1)
            self.assertEqual(resolved["reviews"][0]["local"]["album_id"], "local:replacement")
            self.assertEqual(resolved["reviews"][0]["feedback"]["album_id"], "local:replacement")
            self.assertEqual(resolved["reviews"][0]["feedback"]["user_decision"], "LATER")
            self.assertEqual(
                service.calibration_album_paths(batch["calibration_batch_id"]),
                [original.local.relative_path],
            )

    def test_classical_and_jazz_are_identified_without_becoming_scores(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in (root / "古典/Beethoven 5", root / "爵士/Miles Session"):
                path.mkdir(parents=True)
                (path / "01.flac").write_bytes(b"audio")
            rows = scan_albums(root)
            self.assertEqual({row.category for row in rows}, {"Classical", "Jazz"})
            self.assertTrue(all(not row.professional_evidence for row in rows))

    def test_rating_scope_excludes_explicit_chinese_and_withholds_ambiguous_han(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            chinese = self._review(root, "王菲", "唱游", 80, "Popular/Rock/Folk")
            chinese.local.genres = ["华语"]
            japanese = self._review(root, "椎名林檎", "無罪モラトリアム", 80, "Popular/Rock/Folk")
            classical = self._review(root, "贝多芬", "第五交响曲", 80, "Classical")
            self.assertEqual(classify_rating_scope(chinese)[0], "EXCLUDE_CHINESE")
            self.assertEqual(classify_rating_scope(japanese)[0], "INCLUDE_NON_CHINESE")
            self.assertEqual(classify_rating_scope(classical)[0], "INCLUDE_SPECIALTY")
            ambiguous = self._review(root, "王菲", "浮躁", 80, "Popular/Rock/Folk")
            self.assertEqual(classify_rating_scope(ambiguous)[0], "REVIEW_CJK_LANGUAGE")
            ambiguous.local.scripts = ["Jpan"]
            self.assertEqual(classify_rating_scope(ambiguous)[0], "INCLUDE_NON_CHINESE")
            latin_chinese = self._review(root, "A-Lin", "Album", 80, "Popular/Rock/Folk")
            latin_chinese.local.scripts = ["Hant"]
            self.assertEqual(classify_rating_scope(latin_chinese)[0], "EXCLUDE_CHINESE")

    def test_overlapping_sample_roots_do_not_double_count_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            album = root / "Artist/Album"
            album.mkdir(parents=True)
            (album / "01.flac").write_bytes(b"audio")
            rows = scan_albums(root, album_paths=["Artist", "Artist/Album"])
            self.assertEqual(sum(row.track_count for row in rows), 1)

    def test_album_path_scan_does_not_fall_back_to_full_library(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for artist in ("A", "B"):
                album = root / artist / "Album"
                album.mkdir(parents=True)
                (album / "01.flac").write_bytes(b"audio")
            rows = scan_albums(root, album_paths=["A/Album"])
            self.assertEqual([row.relative_path for row in rows], ["A/Album"])

    def test_feedback_is_independent_and_does_not_change_music_score(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            review = self._review(root, "Artist", "Album", 50, "Popular/Rock/Folk")
            store = ReviewStore(root / "state.sqlite3")
            store.save_reviews([review])
            original = store.review(review.local.album_id).music_score
            row = store.save_calibration_feedback(
                review.local.album_id, "KEEP", "WRONG", "INCOMPLETE", "cal_fixture"
            )
            self.assertEqual(row["match_feedback"], "WRONG")
            self.assertEqual(row["rating_feedback"], "INCOMPLETE")
            self.assertEqual(store.review(review.local.album_id).music_score, original)
            store.close()

    def test_threshold_formulas_and_insufficient_labels_do_not_recommend(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            low = self._review(root, "A", "Low", 40, "Popular/Rock/Folk")
            high = self._review(root, "B", "High", 80, "Jazz")
            feedback = {
                low.local.album_id: {"user_decision": "DELETE_CANDIDATE"},
                high.local.album_id: {"user_decision": "KEEP"},
            }
            report = threshold_report([low, high], feedback)
            row45 = next(row for row in report["thresholds"] if row["threshold"] == 45)
            self.assertEqual(row45["candidate_hit_rate"], 1.0)
            self.assertEqual(row45["false_positive_rate"], 0.0)
            self.assertEqual(row45["recall"], 1.0)
            self.assertEqual(report["report_status"], "INSUFFICIENT_CALIBRATION_LABELS")
            self.assertFalse(report["recommendation_generated"])

    def test_policy_threshold_is_null_and_calibration_ui_has_no_destructive_api(self) -> None:
        self.assertIn("music_score_threshold: null", policy_template())
        self.assertIn("selected_threshold: null", policy_template())
        self.assertNotIn("/api/apply", CALIBRATION_HTML)
        self.assertNotIn("/api/purge", CALIBRATION_HTML)
        self.assertIn("<button disabled>隔离</button>", CALIBRATION_HTML)
        self.assertIn("<button disabled>永久清空</button>", CALIBRATION_HTML)

    def test_loopback_controls_reject_dns_rebinding_and_cross_origin_requests(self) -> None:
        validate_loopback_request(
            {"Host": "127.0.0.1:8765", "Origin": "http://localhost:8765"},
            8765,
            require_origin=True,
        )
        with self.assertRaisesRegex(PermissionError, "loopback Host"):
            validate_loopback_request({"Host": "attacker.example:8765"}, 8765)
        with self.assertRaisesRegex(PermissionError, "same-origin"):
            validate_loopback_request(
                {"Host": "127.0.0.1:8765", "Origin": "https://attacker.example"},
                8765,
                require_origin=True,
            )

    def test_calibration_body_requires_origin_csrf_object_and_bounded_size(self) -> None:
        handler = CalibrationHandler.__new__(CalibrationHandler)
        handler.server = SimpleNamespace(server_port=8767, csrf="fixture")
        handler.headers = {
            "Host": "127.0.0.1:8767",
            "Origin": "http://127.0.0.1:8767",
            "X-Calibration-CSRF": "fixture",
            "Content-Length": "2",
        }
        handler.rfile = BytesIO(b"{}")
        self.assertEqual(handler._body(), {})
        handler.headers["Content-Length"] = str(1024 * 1024 + 1)
        with self.assertRaisesRegex(ValueError, "too large"):
            handler._body()

    def test_review_html_escapes_remote_labels_and_rejects_non_http_links(self) -> None:
        for html in (REVIEW_HTML, CALIBRATION_HTML):
            self.assertIn("safeUrl", html)
            self.assertIn("['http:','https:']", html)
        self.assertIn("${esc(e.source)}", REVIEW_HTML)
        self.assertNotIn('href="${e.source_album_url}"', REVIEW_HTML)
        self.assertIn("Data provided by Discogs.", REVIEW_HTML)
        self.assertIn("Data provided by Discogs.", CALIBRATION_HTML)

    def test_public_metadata_response_is_bounded(self) -> None:
        class OversizedResponse:
            def read(self, requested: int) -> bytes:
                self.requested = requested
                return b"x" * requested

        response = OversizedResponse()
        with self.assertRaisesRegex(ValueError, "exceeds 16 MiB"):
            _read_response(response)
        self.assertEqual(response.requested, MAX_RESPONSE_BYTES + 1)

    def test_personal_policy_keeps_machine_and_user_candidate_groups_independent(self) -> None:
        strong = self._review(self.root, "Strong", "Low", 60, "Popular/Rock/Folk")
        strong.evidence = [evidence("a", 60), evidence("b", 60)]
        strong = classify(strong, ScoringConfig())
        review = self._review(self.root, "Review", "Conflict", 68, "Popular/Rock/Folk")
        review.evidence = [evidence("a", 50), evidence("b", 86)]
        review = classify(review, ScoringConfig())
        single = self._review(self.root, "Single", "Low", 60, "Popular/Rock/Folk")
        keep = self._review(self.root, "Keep", "Low", 60, "Popular/Rock/Folk")
        keep.evidence = [evidence("a", 60), evidence("b", 60)]
        keep = classify(keep, ScoringConfig())
        unscored = self._review(self.root, "Manual", "Unscored", None, "Classical")
        high = self._review(self.root, "Manual", "High", 82, "Jazz")
        later = self._review(self.root, "Manual", "Later", None, "Jazz")
        protected = self._review(self.root, "Protected", "Award", 60, "Jazz")
        protected.evidence = [evidence("a", 60), evidence("b", 60)]
        protected = classify(protected, ScoringConfig())
        protected.candidate_status = "PROFESSIONAL_PROTECTED"
        protected.professional_score = 95
        protected.protection_reasons = ["AWARD_WINNER"]
        ambiguous = self._review(self.root, "Ambiguous", "Low", 60, "Popular/Rock/Folk")
        ambiguous.evidence = [evidence("a", 60), evidence("b", 60)]
        ambiguous.canonical = replace(canonical(), match_status="AMBIGUOUS")
        ambiguous = classify(ambiguous, ScoringConfig())
        reviews = [strong, review, single, keep, unscored, high, later, protected, ambiguous]
        feedback = {
            strong.local.album_id: {"user_decision": "DELETE_CANDIDATE"},
            keep.local.album_id: {"user_decision": "KEEP"},
            unscored.local.album_id: {"user_decision": "DELETE_CANDIDATE"},
            high.local.album_id: {"user_decision": "DELETE_CANDIDATE"},
            later.local.album_id: {"user_decision": "LATER"},
            protected.local.album_id: {"user_decision": "DELETE_CANDIDATE"},
        }
        report = build_personal_candidate_report(
            reviews,
            feedback,
            PersonalPruningPolicy("cal_fixture"),
        )
        self.assertEqual(report["summary"]["strong_low_score"], 1)
        self.assertEqual(report["summary"]["review_65_to_70"], 1)
        self.assertEqual(report["summary"]["explicit_user_candidates"], 3)
        self.assertEqual(report["summary"]["later"], 1)
        self.assertEqual(report["summary"]["total_unique_candidates"], 4)
        rows = {row["local"]["album_id"]: row for row in report["candidates"]}
        self.assertEqual(
            rows[strong.local.album_id]["candidate_groups"],
            ["USER_SELECTED_CANDIDATE", "STRONG_PERSONAL_CANDIDATE"],
        )
        self.assertIn(unscored.local.album_id, rows)
        self.assertIn(high.local.album_id, rows)
        self.assertNotIn(single.local.album_id, rows)
        self.assertNotIn(keep.local.album_id, rows)
        self.assertNotIn(protected.local.album_id, rows)
        self.assertNotIn(ambiguous.local.album_id, rows)
        self.assertTrue(all(not row["checked"] for row in rows.values()))

    def test_completed_batch_can_enable_policy_without_selection_or_batch(self) -> None:
        service = AlbumPruneService(self.state)
        delete = self._review(self.root, "Delete", "Album", 60, "Popular/Rock/Folk")
        delete.evidence = [evidence("a", 60), evidence("b", 60)]
        delete = classify(delete, ScoringConfig())
        keep = self._review(self.root, "Keep", "Album", 80, "Popular/Rock/Folk")
        with ReviewStore(service.store_path) as store:
            store.save_reviews([delete, keep])
        batch = service.create_calibration_sample(size=2, seed=7)
        service.save_calibration_feedback(
            album_id=delete.local.album_id,
            user_decision="DELETE_CANDIDATE",
            match_feedback="CORRECT",
            rating_feedback="CORRECT",
            calibration_batch_id=batch["calibration_batch_id"],
        )
        service.save_calibration_feedback(
            album_id=keep.local.album_id,
            user_decision="KEEP",
            match_feedback="CORRECT",
            rating_feedback="CORRECT",
            calibration_batch_id=batch["calibration_batch_id"],
        )
        result = service.apply_personal_policy(batch["calibration_batch_id"])
        self.assertEqual(result["calibration"]["reviewed"], 2)
        self.assertEqual(result["candidate_summary"]["explicit_user_candidates"], 1)
        self.assertIn("automatic_selection: false", Path(result["policy_output"]).read_text())
        with ReviewStore(service.store_path) as store:
            self.assertEqual(store.active_personal_policy()["strong_candidate_threshold"], 65)
            self.assertEqual(store.connection.execute("SELECT count(*) FROM selections").fetchone()[0], 0)
            self.assertEqual(store.connection.execute("SELECT count(*) FROM batches").fetchone()[0], 0)

    def test_personal_review_ui_has_four_groups_and_no_default_checked_markup(self) -> None:
        for label in ("强低分候选", "65–70 人工审核", "用户明确选择", "以后再看"):
            self.assertIn(label, REVIEW_HTML)
        self.assertIn("评分与专业证据", REVIEW_HTML)
        self.assertIn("PERSONAL_POLICY_ACTIVE · 默认未勾选", REVIEW_HTML)
        self.assertNotIn("checked disabled", REVIEW_HTML)

    def test_preview_ui_reports_selection_progress_success_and_errors(self) -> None:
        self.assertIn("e.checked?selected.add(e.dataset.id):selected.delete(e.dataset.id)", REVIEW_HTML)
        self.assertIn("已勾选 ${selected.size}", REVIEW_HTML)
        self.assertIn("if(selected.size===0)", REVIEW_HTML)
        self.assertIn("请先勾选至少一张候选专辑", REVIEW_HTML)
        self.assertIn('class="preview-progress"', REVIEW_HTML)
        self.assertIn("<progress></progress>", REVIEW_HTML)
        self.assertIn("setPreviewBusy(true)", REVIEW_HTML)
        self.assertIn("previewButton.disabled=value", REVIEW_HTML)
        self.assertIn("applyButton.disabled=false", REVIEW_HTML)
        self.assertIn("api('/api/preview','POST'", REVIEW_HTML)
        self.assertIn("modalErrorEl.textContent=e instanceof Error?e.message:String(e)", REVIEW_HTML)
        for label in ("专辑", "文件", "空间", "隔离区"):
            self.assertIn(f"<b>{label}</b>", REVIEW_HTML)

    def test_incremental_library_enrichment_persists_attempts_without_media_scan(self) -> None:
        class FixtureSource:
            def lookup_album(self, local):
                return canonical()

            def fetch_rating(self, matched):
                return evidence("musicbrainz", 75)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            review = self._review(root, "Artist", "Album", None, "Popular/Rock/Folk")
            service = AlbumPruneService(root / "state")
            with ReviewStore(service.store_path) as store:
                store.save_reviews([review])
            with patch(
                "music_library_organizer.album_prune.service.MusicBrainzSource",
                return_value=FixtureSource(),
            ):
                result = service.enrich_existing_library("musicbrainz", offline=True)
            self.assertEqual(result["albums_processed"], 1)
            with ReviewStore(service.store_path) as store:
                saved = store.review(review.local.album_id)
                self.assertEqual(saved.music_score, 75)
                self.assertEqual(store.rating_attempt_counts()["musicbrainz"]["RATED"], 1)

    def test_incremental_enrichment_can_be_limited_to_language_route(self) -> None:
        class FixtureSource:
            def lookup_album(self, local):
                return canonical()

            def fetch_rating(self, matched):
                return evidence("musicbrainz", 75)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            japanese = self._review(root, "Artist JP", "Album JP", None, "Popular/Rock/Folk")
            japanese.local.language_bucket = "JA_CONFIRMED"
            japanese.local.rating_scope = "INCLUDE_NON_CHINESE"
            other = self._review(root, "Artist EN", "Album EN", None, "Popular/Rock/Folk")
            other.local.language_bucket = "NON_CJK"
            other.local.rating_scope = "INCLUDE_NON_CHINESE"
            service = AlbumPruneService(root / "state")
            with ReviewStore(service.store_path) as store:
                store.save_reviews([japanese, other])
            with patch(
                "music_library_organizer.album_prune.service.MusicBrainzSource",
                return_value=FixtureSource(),
            ):
                result = service.enrich_existing_library(
                    "musicbrainz",
                    language_statuses={"JA_CONFIRMED"},
                    categories={"Popular/Rock/Folk"},
                    offline=True,
                )
            self.assertEqual(result["albums_processed"], 1)
            self.assertEqual(result["language_statuses"], ["JA_CONFIRMED"])
            self.assertEqual(result["categories"], ["Popular/Rock/Folk"])
            with ReviewStore(service.store_path) as store:
                self.assertIsNotNone(store.review(japanese.local.album_id).music_score)
                self.assertIsNone(store.review(other.local.album_id).music_score)

    def test_unresolved_incremental_result_does_not_replace_existing_exact_match(self) -> None:
        class FixtureSource:
            def lookup_album(self, local):
                return replace(canonical(), match_status="NOT_FOUND", source_album_id="")

            def fetch_rating(self, matched):
                raise AssertionError("an unresolved entity must not be rated")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            review = self._review(root, "Artist", "Album", None, "Popular/Rock/Folk")
            review.canonical = replace(
                canonical(),
                source="discogs",
                canonical_album_id="discogs:master:1",
                source_album_id="1",
            )
            service = AlbumPruneService(root / "state")
            with ReviewStore(service.store_path) as store:
                store.save_reviews([review])
            with patch(
                "music_library_organizer.album_prune.service.MusicBrainzSource",
                return_value=FixtureSource(),
            ):
                service.enrich_existing_library("musicbrainz", offline=True)
            with ReviewStore(service.store_path) as store:
                saved = store.review(review.local.album_id)
                self.assertEqual(saved.canonical.source, "discogs")
                self.assertEqual(saved.canonical.match_status, "EXACT")

    def test_library_statistics_reports_exclusive_language_and_genre_groups(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = AlbumPruneService(root / "state")
            chinese = self._review(root, "A-Lin", "Album", None, "Popular/Rock/Folk")
            chinese.local.language_bucket = "ZH_CONFIRMED"
            classical = self._review(root, "Orchestra", "Symphony", None, "Classical")
            classical.local.language_bucket = "NON_CJK"
            with ReviewStore(service.store_path) as store:
                store.save_reviews([chinese, classical])
            groups = service.library_statistics()["coverage_by_category"]
            self.assertEqual(groups["chinese"]["albums"], 1)
            self.assertEqual(groups["classical"]["albums"], 1)
            self.assertEqual(sum(row["albums"] for row in groups.values()), 2)

    def test_beets_scope_import_excludes_latin_named_chinese_album(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "musicfinal"
            review = self._review(root, "A-Lin", "Album", 75, "Popular/Rock/Folk")
            service = AlbumPruneService(Path(temporary) / "state")
            with ReviewStore(service.store_path) as store:
                store.save_reviews([review])
            beets_db = Path(temporary) / "beets.db"
            connection = sqlite3.connect(beets_db)
            connection.execute(
                """CREATE TABLE items (
                    path BLOB, language TEXT, country TEXT, script TEXT,
                    mb_albumid TEXT, mb_releasegroupid TEXT, barcode TEXT,
                    catalognum TEXT, discogs_albumid INTEGER, label TEXT,
                    albumartist_sort TEXT, acoustid_id TEXT, disctotal INTEGER, tracktotal INTEGER
                )"""
            )
            connection.execute(
                """INSERT INTO items VALUES (
                    ?, 'cmn', 'TW', 'Hant', 'release-id', 'group-id', 'barcode-id',
                    'catalog-id', 123, 'Label', 'A-Lin', 'acoustid', 1, 10
                )""",
                (str((root / "A-Lin/Album/01.flac").resolve()).encode(),),
            )
            connection.commit()
            connection.close()
            result = service.import_beets_rating_scope(beets_db, root)
            self.assertEqual(result["albums_matched"], 1)
            with ReviewStore(service.store_path) as store:
                saved = store.review(review.local.album_id)
                self.assertEqual(saved.local.rating_scope, "EXCLUDE_CHINESE")
                self.assertEqual(saved.local.release_group_mbid, "group-id")
                self.assertEqual(saved.local.release_mbid, "release-id")
                self.assertEqual(saved.local.discogs_id, "123")
                self.assertTrue(saved.local.acoustid_available)
                self.assertIsNone(saved.music_score)

    def test_apply_quarantines_and_rollback_restores(self) -> None:
        store, selection_id = self.ready_store()
        plan = create_delete_plan(store, selection_id, self.library, self.quarantine, self.state)
        batch = apply_delete_plan(store, plan, plan["confirmation_token"])
        self.assertEqual(batch["status"], "VERIFIED")
        self.assertFalse(self.album.exists())
        quarantined = Path(plan["quarantine_batch_root"]) / "Artist/Album/01.flac"
        self.assertEqual(quarantined.read_bytes(), b"one")
        batch = rollback_batch(store, plan["batch_id"], f"ROLLBACK:{plan['batch_id']}")
        self.assertEqual(batch["status"], "ROLLED_BACK")
        self.assertEqual((self.album / "01.flac").read_bytes(), b"one")
        store.close()

    def test_wrong_confirmation_changes_nothing(self) -> None:
        store, selection_id = self.ready_store()
        plan = create_delete_plan(store, selection_id, self.library, self.quarantine, self.state)
        with self.assertRaises(ValueError):
            apply_delete_plan(store, plan, "wrong")
        self.assertTrue((self.album / "01.flac").is_file())
        self.assertFalse(Path(plan["quarantine_batch_root"]).exists())
        store.close()

    def test_plan_drift_blocks_apply(self) -> None:
        store, selection_id = self.ready_store()
        plan = create_delete_plan(store, selection_id, self.library, self.quarantine, self.state)
        (self.album / "01.flac").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "drift"):
            apply_delete_plan(store, plan, plan["confirmation_token"])
        self.assertEqual((self.album / "01.flac").read_bytes(), b"changed")
        store.close()

    def test_tampered_delete_plan_is_rejected_before_moving_files(self) -> None:
        store, selection_id = self.ready_store()
        plan = create_delete_plan(store, selection_id, self.library, self.quarantine, self.state)
        plan["quarantine_batch_root"] = str(self.root / "unexpected")
        with self.assertRaisesRegex(ValueError, "integrity"):
            apply_delete_plan(store, plan, plan["confirmation_token"])
        self.assertTrue((self.album / "01.flac").is_file())
        self.assertFalse((self.root / "unexpected").exists())
        store.close()

    def test_capacity_failure_stops_before_quarantine_creation(self) -> None:
        store, selection_id = self.ready_store()
        plan = create_delete_plan(store, selection_id, self.library, self.quarantine, self.state)
        with patch(
            "music_library_organizer.album_prune.quarantine._preflight_capacity",
            side_effect=OSError("insufficient quarantine space"),
        ):
            with self.assertRaisesRegex(OSError, "insufficient"):
                apply_delete_plan(store, plan, plan["confirmation_token"])
        self.assertTrue((self.album / "01.flac").is_file())
        self.assertFalse(Path(plan["quarantine_batch_root"]).exists())
        self.assertEqual(store.batch(plan["batch_id"])["status"], "DELETE_PLAN_READY")
        store.close()

    def test_interrupted_apply_can_be_recovered_from_persisted_journal(self) -> None:
        store, selection_id = self.ready_store()
        plan = create_delete_plan(store, selection_id, self.library, self.quarantine, self.state)
        batch = store.batch(plan["batch_id"])
        quarantine_batch = Path(plan["quarantine_batch_root"])
        moved_target = quarantine_batch / "Artist/Album/01.flac"
        moved_target.parent.mkdir(parents=True)
        (self.album / "01.flac").replace(moved_target)
        rows = []
        for file_row in plan["albums"][0]["files"]:
            rows.append(
                {
                    "album_id": plan["albums"][0]["album_id"],
                    "source": str(self.album / file_row["relative"]),
                    "target": str(quarantine_batch / "Artist/Album" / file_row["relative"]),
                    "bytes": file_row["bytes"],
                    "mtime_ns": file_row["mtime_ns"],
                    "sha256": file_row["sha256"],
                }
            )
        journal = Path(batch["state_dir"]) / "execution_journal.json"
        journal.write_text(
            __import__("json").dumps({"batch_id": plan["batch_id"], "status": "APPLYING", "files": rows}),
            encoding="utf-8",
        )
        batch["status"] = "APPLYING"
        store.save_batch(batch)
        recovered = recover_interrupted_batch(store, plan["batch_id"], f"RECOVER:{plan['batch_id']}")
        self.assertEqual(recovered["status"], "RECOVERED_ROLLED_BACK")
        self.assertEqual((self.album / "01.flac").read_bytes(), b"one")
        self.assertEqual((self.album / "cover.jpg").read_bytes(), b"cover")
        self.assertFalse(quarantine_batch.exists())
        store.close()

    def test_rollback_conflict_stops_before_restore(self) -> None:
        store, selection_id = self.ready_store()
        plan = create_delete_plan(store, selection_id, self.library, self.quarantine, self.state)
        apply_delete_plan(store, plan, plan["confirmation_token"])
        self.album.mkdir(parents=True)
        (self.album / "01.flac").write_bytes(b"new")
        with self.assertRaises(FileExistsError):
            rollback_batch(store, plan["batch_id"], f"ROLLBACK:{plan['batch_id']}")
        self.assertEqual((self.album / "01.flac").read_bytes(), b"new")
        store.close()

    def test_purge_requires_independent_confirmation(self) -> None:
        store, selection_id = self.ready_store()
        plan = create_delete_plan(store, selection_id, self.library, self.quarantine, self.state)
        apply_delete_plan(store, plan, plan["confirmation_token"])
        with self.assertRaises(ValueError):
            purge_batch(store, plan["batch_id"], "wrong")
        self.assertTrue(Path(plan["quarantine_batch_root"]).exists())
        batch = purge_batch(store, plan["batch_id"], f"PURGE:{plan['batch_id']}")
        self.assertEqual(batch["status"], "PURGED")
        self.assertFalse(Path(plan["quarantine_batch_root"]).exists())
        store.close()

    def test_purge_uses_signed_plan_path_not_mutable_database_path(self) -> None:
        store, selection_id = self.ready_store()
        plan = create_delete_plan(store, selection_id, self.library, self.quarantine, self.state)
        apply_delete_plan(store, plan, plan["confirmation_token"])
        external = self.root / "must-not-delete"
        external.mkdir()
        (external / "sentinel").write_text("safe", encoding="utf-8")
        batch = store.batch(plan["batch_id"])
        batch["quarantine_batch_root"] = str(external)
        store.save_batch(batch)
        purge_batch(store, plan["batch_id"], f"PURGE:{plan['batch_id']}")
        self.assertEqual((external / "sentinel").read_text(encoding="utf-8"), "safe")
        self.assertFalse(Path(plan["quarantine_batch_root"]).exists())
        store.close()

    def test_protected_album_cannot_be_selected(self) -> None:
        store, selection_id = self.ready_store()
        album_id = store.selection(selection_id)["album_ids"][0]
        store.protect(album_id, "favorite")
        with self.assertRaisesRegex(ValueError, "not selectable"):
            store.create_selection([album_id])
        store.close()


if __name__ == "__main__":
    unittest.main()
