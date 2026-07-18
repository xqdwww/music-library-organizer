from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .store import ReviewStore

BATCH_ID = re.compile(r"^prune_\d{8}T\d{6}Z_[0-9a-f]{8}$")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _validate_plan_integrity(plan: dict[str, Any]) -> None:
    claimed = plan.get("plan_sha256")
    unsigned = {key: value for key, value in plan.items() if key not in {"plan_sha256", "state_dir"}}
    if not isinstance(claimed, str) or hashlib.sha256(_canonical(unsigned)).hexdigest() != claimed:
        raise ValueError("delete plan integrity check failed")


def _trusted_batch_plan(store: ReviewStore, batch: dict[str, Any]) -> dict[str, Any]:
    batch_id = str(batch.get("batch_id", ""))
    if not BATCH_ID.fullmatch(batch_id):
        raise ValueError("invalid cleanup batch ID")
    expected_state = (store.path.parent / "batches" / batch_id).resolve(strict=False)
    state_dir = Path(str(batch.get("state_dir", ""))).expanduser()
    if state_dir.is_symlink() or state_dir.resolve(strict=False) != expected_state:
        raise ValueError("cleanup batch state path is unsafe")
    plan = load_delete_plan(expected_state / "delete_plan.json")
    if plan.get("batch_id") != batch_id or plan.get("selection_id") != batch.get("selection_id"):
        raise ValueError("cleanup batch does not match its signed plan")
    quarantine_root = Path(str(plan.get("quarantine_root", ""))).expanduser().resolve(strict=False)
    expected_quarantine = quarantine_root / batch_id
    actual_quarantine = Path(str(plan.get("quarantine_batch_root", ""))).expanduser().resolve(strict=False)
    if actual_quarantine != expected_quarantine:
        raise ValueError("cleanup batch quarantine path is unsafe")
    return plan


def load_batch_plan(store: ReviewStore, batch_id: str) -> dict[str, Any]:
    return _trusted_batch_plan(store, store.batch(batch_id))


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe relative path: {value}")
    return path


def _nearest_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise ValueError(f"no existing parent for path: {path}")
        current = parent
    if current.is_symlink() or not current.is_dir():
        raise ValueError(f"unsafe destination parent: {current}")
    return current


def _files(directory: Path) -> list[Path]:
    result: list[Path] = []
    for root, dirs, files in os.walk(directory, followlinks=False):
        for name in dirs:
            if (Path(root) / name).is_symlink():
                raise ValueError(f"album contains a symlink: {Path(root) / name}")
        for name in files:
            path = Path(root) / name
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"album contains a non-regular file: {path}")
            result.append(path)
    return sorted(result)


def create_delete_plan(
    store: ReviewStore,
    selection_id: str,
    library_root: Path,
    quarantine_root: Path,
    state_root: Path,
) -> dict[str, Any]:
    selection = store.selection(selection_id)
    if selection["status"] != "USER_SELECTED":
        raise ValueError("selection is not ready")
    library_root = library_root.expanduser().resolve()
    quarantine_root = quarantine_root.expanduser().resolve()
    if not library_root.is_dir() or library_root.is_symlink():
        raise ValueError("library root is unavailable")
    if (
        quarantine_root == library_root
        or quarantine_root in library_root.parents
        or library_root in quarantine_root.parents
    ):
        raise ValueError("library and quarantine roots cannot contain one another")
    batch_id = "prune_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ_") + secrets.token_hex(4)
    albums: list[dict[str, Any]] = []
    seen_paths: list[Path] = []
    for album_id in selection["album_ids"]:
        review = store.review(album_id)
        source = Path(review.local.path).resolve()
        try:
            source.relative_to(library_root)
        except ValueError as exc:
            raise ValueError(f"album is outside library root: {source}") from exc
        if source.is_symlink() or not source.is_dir() or not review.local.safe_directory:
            raise ValueError(f"unsafe album directory: {source}")
        if any(source in other.parents or other in source.parents for other in seen_paths):
            raise ValueError("selected album directories overlap")
        seen_paths.append(source)
        files = _files(source)
        file_rows = []
        for path in files:
            stat = path.stat()
            file_rows.append(
                {
                    "relative": path.relative_to(source).as_posix(),
                    "bytes": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "sha256": _sha256(path),
                }
            )
        current_audio_fingerprint = hashlib.sha256(
            "\n".join(
                sorted(
                    f"{row['relative']}\0{row['bytes']}\0{row['mtime_ns']}"
                    for row in file_rows
                    if Path(row["relative"]).suffix.lower()
                    in {
                        ".aac",
                        ".aif",
                        ".aiff",
                        ".alac",
                        ".ape",
                        ".dsf",
                        ".flac",
                        ".m4a",
                        ".mp3",
                        ".ogg",
                        ".opus",
                        ".wav",
                        ".wma",
                    }
                )
            ).encode()
        ).hexdigest()
        if current_audio_fingerprint != selection["fingerprints"][album_id]:
            raise ValueError(f"selection is stale; album changed after scan: {album_id}")
        canonical_id = review.canonical.canonical_album_id if review.canonical else review.local.album_id
        albums.append(
            {
                "album_id": album_id,
                "canonical_album_id": canonical_id,
                "artist": review.local.artist,
                "album": review.local.album,
                "source_path": str(source),
                "source_relative": source.relative_to(library_root).as_posix(),
                "quarantine_relative": source.relative_to(library_root).as_posix(),
                "music_score": review.music_score,
                "candidate_status": review.candidate_status,
                "rating_sources": [
                    {"source": item.source, "score": item.normalized_score_100, "url": item.source_album_url}
                    for item in review.evidence
                ],
                "track_count": review.local.track_count,
                "file_count": len(file_rows),
                "size_bytes": sum(item["bytes"] for item in file_rows),
                "formats": review.local.formats,
                "multiple_local_versions": review.local.duplicate_local_versions > 1,
                "files": file_rows,
            }
        )
    token = secrets.token_urlsafe(18)
    unsigned = {
        "schema_version": 1,
        "batch_id": batch_id,
        "selection_id": selection_id,
        "status": "DELETE_PLAN_READY",
        "created_at": _now(),
        "library_root": str(library_root),
        "quarantine_root": str(quarantine_root),
        "quarantine_batch_root": str(quarantine_root / batch_id),
        "album_count": len(albums),
        "track_count": sum(album["track_count"] for album in albums),
        "file_count": sum(album["file_count"] for album in albums),
        "size_bytes": sum(album["size_bytes"] for album in albums),
        "confirmation_token": token,
        "warnings": {
            "playlist_links_may_break": True,
            "cross_filesystem": (
                library_root.stat().st_dev != quarantine_root.stat().st_dev if quarantine_root.exists() else "UNKNOWN"
            ),
            "symlinks_present": False,
        },
        "albums": albums,
    }
    unsigned["plan_sha256"] = hashlib.sha256(_canonical(unsigned)).hexdigest()
    batch_dir = state_root / "batches" / batch_id
    _write_json(batch_dir / "selection.json", selection)
    _write_json(batch_dir / "delete_plan.json", unsigned)
    batch = dict(unsigned)
    batch["state_dir"] = str(batch_dir)
    store.save_batch(batch)
    return batch


def load_delete_plan(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("delete plan is unavailable or unsafe")
    plan = json.loads(path.read_text(encoding="utf-8"))
    _validate_plan_integrity(plan)
    return plan


def _relocate(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"target exists: {target}")
    if source.stat().st_dev == target.parent.stat().st_dev:
        os.link(source, target, follow_symlinks=False)
        try:
            source.unlink()
        except Exception:
            if source.exists():
                target.unlink(missing_ok=True)
            raise
        return
    temporary = target.with_name(f".{target.name}.partial-{secrets.token_hex(4)}")
    try:
        shutil.copy2(source, temporary)
        if _sha256(temporary) != _sha256(source):
            raise OSError(f"cross-filesystem verification failed: {source}")
        os.link(temporary, target, follow_symlinks=False)
        try:
            source.unlink()
        except Exception:
            if source.exists():
                target.unlink(missing_ok=True)
            raise
    finally:
        temporary.unlink(missing_ok=True)


def _remove_empty_tree(root: Path) -> None:
    for directory, _, _ in os.walk(root, topdown=False):
        path = Path(directory)
        try:
            path.rmdir()
        except OSError:
            pass


def _planned_files(plan: dict[str, Any]) -> list[dict[str, Any]]:
    quarantine_batch = Path(plan["quarantine_batch_root"])
    files: list[dict[str, Any]] = []
    for album in plan["albums"]:
        source_root = Path(album["source_path"])
        target_root = quarantine_batch.joinpath(*_safe_relative(album["quarantine_relative"]).parts)
        for row in album["files"]:
            relative = _safe_relative(row["relative"])
            files.append(
                {
                    "album_id": album["album_id"],
                    "source": str(source_root.joinpath(*relative.parts)),
                    "target": str(target_root.joinpath(*relative.parts)),
                    "bytes": row["bytes"],
                    "mtime_ns": row["mtime_ns"],
                    "sha256": row["sha256"],
                }
            )
    return files


def _preflight_capacity(plan: dict[str, Any], files: list[dict[str, Any]]) -> None:
    destination_parent = _nearest_existing_parent(Path(plan["quarantine_batch_root"]))
    destination_device = destination_parent.stat().st_dev
    copy_bytes = sum(int(row["bytes"]) for row in files if Path(row["source"]).stat().st_dev != destination_device)
    if not copy_bytes:
        return
    margin = max(64 * 1024 * 1024, (copy_bytes + 19) // 20)
    free = shutil.disk_usage(destination_parent).free
    if free < copy_bytes + margin:
        raise OSError(
            f"insufficient quarantine space: need {copy_bytes + margin} bytes "
            f"({copy_bytes} data + {margin} safety margin), have {free}"
        )


def apply_delete_plan(store: ReviewStore, plan: dict[str, Any], confirmation: str) -> dict[str, Any]:
    _validate_plan_integrity(plan)
    if plan.get("status") != "DELETE_PLAN_READY" or confirmation != plan.get("confirmation_token"):
        raise ValueError("explicit delete-plan confirmation token is required")
    batch = store.batch(plan["batch_id"])
    if batch["status"] != "DELETE_PLAN_READY":
        raise ValueError(f"batch cannot be applied from status {batch['status']}")
    quarantine_batch = Path(plan["quarantine_batch_root"])
    if quarantine_batch.exists():
        raise FileExistsError("quarantine batch directory already exists")
    planned = _planned_files(plan)
    moved: list[tuple[Path, Path, dict[str, Any], str]] = []
    try:
        # Full preflight prevents a late conflict after earlier files have moved.
        for row in planned:
            source, target = Path(row["source"]), Path(row["target"])
            if source.is_symlink() or not source.is_file() or target.exists() or target.is_symlink():
                raise ValueError(f"plan drift or target conflict: {source}")
            stat = source.stat()
            if stat.st_size != row["bytes"] or stat.st_mtime_ns != row["mtime_ns"] or _sha256(source) != row["sha256"]:
                raise ValueError(f"plan drift detected: {source}")
        _preflight_capacity(plan, planned)
        state_dir = Path(batch["state_dir"])
        journal = {"batch_id": plan["batch_id"], "status": "APPLYING", "files": planned}
        _write_json(state_dir / "execution_journal.json", journal)
        batch.update({"status": "APPLYING", "applying_at": _now()})
        store.save_batch(batch)
        quarantine_batch.mkdir(parents=True)
        for row in planned:
            source, target = Path(row["source"]), Path(row["target"])
            _relocate(source, target)
            moved.append((source, target, row, row["album_id"]))
        for album in plan["albums"]:
            _remove_empty_tree(Path(album["source_path"]))
    except Exception:
        for source, target, _, _ in reversed(moved):
            if target.exists() and not source.exists():
                _relocate(target, source)
        _remove_empty_tree(quarantine_batch)
        if batch.get("status") == "APPLYING":
            batch.update({"status": "DELETE_PLAN_READY", "last_apply_error_at": _now()})
            store.save_batch(batch)
        raise
    verification = {
        "batch_id": plan["batch_id"],
        "status": "VERIFIED",
        "verified_at": _now(),
        "files": [
            {
                "album_id": album_id,
                "original_path": str(source),
                "quarantine_path": str(target),
                "bytes": row["bytes"],
                "sha256": row["sha256"],
                "verified": target.is_file()
                and target.stat().st_size == row["bytes"]
                and _sha256(target) == row["sha256"],
            }
            for source, target, row, album_id in moved
        ],
    }
    if not all(row["verified"] for row in verification["files"]):
        raise OSError("post-move verification failed; quarantine retained for inspection")
    state_dir = Path(batch["state_dir"])
    move_manifest = {"batch_id": plan["batch_id"], "status": "QUARANTINED", "files": verification["files"]}
    rollback = {
        "batch_id": plan["batch_id"],
        "files": [
            {
                "source": row["quarantine_path"],
                "target": row["original_path"],
                "sha256": row["sha256"],
                "bytes": row["bytes"],
            }
            for row in verification["files"]
        ],
    }
    _write_json(state_dir / "move_manifest.json", move_manifest)
    _write_json(state_dir / "verification.json", verification)
    _write_json(state_dir / "rollback_manifest.json", rollback)
    report = (
        f"# Album pruning batch {plan['batch_id']}\n\n"
        f"- Status: VERIFIED\n- Albums: {plan['album_count']}\n- Files: {plan['file_count']}\n"
        f"- Bytes: {plan['size_bytes']}\n- Quarantine: `{quarantine_batch}`\n"
    )
    (state_dir / "final_report.md").write_text(report, encoding="utf-8")
    batch.update({"status": "VERIFIED", "verified_at": verification["verified_at"]})
    store.save_batch(batch)
    return batch


def recover_interrupted_batch(store: ReviewStore, batch_id: str, confirmation: str) -> dict[str, Any]:
    batch = store.batch(batch_id)
    if batch["status"] != "APPLYING" or confirmation != f"RECOVER:{batch_id}":
        raise ValueError("recovery requires an APPLYING batch and exact RECOVER:<batch_id> confirmation")
    plan = _trusted_batch_plan(store, batch)
    journal_path = Path(batch["state_dir"]) / "execution_journal.json"
    if journal_path.is_symlink() or not journal_path.is_file():
        raise ValueError("execution journal is unavailable or unsafe")
    moved: list[tuple[Path, Path]] = []
    for row in _planned_files(plan):
        source, target = Path(row["source"]), Path(row["target"])
        source_exists, target_exists = source.is_file(), target.is_file()
        if source_exists == target_exists:
            raise ValueError(f"interrupted batch has ambiguous file state: {source}")
        existing = source if source_exists else target
        if existing.stat().st_size != row["bytes"] or _sha256(existing) != row["sha256"]:
            raise ValueError(f"interrupted batch file failed integrity check: {existing}")
        if target_exists:
            moved.append((target, source))
    restored: list[tuple[Path, Path]] = []
    try:
        for source, target in reversed(moved):
            _relocate(source, target)
            restored.append((source, target))
    except Exception:
        for source, target in reversed(restored):
            if target.exists() and not source.exists():
                _relocate(target, source)
        raise
    _remove_empty_tree(Path(batch["quarantine_batch_root"]))
    batch.update({"status": "RECOVERED_ROLLED_BACK", "recovered_at": _now()})
    store.save_batch(batch)
    return batch


def rollback_batch(store: ReviewStore, batch_id: str, confirmation: str) -> dict[str, Any]:
    batch = store.batch(batch_id)
    if batch["status"] != "VERIFIED" or confirmation != f"ROLLBACK:{batch_id}":
        raise ValueError("rollback requires a VERIFIED batch and exact ROLLBACK:<batch_id> confirmation")
    plan = _trusted_batch_plan(store, batch)
    rows = _planned_files(plan)
    for row in rows:
        source, target = Path(row["target"]), Path(row["source"])
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"rollback target exists; nothing was changed: {target}")
        if not source.is_file() or source.stat().st_size != row["bytes"] or _sha256(source) != row["sha256"]:
            raise ValueError(f"quarantined file changed: {source}")
    restored: list[tuple[Path, Path]] = []
    try:
        for row in rows:
            source, target = Path(row["target"]), Path(row["source"])
            _relocate(source, target)
            restored.append((source, target))
    except Exception:
        for source, target in reversed(restored):
            if target.exists() and not source.exists():
                _relocate(target, source)
        raise
    _remove_empty_tree(Path(batch["quarantine_batch_root"]))
    batch.update({"status": "ROLLED_BACK", "rolled_back_at": _now()})
    store.save_batch(batch)
    return batch


def purge_batch(store: ReviewStore, batch_id: str, confirmation: str) -> dict[str, Any]:
    batch = store.batch(batch_id)
    if batch["status"] != "VERIFIED" or confirmation != f"PURGE:{batch_id}":
        raise ValueError("purge requires a VERIFIED batch and exact PURGE:<batch_id> confirmation")
    plan = _trusted_batch_plan(store, batch)
    quarantine = Path(plan["quarantine_batch_root"])
    if not quarantine.is_dir() or quarantine.is_symlink():
        raise ValueError("quarantine batch is unavailable or unsafe")
    shutil.rmtree(quarantine)
    batch.update({"status": "PURGED", "purged_at": _now()})
    store.save_batch(batch)
    return batch
