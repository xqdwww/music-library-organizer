from __future__ import annotations

import json
import plistlib
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from music_library_organizer.album_prune.curator import (
    DUPLICATE_VALUE,
    KEEP,
    LOW_PERSONAL_VALUE,
    PROTECTED_COLLECTION,
    build_curator_report,
)
from music_library_organizer.album_prune.curator_webui import HTML, CuratorHandler
from music_library_organizer.album_prune.models import AlbumReview, CanonicalAlbum, LocalAlbum, ProfessionalEvidence
from music_library_organizer.album_prune.personal_signals import (
    PersonalSignal,
    match_apple_signals,
    match_netease_signals,
)
from music_library_organizer.album_prune.service import AlbumPruneService
from music_library_organizer.album_prune.store import ReviewStore


def local_album(
    album_id: str,
    artist: str = "Artist",
    album: str = "Album",
    *,
    tracks: int = 2,
    formats: list[str] | None = None,
    category: str = "Other",
    album_type: str = "Album",
) -> LocalAlbum:
    return LocalAlbum(
        album_id=album_id,
        path=f"/library/{artist}/{album}",
        relative_path=f"{artist}/{album}",
        artist=artist,
        album=album,
        year=2001,
        track_count=tracks,
        file_count=tracks,
        size_bytes=tracks * 1000,
        formats=formats or ["mp3"],
        fingerprint=f"fingerprint-{album_id}",
        category=category,
        album_type=album_type,
        language_bucket="NON_CJK",
    )


def canonical(group_id: str) -> CanonicalAlbum:
    return CanonicalAlbum(
        canonical_album_id=f"musicbrainz:release-group:{group_id}",
        source="musicbrainz",
        source_album_id=group_id,
        source_album_url=f"https://musicbrainz.org/release-group/{group_id}",
        artist="Artist",
        album="Album",
        year=2001,
        primary_type="Album",
        secondary_types=[],
        match_status="EXACT",
        match_basis="fixture",
        release_group_id=group_id,
    )


class PersonalSignalImportTests(unittest.TestCase):
    def test_apple_xml_parses_usage_rating_favorite_and_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "Library.xml"
            payload = {
                "Tracks": {
                    "1": {
                        "Track ID": 1,
                        "Artist": "Artist",
                        "Album": "Album",
                        "Play Count": 7,
                        "Play Date UTC": datetime(2026, 7, 1),
                        "Rating": 80,
                        "Loved": True,
                        "Date Added": datetime(2018, 1, 1),
                    },
                    "2": {
                        "Track ID": 2,
                        "Album Artist": "Artist",
                        "Album": "Album",
                        "Play Count": 3,
                    },
                },
                "Playlists": [{"Name": "Favourites", "Playlist Items": [{"Track ID": 1}]}],
            }
            with source.open("wb") as stream:
                plistlib.dump(payload, stream)
            review = AlbumReview(local=local_album("one"), canonical=None)
            signals, summary = match_apple_signals([review], source)
        signal = signals["one"]
        self.assertEqual(summary["source"], "APPLE_MUSIC_XML")
        self.assertTrue(signal.observed)
        self.assertEqual(signal.play_count, 10)
        self.assertEqual(signal.rating, 80)
        self.assertTrue(signal.favorite)
        self.assertEqual(signal.playlist_count, 1)
        self.assertEqual(signal.match_rate, 1)

    def test_private_musiclibrary_is_not_claimed_as_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "Music Library.musiclibrary"
            source.mkdir()
            with self.assertRaisesRegex(ValueError, "private musicdb"):
                match_apple_signals([], source)

    def test_missing_apple_data_is_neutral_not_zero_play(self) -> None:
        review = AlbumReview(local=local_album("one"), canonical=None, music_score=30)
        report = build_curator_report([review], generated_at=datetime(2026, 7, 19, tzinfo=UTC))
        row = report["albums"][0]
        self.assertFalse(row["personal_signal"]["observed"])
        self.assertEqual(row["personal_usage_score"], 50)
        self.assertNotEqual(row["recommendation"], LOW_PERSONAL_VALUE)

    def test_netease_requires_two_thirds_album_track_coverage(self) -> None:
        review = AlbumReview(local=local_album("one", tracks=12), canonical=None)
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "netease.json"
            source.write_text(json.dumps({"albums": [{
                "artist": "Artist",
                "album": "Album",
                "album_id": 7,
                "matched_song_count": 7,
                "source_song_count": 12,
                "score": 8.8,
                "comment_count": 100,
            }]}), encoding="utf-8")
            signals, _ = match_netease_signals([review], source)
            self.assertFalse(signals["one"].accepted)
            payload = json.loads(source.read_text())
            payload["albums"][0]["matched_song_count"] = 8
            source.write_text(json.dumps(payload), encoding="utf-8")
            signals, _ = match_netease_signals([review], source)
        self.assertTrue(signals["one"].accepted)
        self.assertEqual(signals["one"].score, 88)


class PersonalCuratorModelTests(unittest.TestCase):
    def test_observed_old_zero_play_album_can_be_low_personal_value(self) -> None:
        review = AlbumReview(local=local_album("one"), canonical=None)
        signal = PersonalSignal(
            source="APPLE_MUSIC_XML",
            observed=True,
            match_confidence=1,
            match_rate=1,
            matched_track_count=2,
            source_track_count=2,
            local_track_count=2,
            play_count=0,
            library_added_date="2010-01-01T00:00:00+00:00",
        )
        report = build_curator_report(
            [review], {"one": signal}, generated_at=datetime(2026, 7, 19, tzinfo=UTC)
        )
        self.assertEqual(report["albums"][0]["recommendation"], LOW_PERSONAL_VALUE)
        self.assertFalse(report["albums"][0]["checked"])

    def test_recent_high_usage_album_is_kept(self) -> None:
        review = AlbumReview(local=local_album("one"), canonical=None)
        signal = PersonalSignal(
            source="APPLE_MUSIC_XML",
            observed=True,
            match_confidence=1,
            play_count=120,
            last_played_at="2026-07-01T00:00:00+00:00",
            favorite=True,
            playlist_count=2,
        )
        row = build_curator_report(
            [review], {"one": signal}, generated_at=datetime(2026, 7, 19, tzinfo=UTC)
        )["albums"][0]
        self.assertEqual(row["recommendation"], KEEP)

    def test_professional_award_protects_classical_recording(self) -> None:
        local = local_album("classical", category="Classical")
        local.classical_identity = {"conductor": "Conductor", "orchestra": "Orchestra"}
        professional = ProfessionalEvidence(
            publication="Gramophone",
            publication_type="critic",
            source_title="Recording",
            source_url="https://example.test/review",
            source_entity_id="recording",
            evidence_type="award",
            award="Record of the Year",
            match_confidence=0.95,
        )
        review = AlbumReview(local=local, canonical=None, professional_evidence=[professional])
        row = build_curator_report([review])["albums"][0]
        self.assertEqual(row["recommendation"], PROTECTED_COLLECTION)
        self.assertTrue(row["collector_protected"])

    def test_jazz_identity_is_visible_without_guessing_an_award(self) -> None:
        local = local_album("jazz", category="Jazz")
        local.jazz_identity = {"leader": "Leader", "recording_date": "1959", "label": "Label"}
        row = build_curator_report([AlbumReview(local=local, canonical=None)])["albums"][0]
        self.assertIn("identified jazz session/release context", row["collector_protection_reason"])
        self.assertFalse(row["collector_protected"])

    def test_duplicate_group_keeps_one_preferred_lossless_release(self) -> None:
        standard = AlbumReview(
            local=local_album("standard", album="Album", formats=["mp3"], tracks=10),
            canonical=canonical("same"),
        )
        deluxe = AlbumReview(
            local=local_album("deluxe", album="Album Deluxe Edition", formats=["flac"], tracks=14),
            canonical=canonical("same"),
        )
        rows = {row["album_id"]: row for row in build_curator_report([standard, deluxe])["albums"]}
        self.assertEqual(rows["standard"]["recommendation"], DUPLICATE_VALUE)
        self.assertTrue(rows["deluxe"]["preferred_release_candidate"])
        self.assertEqual(rows["standard"]["duplicate_group_id"], rows["deluxe"]["duplicate_group_id"])

    def test_box_set_discs_and_bonus_subdirectories_are_not_duplicates(self) -> None:
        disc_one = local_album("disc-one", album="Complete Recordings (disc 1)")
        disc_one.relative_path = "Artist/Complete Recordings (disc 1) [2008]"
        disc_two = local_album("disc-two", album="Complete Recordings (disc 2)")
        disc_two.relative_path = "Artist/Complete Recordings (disc 2) [2008]"
        parent = local_album("parent", album="Box Set", tracks=20)
        parent.relative_path = "Artist/Box Set"
        bonus = local_album("bonus", album="Box Set", tracks=2)
        bonus.relative_path = "Artist/Box Set/Bonus"
        reviews = [
            AlbumReview(local=disc_one, canonical=canonical("complete")),
            AlbumReview(local=disc_two, canonical=canonical("complete")),
            AlbumReview(local=parent, canonical=canonical("box")),
            AlbumReview(local=bonus, canonical=canonical("box")),
        ]
        for episode in range(1, 5):
            local = local_album(f"episode-{episode}", album="Series BGM")
            local.relative_path = f"Soundtrack/E{episode:02d}"
            reviews.append(AlbumReview(local=local, canonical=None))
        rows = build_curator_report(reviews)["albums"]
        self.assertTrue(all(row["duplicate_group_id"] is None for row in rows))

    def test_unsafe_flat_container_is_not_selected_by_storage_review(self) -> None:
        unsafe = local_album("singles", artist="Various", album="Singles")
        unsafe.safe_directory = False
        unsafe.size_bytes = 10_000_000
        safe = local_album("safe", artist="Artist", album="Large Album")
        safe.size_bytes = 1_000_000
        rows = {
            row["album_id"]: row
            for row in build_curator_report([
                AlbumReview(local=unsafe, canonical=None),
                AlbumReview(local=safe, canonical=None),
            ])["albums"]
        }
        self.assertEqual(rows["singles"]["recommendation"], KEEP)


class CuratorSafetyAndWebTests(unittest.TestCase):
    def test_full_library_analysis_does_not_change_media(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "library/Artist/Album"
            library.mkdir(parents=True)
            media = library / "01.flac"
            media.write_bytes(b"fixture-audio")
            before = (media.read_bytes(), media.stat().st_size, media.stat().st_mtime_ns)
            service = AlbumPruneService(root / "state")
            report = service.analyze_curator(library_root=root / "library")
            after = (media.read_bytes(), media.stat().st_size, media.stat().st_mtime_ns)
            self.assertEqual(before, after)
            self.assertEqual(report["inputs"]["library"]["media_writes"], 0)
            self.assertEqual(report["summary"]["albums"], 1)
            self.assertFalse((root / "quarantine").exists())

    def test_curator_store_and_ui_are_read_only(self) -> None:
        self.assertIn("Personal Library Curator", HTML)
        self.assertIn("0 播放", HTML)
        self.assertIn("collector_protection_reason", HTML)
        self.assertNotIn("/api/apply", HTML)
        with tempfile.TemporaryDirectory() as temporary:
            service = AlbumPruneService(Path(temporary) / "state")
            report = build_curator_report([AlbumReview(local=local_album("one"), canonical=None)])
            report["run_id"] = "cur_fixture"
            with ReviewStore(service.store_path) as store:
                store.save_curator_report("cur_fixture", report)
            self.assertEqual(service.curator_report()["run_id"], "cur_fixture")
            handler = object.__new__(CuratorHandler)
            responses: list[tuple[object, int]] = []
            handler._json = lambda value, status=200: responses.append((value, status))  # type: ignore[method-assign]
            CuratorHandler.do_POST(handler)
            self.assertEqual(responses, [({"error": "Personal Curator is read-only"}, 405)])


if __name__ == "__main__":
    unittest.main()
