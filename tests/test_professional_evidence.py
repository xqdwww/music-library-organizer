from __future__ import annotations

import unittest
from pathlib import Path

from music_library_organizer.album_prune.calibration_webui import HTML
from music_library_organizer.album_prune.entity_resolution import language_route, query_variants
from music_library_organizer.album_prune.models import (
    MATCH_EXACT,
    AlbumReview,
    CanonicalAlbum,
    LocalAlbum,
    ProfessionalEvidence,
    RatingEvidence,
)
from music_library_organizer.album_prune.professional import (
    AwardRecord,
    OfficialAwardsSource,
    deduplicate_professional_evidence,
    parse_golden_indie_awards,
    professional_summary,
)
from music_library_organizer.album_prune.ratings import CachedResponse, MusicBrainzSource
from music_library_organizer.album_prune.scoring import ScoringConfig, classify
from music_library_organizer.album_prune.service import AlbumPruneService

FIXTURES = Path(__file__).parent / "fixtures"


def local(artist: str, album: str, year: int | None = None, category: str = "Other") -> LocalAlbum:
    return LocalAlbum(
        album_id=f"local:{artist}:{album}",
        path=f"/library/{artist}/{album}",
        relative_path=f"{artist}/{album}",
        artist=artist,
        album=album,
        year=year,
        track_count=10,
        file_count=10,
        size_bytes=100,
        formats=["flac"],
        fingerprint="fixture",
        category=category,
        album_type="Album",
    )


def canonical(row: LocalAlbum) -> CanonicalAlbum:
    return CanonicalAlbum(
        canonical_album_id="musicbrainz:release-group:fixture",
        source="musicbrainz",
        source_album_id="fixture",
        source_album_url="https://musicbrainz.org/release-group/fixture",
        artist=row.artist,
        album=row.album,
        year=row.year,
        primary_type="Album",
        secondary_types=[],
        match_status=MATCH_EXACT,
        match_basis="fixture exact identity",
        release_group_id="fixture",
    )


def community(score: float) -> RatingEvidence:
    return RatingEvidence(
        source="discogs",
        source_album_id="fixture",
        source_album_url="https://example.test/community",
        raw_score=score / 20,
        raw_scale=5,
        normalized_score_100=score,
        rating_count=100,
        review_count=None,
        critic_or_community="community",
        matched_artist="Artist",
        matched_album="Album",
        matched_year=2024,
        match_basis="fixture",
        fetched_at="2026-07-18T00:00:00+00:00",
        response_cache_path="fixture",
        adapter_version="fixture-v1",
    )


class FakeCache:
    def __init__(self, body: dict):
        self.body = body

    def fetch_json(self, *_: object, **__: object) -> CachedResponse:
        return CachedResponse(self.body, Path("fixture.json"), "2026-07-18T00:00:00+00:00", False)


class CjkResolutionExpansionTests(unittest.TestCase):
    def test_album_translation_does_not_turn_latin_artist_into_unknown_cjk(self) -> None:
        row = local("David Bowie", "1993年日本版 Black Tie White Noise")
        route = language_route(row)
        self.assertEqual(route["language_status"], "NON_CJK")
        self.assertIn("decision_trace", route)
        self.assertEqual(route["resolver_source"], "multi_evidence_cjk_v4")

    def test_hong_kong_and_japanese_catalog_evidence_are_explicit(self) -> None:
        cantonese = local("陳奕迅", "U87")
        cantonese.release_countries = ["HK"]
        japanese = local("久石譲", "Piano Stories")
        japanese.catalog_number = "TKCA-72401"
        japanese.label = "Victor Entertainment Japan"
        self.assertEqual(language_route(cantonese)["language_status"], "HK_TW_CANTONESE")
        self.assertEqual(language_route(japanese)["language_status"], "JA_CONFIRMED")

    def test_musicbrainz_exact_alias_area_enrichment_routes_japanese(self) -> None:
        row = local("久石譲", "Piano Stories")
        payload = {
            "artists": [{
                "id": "fixture-artist",
                "name": "久石譲",
                "sort-name": "Hisaishi, Joe",
                "country": "JP",
                "area": {"name": "Japan"},
                "aliases": [{"name": "Joe Hisaishi", "locale": "en"}, {"name": "久石譲", "locale": "ja"}],
            }]
        }
        source = MusicBrainzSource(FakeCache(payload), "test (contact: test@example.com)")
        trace = source.resolve_artist_metadata(row)
        self.assertEqual(trace["selected_candidate"], "fixture-artist")
        self.assertIn("Joe Hisaishi", row.artist_aliases)
        self.assertEqual(language_route(row)["language_status"], "JA_CONFIRMED")
        self.assertTrue(any(item["artist"] == "Joe Hisaishi" for item in query_variants(row)))


class ProfessionalEvidenceTests(unittest.TestCase):
    def test_open_dataset_shape_parses_only_album_level_awards(self) -> None:
        csv_rows = parse_golden_indie_awards(
            (FIXTURES / "golden-indie-minimal.csv").read_text(encoding="utf-8")
        )
        self.assertEqual({row.album for row in csv_rows}, {"圍庄 2CD", "自己的房間"})

    def test_taiwan_official_award_adds_region_evidence_without_becoming_popularity(self) -> None:
        row = local("草東沒有派對", "醜奴兒", 2016)
        award = ProfessionalEvidence(
            publication="Golden Indie Music Awards",
            publication_type="official_music_award",
            source_title="最佳樂團獎",
            source_url="https://data.gov.tw/en/datasets/58040",
            source_entity_id="golden-indie:fixture",
            evidence_type="AWARD_WINNER",
        )
        changed = AlbumPruneService._apply_professional_scope_metadata(row, [award])
        self.assertTrue(changed)
        self.assertEqual(row.release_countries, ["TW"])
        self.assertEqual(language_route(row)["language_status"], "ZH_CONFIRMED")

    def test_strict_award_matching_rejects_wrong_artist_and_year(self) -> None:
        source = OfficialAwardsSource.__new__(OfficialAwardsSource)
        source.records = [AwardRecord(
            "Fixture Award",
            2025,
            "Best Jazz Instrumental Album",
            "Chick Corea, Béla Fleck",
            "Remembrance",
            "https://example.test/awards/jazz/2025",
            "fixture-jazz:2025:remembrance",
        )]
        source.fetched_at = "2026-07-18T00:00:00+00:00"
        source.stale = False
        right = local("Chick Corea & Béla Fleck", "Remembrance", 2024, "Jazz")
        wrong_artist = local("Different Leader", "Remembrance", 2024, "Jazz")
        wrong_year = local("Chick Corea & Béla Fleck", "Remembrance", 1990, "Jazz")
        self.assertEqual(len(source.evidence_for(right, canonical(right))), 1)
        self.assertEqual(source.evidence_for(wrong_artist, canonical(wrong_artist)), [])
        self.assertEqual(source.evidence_for(wrong_year, canonical(wrong_year)), [])
        missing_year = local("Chick Corea & Béla Fleck", "Remembrance", None, "Jazz")
        no_year_canonical = canonical(missing_year)
        no_year_canonical.year = None
        self.assertEqual(source.evidence_for(missing_year, no_year_canonical), [])

    def test_classical_canonical_original_year_matches_reissue_directory(self) -> None:
        source = OfficialAwardsSource.__new__(OfficialAwardsSource)
        source.records = [AwardRecord(
            "Fixture Award",
            1988,
            "Classical Album",
            "Vladimir Horowitz",
            "Horowitz in Moscow",
            "https://example.test/awards/classical/1988",
            "fixture-classical:1988:horowitz-in-moscow",
        )]
        source.fetched_at = "2026-07-18T00:00:00+00:00"
        source.stale = False
        row = local("Vladimir Horowitz", "Horowitz in Moscow", 2016, "Classical")
        match = canonical(row)
        match.year = 1986
        evidence = source.evidence_for(row, match)
        self.assertEqual(len(evidence), 1)
        self.assertIn("release/award year window", evidence[0].match_features)

    def test_historic_recording_does_not_cross_to_different_live_title(self) -> None:
        source = OfficialAwardsSource.__new__(OfficialAwardsSource)
        source.records = [AwardRecord(
            "Fixture Archive",
            1961,
            "Historic Recordings, inducted 1998",
            "Bill Evans Trio",
            "Waltz for Debby",
            "https://example.test/archive/historic-recordings",
            "fixture-historic:1961:bill-evans-trio:waltz-for-debby",
            "HISTORIC_RECORDING",
            "HISTORIC_RECORDING",
        )]
        source.fetched_at = "2026-07-18T00:00:00+00:00"
        source.stale = False
        different_live = local(
            "Bill Evans Trio",
            "Waltz for Debby: Live in Copenhagen 1969",
            1969,
            "Jazz",
        )
        different_live.live_studio = "live"
        self.assertEqual(source.evidence_for(different_live, canonical(different_live)), [])

    def test_historic_recording_reissue_requires_controlled_original_year(self) -> None:
        source = OfficialAwardsSource.__new__(OfficialAwardsSource)
        source.records = [AwardRecord(
            "Fixture Archive",
            1961,
            "Historic Recordings, inducted 1998",
            "Bill Evans Trio",
            "Waltz for Debby",
            "https://example.test/archive/historic-recordings",
            "fixture-historic:1961:bill-evans-trio:waltz-for-debby",
            "HISTORIC_RECORDING",
            "HISTORIC_RECORDING",
        )]
        source.fetched_at = "2026-07-18T00:00:00+00:00"
        source.stale = False
        reissue = local("Bill Evans Trio", "Waltz for Debby", 2011, "Jazz")
        unresolved = canonical(reissue)
        unresolved.match_status = "NOT_FOUND"
        unresolved.year = 2011
        self.assertEqual(source.evidence_for(reissue, unresolved), [])
        release_group = canonical(reissue)
        release_group.year = 1961
        evidence = source.evidence_for(reissue, release_group)
        self.assertEqual(len(evidence), 1)
        self.assertIn("release/award year window", evidence[0].match_features)

    def test_professional_award_is_transparent_and_protects_low_community_score(self) -> None:
        row = local("Chick Corea & Béla Fleck", "Remembrance", 2024, "Jazz")
        award = ProfessionalEvidence(
            publication="Fixture Award",
            publication_type="official_music_award",
            source_title="2025 Best Jazz Instrumental Album: Remembrance",
            source_url="https://example.test/awards/jazz/2025",
            source_entity_id="fixture-jazz:2025:remembrance",
            evidence_type="AWARD_WINNER",
            award="Best Jazz Instrumental Album",
            recording_identity="musicbrainz:release-group:fixture",
            match_features=["canonical album title", "artist/performer identity", "release/award year window"],
            match_confidence=0.98,
            normalized_score_100=95,
            conversion_rule="official album-category winner -> 95; award retained as raw evidence",
            protection_reason="AWARD_WINNER",
        )
        review = classify(
            AlbumReview(
                local=row,
                canonical=canonical(row),
                evidence=[community(40)],
                professional_evidence=[award],
            ),
            ScoringConfig(),
        )
        self.assertEqual(review.community_score, 40)
        self.assertEqual(review.professional_score, 95)
        self.assertEqual(review.rating_status, "SOURCE_CONFLICT")
        self.assertEqual(review.candidate_status, "PROFESSIONAL_PROTECTED")
        self.assertIn("AWARD_WINNER", review.protection_reasons)

    def test_duplicate_professional_rows_do_not_double_count(self) -> None:
        item = ProfessionalEvidence(
            publication="Fixture Award",
            publication_type="official_music_award",
            source_title="Award",
            source_url="https://example.test/award",
            source_entity_id="same",
            evidence_type="AWARD_WINNER",
            recording_identity="recording:one",
            match_confidence=0.9,
        )
        self.assertEqual(len(deduplicate_professional_evidence([item, item])), 1)

    def test_professional_confidence_uses_match_quality_and_source_independence(self) -> None:
        first = ProfessionalEvidence(
            publication="Source A",
            publication_type="official_music_award",
            source_title="Award A",
            source_url="https://example.test/a",
            source_entity_id="a",
            evidence_type="AWARD_WINNER",
            recording_identity="recording:one",
            match_confidence=0.8,
            normalized_score_100=90,
        )
        second = ProfessionalEvidence(
            publication="Source B",
            publication_type="official_music_award",
            source_title="Award B",
            source_url="https://example.test/b",
            source_entity_id="b",
            evidence_type="AWARD_WINNER",
            recording_identity="recording:one",
            match_confidence=0.8,
            normalized_score_100=90,
        )
        self.assertEqual(professional_summary([first])["professional_confidence"], 0.6)
        self.assertEqual(professional_summary([first, second])["professional_confidence"], 0.68)
        first.normalized_score_100 = 80
        second.normalized_score_100 = 100
        weighted = professional_summary(
            [first, second],
            {"source a": 3.0, "source b": 1.0},
        )
        self.assertEqual(weighted["professional_score"], 85)

    def test_ui_exposes_score_groups_raw_awards_and_non_destructive_boundary(self) -> None:
        for marker in (
            "community_score",
            "critic_score",
            "professional_score",
            "conversion_rule",
            "专业保护理由",
            "没有找到已验证专业评价；这不等于评价较低",
        ):
            self.assertIn(marker, HTML)
        self.assertNotIn("/api/calibration/apply", HTML)
        self.assertNotIn("/api/calibration/purge", HTML)


if __name__ == "__main__":
    unittest.main()
