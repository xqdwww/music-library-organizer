from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from music_library_organizer.album_prune.audit import classify_unresolved_root_cause, match_invariant_violation
from music_library_organizer.album_prune.entity_resolution import (
    build_classical_identity,
    build_jazz_identity,
    language_route,
    query_variants,
    same_classical_recording,
    same_jazz_session,
)
from music_library_organizer.album_prune.models import AlbumReview, CanonicalAlbum, LocalAlbum
from music_library_organizer.album_prune.normalize import canonical_identity, normalization_trace
from music_library_organizer.album_prune.ratings import (
    CachedResponse,
    CritiqueBrainzSource,
    DiscogsSource,
    HttpCache,
    MusicBrainzSource,
)
from music_library_organizer.album_prune.service import AlbumPruneService
from music_library_organizer.album_prune.store import ReviewStore


class FakeCache:
    def __init__(self, payloads: list[dict]):
        self.payloads = iter(payloads)

    def fetch_json(self, *args: object, **kwargs: object) -> CachedResponse:
        return CachedResponse(next(self.payloads), Path("fixture.json"), "2026-07-18T00:00:00+00:00", False)


def local(artist: str = "Artist", album: str = "Album", year: int | None = 2001) -> LocalAlbum:
    return LocalAlbum(
        album_id=f"local:{artist}:{album}",
        path=f"/library/{artist}/{album}",
        relative_path=f"{artist}/{album}",
        artist=artist,
        album=album,
        year=year,
        track_count=10,
        file_count=11,
        size_bytes=100,
        formats=["flac"],
        fingerprint="fixture",
    )


def canonical() -> CanonicalAlbum:
    return CanonicalAlbum(
        canonical_album_id="musicbrainz:release-group:rg1",
        source="musicbrainz",
        source_album_id="rg1",
        source_album_url="https://musicbrainz.org/release-group/rg1",
        artist="Artist",
        album="Album",
        year=2001,
        primary_type="Album",
        secondary_types=[],
        match_status="EXACT",
        match_basis="fixture",
        release_group_id="rg1",
    )


class EntityResolutionTests(unittest.TestCase):
    def test_unresolved_root_cause_classification(self) -> None:
        row = local(year=None)
        self.assertEqual(
            classify_unresolved_root_cause(AlbumReview(local=row, canonical=None), "offline cache miss"),
            "YEAR_MISMATCH",
        )
        row.category = "Classical"
        self.assertEqual(
            classify_unresolved_root_cause(AlbumReview(local=row, canonical=None), None),
            "CLASSICAL_ENTITY_MODEL_MISMATCH",
        )

    def test_match_invariant_rejects_wrong_artist_and_accepts_identifier_match(self) -> None:
        review = AlbumReview(local=local(), canonical=canonical())
        self.assertIsNone(match_invariant_violation(review))
        review.canonical = replace(canonical(), artist="Wrong Artist")
        self.assertEqual(match_invariant_violation(review), "artist_album_identity_mismatch")
        review.canonical = replace(
            canonical(),
            artist="Different Credit",
            match_basis="embedded release-group MBID",
        )
        self.assertIsNone(match_invariant_violation(review))

    def test_unicode_edition_disc_normalization_is_traced(self) -> None:
        trace = normalization_trace("Ａｌｂｕｍ（Super Deluxe） CD 2", strip_edition=True, strip_disc=True)
        self.assertEqual(trace["normalized_value"], "album")
        self.assertIn("edition_suffix", trace["normalization_steps"])
        self.assertIn("disc_suffix", trace["normalization_steps"])
        self.assertEqual(canonical_identity("The Artist", "Album Remastered 2011"), ("artist", "album"))

    def test_cjk_language_routing_has_seven_states(self) -> None:
        chinese = local("王菲", "唱游")
        chinese.languages = ["cmn"]
        japanese = local("宇多田ヒカル", "初恋")
        korean = local("이소라", "눈썹달")
        mixed = local("宇多田ヒカル", "初恋")
        mixed.languages = ["cmn"]
        unknown = local("漢字藝術家", "漢字專輯")
        cantonese = local("陳奕迅", "U87")
        cantonese.release_countries = ["HK"]
        non_cjk = local()
        self.assertEqual(language_route(chinese)["bucket"], "ZH_CONFIRMED")
        self.assertEqual(language_route(japanese)["bucket"], "JA_CONFIRMED")
        self.assertEqual(language_route(korean)["bucket"], "KO_CONFIRMED")
        self.assertEqual(language_route(mixed)["bucket"], "MIXED_CJK")
        self.assertEqual(language_route(unknown)["bucket"], "UNKNOWN_CJK")
        self.assertEqual(language_route(cantonese)["bucket"], "HK_TW_CANTONESE")
        self.assertEqual(language_route(non_cjk)["bucket"], "NON_CJK")

    def test_translated_alias_and_release_language_do_not_reclassify_specialty_artist(self) -> None:
        row = local("Vladimir Horowitz", "Horowitz in Moscow")
        row.category = "Classical"
        row.languages = ["ja"]
        row.artist_aliases = ["ウラディミール・ホロヴィッツ"]
        row.artist_countries = ["US"]
        route = language_route(row)
        self.assertEqual(route["bucket"], "NON_CJK")
        self.assertIn(
            "Japanese release language ignored for Latin-script specialty artist",
            route["language_evidence"],
        )

    def test_authoritative_japanese_artist_country_routes_romanized_artist(self) -> None:
        row = local("Ryuichi Sakamoto", "async")
        row.artist_countries = ["JP"]
        self.assertEqual(language_route(row)["bucket"], "JA_CONFIRMED")

    def test_original_and_romanized_alias_candidates_are_preserved(self) -> None:
        row = local("宇多田ヒカル", "初恋")
        row.artist_aliases = ["Hikaru Utada"]
        variants = query_variants(row)
        self.assertEqual([item["artist"] for item in variants[:2]], ["宇多田ヒカル", "Hikaru Utada"])
        self.assertEqual(variants[0]["artist_trace"]["original_value"], "宇多田ヒカル")

    def test_musicbrainz_romanized_alias_can_be_accepted(self) -> None:
        row = local("宇多田ヒカル", "初恋", 2018)
        row.artist_aliases = ["Hikaru Utada"]
        search = {"release-groups": [{
            "id": "rg-alias",
            "title": "初恋",
            "first-release-date": "2018",
            "artist-credit": [{"name": "Hikaru Utada"}],
        }]}
        lookup = search["release-groups"][0]
        source = MusicBrainzSource(FakeCache([search, lookup]), "test (contact: test@example.com)")
        matched = source.lookup_album(row)
        self.assertEqual(matched.match_status, "EXACT")
        self.assertEqual(matched.source_album_id, "rg-alias")

    def test_musicbrainz_reissue_release_resolves_original_release_group(self) -> None:
        row = local("The Bill Evans Trio", "Waltz for Debby", 2011)
        row.category = "Jazz"
        old_group_search = {
            "release-groups": [{
                "id": "group-original",
                "title": "Waltz for Debby",
                "first-release-date": "1962",
                "artist-credit": [{"name": "Bill Evans Trio"}],
            }]
        }
        release_search = {
            "releases": [{
                "id": "release-2011",
                "title": "Waltz for Debby",
                "date": "2011",
                "artist-credit": [
                    {"name": "Bill Evans Trio", "joinphrase": " with "},
                    {"name": "Scott LaFaro", "joinphrase": ", "},
                    {"name": "Paul Motian"},
                ],
                "release-group": {"id": "group-original"},
            }]
        }
        group_lookup = {
            "id": "group-original",
            "title": "Waltz for Debby",
            "first-release-date": "1962",
            "primary-type": "Album",
            "secondary-types": ["Live"],
            "artist-credit": [{"name": "Bill Evans Trio"}],
        }
        source = MusicBrainzSource(
            FakeCache([old_group_search, release_search, group_lookup]),
            "test (contact: test@example.com)",
        )
        matched = source.lookup_album(row)
        self.assertEqual(matched.release_group_id, "group-original")
        self.assertEqual(matched.year, 1962)
        self.assertEqual(matched.match_status, "CANONICALIZED")
        self.assertEqual(matched.relation_type, "reissue-release-to-group")

    def test_discogs_romanized_alias_can_be_accepted(self) -> None:
        row = local("宇多田ヒカル", "初恋", 2018)
        row.artist_aliases = ["Hikaru Utada"]
        candidates = DiscogsSource._discogs_candidates(
            row,
            [{"id": 1, "title": "Hikaru Utada - 初恋", "year": "2018"}],
            "artist_alias",
        )
        self.assertEqual(candidates, [("1", "EXACT", "Hikaru Utada - 初恋")])

    def test_classical_different_conductors_do_not_merge(self) -> None:
        first = local("Orchestra", "Symphony No. 5")
        first.category = "Classical"
        first.work = "Beethoven: Symphony No. 5"
        first.conductor = "Carlos Kleiber"
        first.orchestra = "Vienna Philharmonic"
        first.original_release_year = 1975
        second = local("Orchestra", "Symphony No. 5")
        second.category = "Classical"
        second.work = first.work
        second.conductor = "Herbert von Karajan"
        second.orchestra = first.orchestra
        second.original_release_year = 1963
        self.assertFalse(same_classical_recording(build_classical_identity(first), build_classical_identity(second)))

    def test_classical_same_recording_different_release_relationship(self) -> None:
        first = local()
        first.category = "Classical"
        first.work = "work-id"
        first.conductor = "Conductor"
        first.release_group_mbid = "group-id"
        second = local()
        second.category = "Classical"
        second.work = "work-id"
        second.conductor = "Conductor"
        second.release_group_mbid = "group-id"
        second.edition = "Remastered"
        self.assertTrue(same_classical_recording(build_classical_identity(first), build_classical_identity(second)))

    def test_classical_boxset_identity_retains_release_and_edition(self) -> None:
        row = local(album="Complete Recordings")
        row.category = "Classical"
        row.release_mbid = "release-id"
        row.release_group_mbid = "group-id"
        row.edition = "Box Set"
        identity = build_classical_identity(row)
        self.assertEqual(identity["release_id"], "release-id")
        self.assertEqual(identity["edition"], "Box Set")

    def test_jazz_reissue_can_share_session_but_live_dates_cannot(self) -> None:
        original = local("Leader", "Session")
        original.category = "Jazz"
        original.leader = "Leader"
        original.recording_date = "1959-03-02"
        original.live_studio = "studio"
        reissue = local("Leader", "Session (Remastered)")
        reissue.category = "Jazz"
        reissue.leader = "Leader"
        reissue.recording_date = "1959-03-02"
        reissue.live_studio = "studio"
        self.assertTrue(same_jazz_session(build_jazz_identity(original), build_jazz_identity(reissue)))
        reissue.recording_date = "1960-04-09"
        reissue.live_studio = "live"
        self.assertFalse(same_jazz_session(build_jazz_identity(original), build_jazz_identity(reissue)))

    def test_musicbrainz_compilation_query_accepts_only_various_artists(self) -> None:
        row = local("Soundtrack", "Film Music")
        row.album_type = "Soundtrack"
        no_artist_match = {"release-groups": []}
        compilation = {
            "release-groups": [{
                "id": "rg1",
                "title": "Film Music",
                "first-release-date": "2001",
                "artist-credit": [{"name": "Various Artists"}],
            }]
        }
        lookup = {
            "id": "rg1",
            "title": "Film Music",
            "first-release-date": "2001",
            "artist-credit": [{"name": "Various Artists"}],
        }
        source = MusicBrainzSource(
            FakeCache([no_artist_match, compilation, lookup]),
            "test (contact: test@example.com)",
        )
        matched = source.lookup_album(row)
        self.assertEqual(matched.match_status, "CANONICALIZED")
        self.assertIn("title_only_compilation", matched.match_basis)

    def test_same_title_wrong_artist_and_wrong_year_are_rejected(self) -> None:
        row = local("Correct Artist", "Same Name", 2001)
        search = {"release-groups": [{
            "id": "wrong",
            "title": "Same Name",
            "first-release-date": "1990",
            "artist-credit": [{"name": "Other Artist"}],
        }]}
        source = MusicBrainzSource(
            FakeCache([search, {"releases": []}]),
            "test (contact: test@example.com)",
        )
        self.assertEqual(source.lookup_album(row).match_status, "NOT_FOUND")
        self.assertTrue(any(item["rejection_reason"] for item in source.last_trace))

    def test_critiquebrainz_fixture_uses_entity_rating_and_preserves_professional_evidence(self) -> None:
        payload = {
            "average_rating": {"rating": 4.0, "count": 3},
            "reviews": [{
                "id": "review-1",
                "source": "BBC",
                "source_url": "https://example.test/review",
                "rating": 4,
                "license": {"id": "CC BY-SA 3.0"},
                "user": {"display_name": "Reviewer"},
            }],
        }
        source = CritiqueBrainzSource(FakeCache([payload]), "test")
        rating = source.fetch_rating(canonical())
        self.assertEqual(rating.normalized_score_100, 80)
        professional = source.professional_evidence(canonical())
        self.assertEqual(professional[0]["publication"], "BBC")
        self.assertEqual(professional[0]["license"], "CC BY-SA 3.0")

    def test_source_failure_uses_stale_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = HttpCache(Path(temporary), refresh=True, ttl_days=1)
            url = "https://example.test/data"
            path = cache._path("fixture", url, "v1")
            path.parent.mkdir(parents=True)
            old = (datetime.now(UTC) - timedelta(days=30)).replace(microsecond=0).isoformat()
            path.write_text(json.dumps({"fetched_at": old, "response": {"ok": True}}), encoding="utf-8")
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
                with patch("music_library_organizer.album_prune.ratings.time.sleep"):
                    response = cache.fetch_json("fixture", url, "v1", {})
            self.assertTrue(response.stale)
            self.assertEqual(response.body, {"ok": True})

    def test_discogs_policy_discards_cache_older_than_six_hours(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = HttpCache(Path(temporary), offline=True, ttl_days=30)
            url = "https://api.discogs.com/releases/fixture"
            path = cache._path("discogs", url, "discogs-v2")
            path.parent.mkdir(parents=True)
            old = (datetime.now(UTC) - timedelta(hours=7)).replace(microsecond=0).isoformat()
            path.write_text(json.dumps({"fetched_at": old, "response": {"id": 1}}), encoding="utf-8")
            with self.assertRaisesRegex(LookupError, "offline mode"):
                cache.fetch_json(
                    "discogs",
                    url,
                    "discogs-v2",
                    {},
                    max_age_seconds=6 * 60 * 60,
                    discard_expired=True,
                )
            self.assertFalse(path.exists())

    def test_full_scan_does_not_query_unknown_cjk_album(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "musicfinal/王菲/浮躁"
            root.mkdir(parents=True)
            (root / "01.flac").write_bytes(b"audio")
            service = AlbumPruneService(Path(temporary) / "state")
            with patch("music_library_organizer.album_prune.service.MusicBrainzSource") as musicbrainz:
                with patch("music_library_organizer.album_prune.service.DiscogsSource") as discogs:
                    result = service.scan(Path(temporary) / "musicfinal")
            musicbrainz.return_value.lookup_album.assert_not_called()
            discogs.return_value.lookup_album.assert_not_called()
            self.assertEqual(result["albums_scanned"], 1)

    def test_full_scan_discogs_match_replaces_musicbrainz_not_found_placeholder(self) -> None:
        row = local()
        musicbrainz_not_found = replace(canonical(), match_status="NOT_FOUND", source_album_id="")
        discogs_match = replace(
            canonical(),
            source="discogs",
            canonical_album_id="discogs:master:1",
            source_album_id="1",
        )
        with tempfile.TemporaryDirectory() as temporary:
            service = AlbumPruneService(Path(temporary) / "state")
            with patch("music_library_organizer.album_prune.service.scan_albums", return_value=[row]):
                with patch("music_library_organizer.album_prune.service.MusicBrainzSource") as musicbrainz:
                    with patch("music_library_organizer.album_prune.service.DiscogsSource") as discogs:
                        musicbrainz.return_value.lookup_album.return_value = musicbrainz_not_found
                        musicbrainz.return_value.fetch_rating.return_value = None
                        discogs.return_value.lookup_album.return_value = discogs_match
                        discogs.return_value.fetch_rating.return_value = None
                        result = service.scan(Path(temporary))
            self.assertEqual(result["exact_matches"], 1)
            with ReviewStore(service.store_path) as store:
                self.assertEqual(store.list_reviews()[0].canonical.source, "discogs")

    def test_scan_error_output_is_bounded(self) -> None:
        rows = [local(album=f"Album {index}") for index in range(60)]
        with tempfile.TemporaryDirectory() as temporary:
            service = AlbumPruneService(Path(temporary) / "state")
            with patch("music_library_organizer.album_prune.service.scan_albums", return_value=rows):
                with patch("music_library_organizer.album_prune.service.MusicBrainzSource") as musicbrainz:
                    with patch("music_library_organizer.album_prune.service.DiscogsSource") as discogs:
                        musicbrainz.return_value.lookup_album.side_effect = LookupError("offline")
                        discogs.return_value.lookup_album.side_effect = LookupError("offline")
                        result = service.scan(Path(temporary))
            self.assertEqual(result["error_count"], 120)
            self.assertEqual(len(result["errors"]), 100)
            self.assertTrue(result["errors_truncated"])

    def test_golden_sample_has_required_coverage_and_schema(self) -> None:
        fixture = Path(__file__).parent / "fixtures/entity-resolution-golden.json"
        cases = json.loads(fixture.read_text(encoding="utf-8"))["cases"]
        self.assertEqual(len(cases), 100)
        categories = {row["category"] for row in cases}
        self.assertTrue({"popular", "classical", "jazz", "chinese", "japanese", "other_cjk"} <= categories)
        self.assertTrue(all(row["expected_entity"] in row["allowed_candidates"] for row in cases))
        self.assertTrue(all(row["expected_entity"] not in row["forbidden_candidates"] for row in cases))
        for case in cases:
            row = local(case["local_artist"], case["local_album"], case["year"])
            expected_bucket = case["expected_language_bucket"]
            if expected_bucket == "ZH_CONFIRMED":
                row.languages = ["cmn"]
            elif expected_bucket == "HK_TW_CANTONESE":
                row.release_countries = ["HK"]
            elif expected_bucket == "JA_CONFIRMED":
                row.languages = ["jpn"]
            elif expected_bucket == "KO_CONFIRMED":
                row.languages = ["kor"]
            self.assertEqual(language_route(row)["bucket"], expected_bucket, case["case_id"])
            if case["expected_match_status"] != "EXACT":
                self.assertEqual(expected_bucket, "UNKNOWN_CJK")
                continue
            expected = {
                "id": case["expected_entity"],
                "title": case["local_album"],
                "first-release-date": str(case["year"]),
                "artist-credit": [{"name": case["local_artist"]}],
            }
            forbidden = {
                "id": case["forbidden_candidates"][0],
                "title": case["local_album"],
                "first-release-date": str(case["year"]),
                "artist-credit": [{"name": "Wrong Artist"}],
            }
            source = MusicBrainzSource(FakeCache([expected]), "test (contact: test@example.com)")
            matched = source._select_group(row, [expected, forbidden], "fixture", "golden_fixture")
            self.assertEqual(matched.source_album_id, case["expected_entity"], case["case_id"])
            self.assertEqual(matched.match_status, case["expected_match_status"], case["case_id"])


if __name__ == "__main__":
    unittest.main()
