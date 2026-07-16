from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from music_library_organizer.applier import apply, load_plan
from music_library_organizer.cli import main
from music_library_organizer.errors import OrganizerError
from music_library_organizer.media import read_metadata, sha256
from music_library_organizer.planner import create_plan, write_json

FFMPEG = shutil.which("ffmpeg")
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415408d763f8cfc0000003010100c9fe92ef0000000049454e44ae426082"
)


@unittest.skipUnless(FFMPEG, "ffmpeg is required to generate synthetic test media")
class OrganizerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.library = self.root / "library"
        self.library.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def media(self, name: str) -> Path:
        path = self.library / name
        codec = {".mp3": "libmp3lame", ".flac": "flac", ".m4a": "aac"}[path.suffix]
        command = [
            FFMPEG, "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono",
            "-t", "0.15", "-c:a", codec, str(path),
        ]
        subprocess.run(
            command,
            check=True,
        )
        return path

    def override(self, source: str, **fields: str) -> Path:
        path = self.root / "metadata.json"
        path.write_text(json.dumps({source: fields}), encoding="utf-8")
        return path

    def test_empty_and_missing_directories(self) -> None:
        self.assertEqual(create_plan(self.library)["items"], [])
        with self.assertRaisesRegex(OrganizerError, "not a directory"):
            create_plan(self.root / "missing")

    def test_unicode_metadata_and_filename(self) -> None:
        self.media("测试 音频.mp3")
        overrides = self.override("测试 音频.mp3", artist="示例 艺术家", album="专辑", title="Hello 世界")
        plan = create_plan(self.library, overrides)
        self.assertEqual(plan["items"][0]["target"], "示例 艺术家/专辑/00 - Hello 世界.mp3")

    def test_unsupported_file_is_ignored(self) -> None:
        (self.library / "notes.txt").write_text("not media", encoding="utf-8")
        report = create_plan(self.library)
        self.assertEqual(report["items"], [])
        self.assertEqual(report["skipped"], [])

    def test_scan_and_plan_supported_formats(self) -> None:
        for name in ("one.mp3", "two.flac", "three.m4a"):
            self.media(name)
        plan = create_plan(self.library)
        self.assertEqual(len(plan["items"]), 3)
        self.assertTrue(all("source_sha256" in item for item in plan["items"]))

    def test_json_overrides_and_deterministic_layout(self) -> None:
        self.media("song.mp3")
        overrides = self.override("song.mp3", artist="Artist", album="Album", title="Title", tracknumber="3/9")
        first = create_plan(self.library, overrides)
        second = create_plan(self.library, overrides)
        self.assertEqual(first, second)
        self.assertEqual(first["items"][0]["target"], "Artist/Album/03 - Title.mp3")

    def test_csv_overrides(self) -> None:
        self.media("song.flac")
        csv_path = self.root / "metadata.csv"
        csv_path.write_text("source,artist,album,title,tracknumber\nsong.flac,A,B,C,2\n", encoding="utf-8")
        plan = create_plan(self.library, csv_path)
        self.assertEqual(plan["items"][0]["target"], "A/B/02 - C.flac")

    def test_generated_target_collision_is_rejected(self) -> None:
        self.media("one.mp3")
        self.media("two.mp3")
        path = self.root / "metadata.json"
        fields = {"artist": "A", "album": "B", "title": "Same", "tracknumber": "1"}
        path.write_text(json.dumps({"one.mp3": fields, "two.mp3": fields}), encoding="utf-8")
        with self.assertRaisesRegex(OrganizerError, "target collision"):
            create_plan(self.library, path)

    def test_apply_defaults_to_dry_run(self) -> None:
        self.media("song.mp3")
        destination = self.root / "organized"
        report = apply(create_plan(self.library), destination)
        self.assertEqual(report["status"], "dry-run")
        self.assertFalse(destination.exists())

    def test_execute_copies_without_changing_source(self) -> None:
        source = self.media("song.mp3")
        before = sha256(source)
        overrides = self.override("song.mp3", artist="A", album="B", title="C", tracknumber="1")
        destination = self.root / "organized"
        report = apply(create_plan(self.library, overrides), destination, execute=True)
        target = destination / "A/B/01 - C.mp3"
        self.assertEqual(report["status"], "applied")
        self.assertTrue(target.is_file())
        self.assertEqual(sha256(source), before)
        self.assertEqual(read_metadata(target)["title"], "C")

    def test_conflict_fails_before_writing(self) -> None:
        self.media("one.mp3")
        self.media("two.mp3")
        plan = create_plan(self.library)
        destination = self.root / "organized"
        target = destination.joinpath(*Path(plan["items"][1]["target"]).parts)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"existing")
        with self.assertRaises(OrganizerError):
            apply(plan, destination, execute=True)
        first = destination.joinpath(*Path(plan["items"][0]["target"]).parts)
        self.assertFalse(first.exists())

    def test_changed_source_is_rejected(self) -> None:
        source = self.media("song.mp3")
        plan = create_plan(self.library)
        source.write_bytes(source.read_bytes() + b"changed")
        with self.assertRaisesRegex(OrganizerError, "source changed"):
            apply(plan, self.root / "organized", execute=True)

    def test_path_traversal_is_rejected(self) -> None:
        self.media("song.mp3")
        plan = create_plan(self.library)
        plan["items"][0]["target"] = "../escape.mp3"
        with self.assertRaisesRegex(OrganizerError, "unsafe target"):
            apply(plan, self.root / "organized", execute=True)

    def test_tampered_serialized_plan_is_rejected(self) -> None:
        self.media("song.mp3")
        plan_path = self.root / "plan.json"
        write_json(create_plan(self.library), plan_path)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["items"][0]["target"] = "Changed/target.mp3"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(OrganizerError, "integrity"):
            load_plan(plan_path)

    def test_symlink_destination_is_rejected(self) -> None:
        self.media("song.mp3")
        actual = self.root / "actual"
        actual.mkdir()
        link = self.root / "destination"
        os.symlink(actual, link)
        with self.assertRaisesRegex(OrganizerError, "symlink"):
            apply(create_plan(self.library), link, execute=True)

    def test_symlink_source_is_skipped(self) -> None:
        source = self.media("song.mp3")
        os.symlink(source, self.library / "link.mp3")
        plan = create_plan(self.library)
        self.assertEqual(len(plan["items"]), 1)
        self.assertEqual(plan["skipped"][0]["source"], "link.mp3")

    def test_mid_apply_failure_rolls_back_created_files(self) -> None:
        self.media("one.mp3")
        self.media("two.mp3")
        plan = create_plan(self.library)
        destination = self.root / "organized"
        with patch("music_library_organizer.applier.write_metadata", side_effect=[None, OrganizerError("injected")]):
            with self.assertRaisesRegex(OrganizerError, "injected"):
                apply(plan, destination, execute=True)
        self.assertEqual(list(destination.rglob("*.mp3")), [])

    def test_cover_embed_and_extract_all_formats(self) -> None:
        cover = self.root / "cover.png"
        cover.write_bytes(PNG)
        for name in ("one.mp3", "two.flac", "three.m4a"):
            self.media(name)
        plan_path = self.root / "plan.json"
        write_json(create_plan(self.library), plan_path)
        destination = self.root / "organized"
        apply(load_plan(plan_path), destination, execute=True, cover=cover)
        for target in destination.rglob("*.*"):
            output = self.root / f"extracted-{target.suffix[1:]}.png"
            status = main(["artwork", str(target), "--output", str(output)])
            self.assertEqual(status, 0)
            self.assertEqual(output.read_bytes(), PNG)

    def test_cli_plan_and_apply_smoke(self) -> None:
        self.media("song.m4a")
        plan = self.root / "plan.json"
        self.assertEqual(main(["plan", str(self.library), "--output", str(plan)]), 0)
        destination = self.root / "organized"
        self.assertEqual(main(["apply", str(plan), "--destination", str(destination)]), 0)
        self.assertFalse(destination.exists())
        self.assertEqual(main(["apply", str(plan), "--destination", str(destination), "--execute"]), 0)
        self.assertEqual(len(list(destination.rglob("*.m4a"))), 1)

    def test_cli_error_exit_and_json_scan(self) -> None:
        self.media("song.mp3")
        report = self.root / "scan.json"
        self.assertEqual(main(["scan", str(self.library), "--output", str(report)]), 0)
        self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["schema_version"], 1)
        self.assertEqual(main(["scan", str(self.root / "missing")]), 2)


if __name__ == "__main__":
    unittest.main()
