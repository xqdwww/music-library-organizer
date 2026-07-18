from __future__ import annotations

import json
import random
import re
import secrets
import sqlite3
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .entity_resolution import language_route
from .models import AlbumReview
from .scanner import AUDIO_EXTENSIONS, DISC_DIR
from .scoring import ScoringConfig, classify
from .store import ReviewStore

CALIBRATION_STRATEGY_VERSION = "stratified-v1"
THRESHOLDS = (45, 50, 55, 60, 65, 70)
DECISIONS = {"UNREVIEWED", "KEEP", "DELETE_CANDIDATE", "LATER"}
MATCH_FEEDBACK = {"CORRECT", "WRONG", "UNSURE"}
RATING_FEEDBACK = {"CORRECT", "WRONG", "INCOMPLETE", "UNSURE"}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _file_row(path: Path, root: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "inode": stat.st_ino,
        "device": stat.st_dev,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def create_read_only_baseline(root: Path, output: Path, seed: int = 20260718) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("library root must be a regular directory")
    audio: list[Path] = []
    sidecars: list[Path] = []
    total_bytes = 0
    latest_mtime_ns = 0
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        stat = path.stat()
        total_bytes += stat.st_size
        latest_mtime_ns = max(latest_mtime_ns, stat.st_mtime_ns)
        (audio if path.suffix.casefold() in AUDIO_EXTENSIONS else sidecars).append(path)
    rng = random.Random(seed)
    audio_sample = rng.sample(audio, min(100, len(audio)))
    sidecar_sample = rng.sample(sidecars, min(30, len(sidecars)))
    value = {
        "schema_version": 1,
        "created_at": utc_now(),
        "library_name": root.name,
        "root_device": root.stat().st_dev,
        "total_bytes": total_bytes,
        "audio_files": len(audio),
        "sidecar_files": len(sidecars),
        "latest_mtime_ns": latest_mtime_ns,
        "sample_seed": seed,
        "audio_sample": [_file_row(path, root) for path in sorted(audio_sample)],
        "sidecar_sample": [_file_row(path, root) for path in sorted(sidecar_sample)],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return value


def verify_read_only_baseline(root: Path, baseline_path: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current = create_read_only_baseline(root, baseline_path.with_suffix(".verification.tmp"), baseline["sample_seed"])
    baseline_path.with_suffix(".verification.tmp").unlink()
    sample_changes: list[str] = []
    for row in [*baseline["audio_sample"], *baseline["sidecar_sample"]]:
        path = root / row["relative_path"]
        if not path.is_file():
            sample_changes.append(row["relative_path"] + ":missing")
            continue
        current_row = _file_row(path, root)
        if any(current_row[key] != row[key] for key in ("inode", "device", "size", "mtime_ns")):
            sample_changes.append(row["relative_path"] + ":changed")
    counts_equal = all(
        current[key] == baseline[key]
        for key in ("total_bytes", "audio_files", "sidecar_files", "latest_mtime_ns", "root_device")
    )
    unchanged = counts_equal and not sample_changes
    return {
        "status": "READ_ONLY_VERIFIED" if unchanged else "LIBRARY_CHANGED",
        "real_music_files_modified": 0 if unchanged else "UNKNOWN",
        "real_music_files_moved": 0 if unchanged else "UNKNOWN",
        "real_music_files_deleted": 0 if unchanged else "UNKNOWN",
        "sampled_files_verified": len(baseline["audio_sample"]) + len(baseline["sidecar_sample"]),
        "sample_changes": sample_changes,
        "counts_equal": counts_equal,
        "current": {key: current[key] for key in ("total_bytes", "audio_files", "sidecar_files", "latest_mtime_ns")},
    }


def category(review: AlbumReview) -> str:
    return review.local.category if review.local.category in {"Popular/Rock/Folk", "Jazz", "Classical"} else "Other"


def classify_rating_scope(review: AlbumReview) -> tuple[str, str]:
    local = review.local
    route = language_route(local)
    local.language_bucket = str(route["bucket"])
    local.language_evidence = route
    if local.language_bucket in {"ZH_CONFIRMED", "HK_TW_CANTONESE"}:
        return "EXCLUDE_CHINESE", "confirmed Chinese language routing evidence"
    if local.category in {"Classical", "Jazz"}:
        return "INCLUDE_SPECIALTY", f"{local.category} calibration scope"
    if local.language_bucket in {"JA_CONFIRMED", "KO_CONFIRMED", "NON_CJK"}:
        return "INCLUDE_NON_CHINESE", f"{local.language_bucket} routing"
    if local.language_bucket in {"MIXED_CJK", "UNKNOWN_CJK"}:
        return "REVIEW_CJK_LANGUAGE", f"{local.language_bucket} requires routing review"
    return "INCLUDE_NON_CHINESE", "no Chinese-language signal"


def _stratum(review: AlbumReview) -> tuple[str, str]:
    if review.music_score is None:
        score_band = "unrated"
    elif review.music_score <= 50:
        score_band = "low"
    elif review.music_score <= 70:
        score_band = "medium"
    else:
        score_band = "high"
    feature = (
        "conflict" if review.rating_status == "SOURCE_CONFLICT" else
        "multi_version" if review.local.duplicate_local_versions > 1 else
        "match_review" if not review.canonical or review.canonical.match_status not in {"EXACT", "CANONICALIZED"} else
        "multi_source" if len(review.evidence) >= 2 else
        "single_source" if len(review.evidence) == 1 else "unrated"
    )
    return score_band, feature


def stratified_sample(reviews: Iterable[AlbumReview], size: int = 140, seed: int = 20260718) -> list[AlbumReview]:
    rows = [row for row in reviews if row.local.rating_scope.startswith("INCLUDE")]
    if not rows or size <= 0:
        return []
    rng = random.Random(seed)
    targets = {"Popular/Rock/Folk": 55, "Jazz": 35, "Classical": 35, "Other": 15}
    scale = min(1.0, size / sum(targets.values()))
    quotas = {key: max(1, round(value * scale)) for key, value in targets.items()}
    while sum(quotas.values()) > min(size, len(rows)):
        key = max(quotas, key=quotas.get)
        quotas[key] -= 1
    selected: list[AlbumReview] = []
    selected_ids: set[str] = set()
    for group_name, quota in quotas.items():
        pool = [row for row in rows if category(row) == group_name]
        buckets: dict[tuple[str, str], list[AlbumReview]] = {}
        for row in pool:
            buckets.setdefault(_stratum(row), []).append(row)
        for bucket in buckets.values():
            rng.shuffle(bucket)
        keys = sorted(buckets)
        while len([row for row in selected if category(row) == group_name]) < min(quota, len(pool)) and keys:
            for key in list(keys):
                if buckets[key]:
                    row = buckets[key].pop()
                    selected.append(row)
                    selected_ids.add(row.local.album_id)
                    if len([item for item in selected if category(item) == group_name]) >= min(quota, len(pool)):
                        break
                else:
                    keys.remove(key)
    remainder = [row for row in rows if row.local.album_id not in selected_ids]
    rng.shuffle(remainder)
    selected.extend(remainder[: max(0, min(size, len(rows)) - len(selected))])
    return selected


def create_calibration_batch(store: ReviewStore, state_root: Path, size: int, seed: int) -> dict[str, Any]:
    sample = stratified_sample(store.list_reviews(), size=size, seed=seed)
    batch_id = "cal_" + secrets.token_hex(8)
    value = {
        "calibration_batch_id": batch_id,
        "created_at": utc_now(),
        "strategy_version": CALIBRATION_STRATEGY_VERSION,
        "random_seed": seed,
        "requested_size": size,
        "album_ids": [row.local.album_id for row in sample],
        "album_paths": [row.local.relative_path for row in sample],
        "category_counts": dict(Counter(category(row) for row in sample)),
    }
    directory = state_root / "calibration" / batch_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "sample.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return value


def threshold_report(reviews: list[AlbumReview], feedback: dict[str, dict[str, Any]]) -> dict[str, Any]:
    labelled = {key: row for key, row in feedback.items() if row["user_decision"] != "UNREVIEWED"}
    delete_total = sum(row["user_decision"] == "DELETE_CANDIDATE" for row in labelled.values())
    rows = []
    for threshold in THRESHOLDS:
        candidates = [row for row in reviews if row.music_score is not None and row.music_score <= threshold]
        labels = [labelled[row.local.album_id]["user_decision"] for row in candidates if row.local.album_id in labelled]
        deletes = labels.count("DELETE_CANDIDATE")
        keeps = labels.count("KEEP")
        later = labels.count("LATER")
        denominator = len(labels)
        genre_keeps = Counter(
            category(row) for row in candidates
            if row.local.album_id in labelled and labelled[row.local.album_id]["user_decision"] == "KEEP"
        )
        genre_totals = Counter(
            category(row) for row in candidates
            if row.local.album_id in labelled
            and labelled[row.local.album_id]["user_decision"] in {"KEEP", "DELETE_CANDIDATE"}
        )
        rows.append({
            "threshold": threshold,
            "candidate_count": len(candidates),
            "delete_candidate_count": deletes,
            "keep_count": keeps,
            "later_count": later,
            "candidate_hit_rate": deletes / denominator if denominator else None,
            "false_positive_rate": keeps / denominator if denominator else None,
            "recall": deletes / delete_total if delete_total else None,
            "genre_distribution": dict(Counter(category(row) for row in candidates)),
            "classical_false_positive_rate": (
                genre_keeps["Classical"] / genre_totals["Classical"] if genre_totals["Classical"] else None
            ),
            "jazz_false_positive_rate": genre_keeps["Jazz"] / genre_totals["Jazz"] if genre_totals["Jazz"] else None,
            "popular_false_positive_rate": (
                genre_keeps["Popular/Rock/Folk"] / genre_totals["Popular/Rock/Folk"]
                if genre_totals["Popular/Rock/Folk"]
                else None
            ),
        })
    enough = len(labelled) >= 30 and delete_total >= 5
    return {
        "report_status": "CALIBRATION_ANALYSIS_READY" if enough else "INSUFFICIENT_CALIBRATION_LABELS",
        "labels_available": len(labelled),
        "recommendation_generated": False,
        "thresholds": rows,
    }


def policy_template(batch_id: str = "", reviewed_count: int = 0) -> str:
    return f'''version: 1

candidate_policy:
  music_score_threshold: null
  minimum_independent_sources: 2
  allow_single_critic_source: false
  source_conflict_excluded: true
  ambiguous_match_excluded: true

genre_policy:
  classical:
    enabled: true
    minimum_sources: 2
    require_manual_review: true
    professional_evidence_visible: true
  jazz:
    enabled: true
    minimum_sources: 2
    require_manual_review: true
    professional_evidence_visible: true
  other:
    require_manual_review: false

protection:
  protected_album_ids: []
  protected_labels: []
  protected_series: []
  protected_recordings: []

calibration:
  batch_id: "{batch_id}"
  reviewed_count: {reviewed_count}
  selected_threshold: null
'''


def import_beets_scope_metadata(
    store: ReviewStore,
    beets_database: Path,
    library_root: Path,
    scoring_config: ScoringConfig | None = None,
) -> dict[str, Any]:
    beets_database = beets_database.expanduser().resolve()
    if not beets_database.is_file():
        raise ValueError("beets database is unavailable")
    library_root = library_root.expanduser().resolve()
    connection = sqlite3.connect(f"file:{beets_database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    grouped: dict[str, dict[str, set[str]]] = {}
    tracks_matched = 0
    try:
        rows = connection.execute(
            """SELECT path, language, country, script, mb_albumid, mb_releasegroupid, barcode,
                      catalognum, discogs_albumid, label, albumartist_sort, acoustid_id,
                      disctotal, tracktotal
               FROM items"""
        )
        for row in rows:
            raw_path = row["path"]
            path_value = raw_path.decode("utf-8", "surrogateescape") if isinstance(raw_path, bytes) else str(raw_path)
            path = Path(path_value)
            try:
                path.relative_to(library_root)
            except ValueError:
                continue
            directory = path.parent
            if DISC_DIR.match(directory.name) and directory.parent != library_root:
                directory = directory.parent
            relative = directory.relative_to(library_root).as_posix()
            values = grouped.setdefault(relative, {
                "languages": set(),
                "countries": set(),
                "scripts": set(),
                "release_mbids": set(),
                "release_group_mbids": set(),
                "barcodes": set(),
                "catalog_numbers": set(),
                "discogs_ids": set(),
                "labels": set(),
                "artist_sort_names": set(),
                "acoustid_ids": set(),
                "disc_totals": set(),
                "track_totals": set(),
            })
            for key, column in (
                ("languages", "language"),
                ("countries", "country"),
                ("scripts", "script"),
                ("release_mbids", "mb_albumid"),
                ("release_group_mbids", "mb_releasegroupid"),
                ("barcodes", "barcode"),
                ("catalog_numbers", "catalognum"),
                ("discogs_ids", "discogs_albumid"),
                ("labels", "label"),
                ("artist_sort_names", "albumartist_sort"),
                ("acoustid_ids", "acoustid_id"),
                ("disc_totals", "disctotal"),
                ("track_totals", "tracktotal"),
            ):
                if row[column]:
                    values[key].update(_split_scope_values(str(row[column])))
            tracks_matched += 1
    finally:
        connection.close()
    albums_matched = 0
    counts: dict[str, int] = {}
    for review in store.list_reviews():
        metadata = grouped.get(review.local.relative_path)
        if metadata:
            review.local.languages = sorted(set(review.local.languages) | metadata["languages"])
            review.local.release_countries = sorted(
                set(review.local.release_countries) | metadata["countries"]
            )
            review.local.scripts = sorted(set(review.local.scripts) | metadata["scripts"])
            review.local.release_mbid = _single_value(metadata["release_mbids"], review.local.release_mbid)
            review.local.release_group_mbid = _single_value(
                metadata["release_group_mbids"], review.local.release_group_mbid
            )
            review.local.barcode = _single_value(metadata["barcodes"], review.local.barcode)
            review.local.catalog_number = _single_value(
                metadata["catalog_numbers"], review.local.catalog_number
            )
            review.local.discogs_id = _single_value(metadata["discogs_ids"], review.local.discogs_id)
            review.local.label = _single_value(metadata["labels"], review.local.label)
            review.local.artist_sort_name = _single_value(
                metadata["artist_sort_names"], review.local.artist_sort_name
            )
            review.local.acoustid_available = bool(metadata["acoustid_ids"])
            review.local.disc_total = _single_int(metadata["disc_totals"], review.local.disc_total)
            review.local.tag_track_total = _single_int(metadata["track_totals"], review.local.tag_track_total)
            albums_matched += 1
        scope, reason = classify_rating_scope(review)
        review.local.rating_scope = scope
        review.local.rating_scope_reason = reason
        if not scope.startswith("INCLUDE"):
            review.evidence = []
        review = classify(review, scoring_config or ScoringConfig())
        store.save_reviews([review])
        counts[scope] = counts.get(scope, 0) + 1
    return {
        "status": "BEETS_SCOPE_METADATA_IMPORTED",
        "tracks_matched": tracks_matched,
        "albums_matched": albums_matched,
        "scope_counts": counts,
    }


def _split_scope_values(value: str) -> set[str]:
    return {item.strip() for item in re.split(r"[;,/]", value) if item.strip()}


def _single_value(values: set[str], fallback: str | None) -> str | None:
    return next(iter(values)) if len(values) == 1 else fallback


def _single_int(values: set[str], fallback: int | None) -> int | None:
    value = _single_value(values, None)
    return int(value) if value and value.isdigit() else fallback
