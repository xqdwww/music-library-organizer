from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import AlbumReview, CanonicalAlbum, LocalAlbum, ProfessionalEvidence, RatingEvidence

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS reviews (
    album_id TEXT PRIMARY KEY,
    canonical_album_id TEXT,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    path TEXT NOT NULL,
    music_score REAL,
    candidate_status TEXT NOT NULL,
    match_status TEXT,
    source_count INTEGER NOT NULL,
    review_json TEXT NOT NULL,
    scanned_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS protections (
    canonical_album_id TEXT PRIMARY KEY,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    protected_at TEXT NOT NULL,
    reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS selections (
    selection_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    album_ids_json TEXT NOT NULL,
    fingerprints_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    batch_id TEXT PRIMARY KEY,
    selection_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    batch_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS calibration_feedback (
    album_id TEXT PRIMARY KEY,
    music_score REAL,
    user_decision TEXT NOT NULL,
    match_feedback TEXT NOT NULL,
    rating_feedback TEXT NOT NULL,
    marked_at TEXT NOT NULL,
    calibration_batch_id TEXT NOT NULL,
    FOREIGN KEY(album_id) REFERENCES reviews(album_id)
);
CREATE TABLE IF NOT EXISTS rating_attempts (
    album_id TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    attempted_at TEXT NOT NULL,
    PRIMARY KEY(album_id, source),
    FOREIGN KEY(album_id) REFERENCES reviews(album_id)
);
CREATE TABLE IF NOT EXISTS personal_policies (
    policy_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL,
    policy_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS curator_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    report_json TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class ReviewStore:
    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.path.chmod(0o600)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> ReviewStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def save_reviews(self, reviews: list[AlbumReview]) -> None:
        scanned = now()
        protected = {row[0] for row in self.connection.execute("SELECT canonical_album_id FROM protections")}
        with self.connection:
            for review in reviews:
                canonical_id = review.canonical.canonical_album_id if review.canonical else review.local.album_id
                if canonical_id in protected:
                    review.protected = True
                    review.candidate_status = "PROTECTED"
                match_status = review.canonical.match_status if review.canonical else None
                self.connection.execute(
                    """INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(album_id) DO UPDATE SET
                    canonical_album_id=excluded.canonical_album_id, artist=excluded.artist, album=excluded.album,
                    path=excluded.path, music_score=excluded.music_score, candidate_status=excluded.candidate_status,
                    match_status=excluded.match_status, source_count=excluded.source_count,
                    review_json=excluded.review_json, scanned_at=excluded.scanned_at""",
                    (
                        review.local.album_id,
                        canonical_id,
                        review.local.artist,
                        review.local.album,
                        review.local.path,
                        review.music_score,
                        review.candidate_status,
                        match_status,
                        len(review.evidence),
                        json.dumps(review.to_dict(), ensure_ascii=False),
                        scanned,
                    ),
                )

    @staticmethod
    def _decode(value: str) -> AlbumReview:
        data = json.loads(value)
        return AlbumReview(
            local=LocalAlbum(**data["local"]),
            canonical=CanonicalAlbum(**data["canonical"]) if data.get("canonical") else None,
            evidence=[RatingEvidence(**item) for item in data.get("evidence", [])],
            professional_evidence=[ProfessionalEvidence(**item) for item in data.get("professional_evidence", [])],
            community_score=data.get("community_score"),
            critic_score=data.get("critic_score"),
            professional_score=data.get("professional_score"),
            professional_confidence=float(data.get("professional_confidence", 0)),
            professional_source_count=int(data.get("professional_source_count", 0)),
            professional_recommendation_count=int(data.get("professional_recommendation_count", 0)),
            professional_award_count=int(data.get("professional_award_count", 0)),
            protection_reasons=list(data.get("protection_reasons", [])),
            music_score=data.get("music_score"),
            rating_status=data.get("rating_status", "INSUFFICIENT_DATA"),
            candidate_status=data.get("candidate_status", "INSUFFICIENT_DATA"),
            checked=False,
            protected=bool(data.get("protected")),
            exclusion_reason=data.get("exclusion_reason"),
            resolution_trace=list(data.get("resolution_trace", [])),
        )

    def list_reviews(self, threshold: float | None = None) -> list[AlbumReview]:
        rows = self.connection.execute(
            "SELECT review_json FROM reviews ORDER BY scanned_at DESC, rowid DESC"
        ).fetchall()
        reviews = []
        active_paths: set[str] = set()
        for row in rows:
            review = self._decode(row[0])
            if review.local.path in active_paths:
                continue
            active_paths.add(review.local.path)
            reviews.append(review)
        if threshold is not None:
            reviews = [review for review in reviews if review.music_score is None or review.music_score <= threshold]
        return sorted(
            reviews,
            key=lambda review: (
                review.music_score is None,
                review.music_score if review.music_score is not None else 101,
                -len(review.evidence),
                review.local.artist.casefold(),
                review.local.album.casefold(),
            ),
        )

    def review(self, album_id: str) -> AlbumReview:
        row = self.connection.execute("SELECT review_json FROM reviews WHERE album_id=?", (album_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown album_id: {album_id}")
        return self._decode(row[0])

    def protect(self, album_id: str, reason: str) -> str:
        review = self.review(album_id)
        canonical_id = review.canonical.canonical_album_id if review.canonical else review.local.album_id
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO protections VALUES (?, ?, ?, ?, ?)",
                (canonical_id, review.local.artist, review.local.album, now(), reason or "user protected"),
            )
            rows = self.connection.execute(
                "SELECT album_id, review_json FROM reviews WHERE canonical_album_id=?", (canonical_id,)
            ).fetchall()
            for row in rows:
                protected_review = self._decode(row["review_json"])
                protected_review.protected = True
                protected_review.candidate_status = "PROTECTED"
                self.connection.execute(
                    "UPDATE reviews SET candidate_status='PROTECTED', review_json=? WHERE album_id=?",
                    (json.dumps(protected_review.to_dict(), ensure_ascii=False), row["album_id"]),
                )
        return canonical_id

    def create_selection(
        self,
        album_ids: list[str],
        allowed_album_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        unique = list(dict.fromkeys(album_ids))
        if not unique:
            raise ValueError("selection cannot be empty")
        fingerprints: dict[str, str] = {}
        for album_id in unique:
            review = self.review(album_id)
            personally_allowed = allowed_album_ids is not None and album_id in allowed_album_ids
            if not personally_allowed and review.candidate_status not in {
                "STRONG_LOW_RATED",
                "LOW_RATED_REVIEW",
                "SINGLE_SOURCE_REVIEW",
            }:
                raise ValueError(f"album is not selectable: {album_id}: {review.candidate_status}")
            if review.protected or not review.local.safe_directory:
                raise ValueError(f"album is protected or unsafe: {album_id}")
            fingerprints[album_id] = review.local.fingerprint
        selection_id = "sel_" + secrets.token_hex(8)
        created = now()
        with self.connection:
            self.connection.execute(
                "INSERT INTO selections VALUES (?, 'USER_SELECTED', ?, ?, ?)",
                (selection_id, created, json.dumps(unique), json.dumps(fingerprints)),
            )
        return {"selection_id": selection_id, "status": "USER_SELECTED", "created_at": created, "album_ids": unique}

    def selection(self, selection_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM selections WHERE selection_id=?", (selection_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown selection_id: {selection_id}")
        return {
            "selection_id": row["selection_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "album_ids": json.loads(row["album_ids_json"]),
            "fingerprints": json.loads(row["fingerprints_json"]),
        }

    def discard_unused_selection(self, selection_id: str) -> bool:
        with self.connection:
            cursor = self.connection.execute(
                """DELETE FROM selections
                   WHERE selection_id=?
                   AND NOT EXISTS (SELECT 1 FROM batches WHERE batches.selection_id=selections.selection_id)""",
                (selection_id,),
            )
        return cursor.rowcount == 1

    def save_batch(self, batch: dict[str, Any]) -> None:
        timestamp = now()
        with self.connection:
            self.connection.execute(
                """INSERT INTO batches VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at,
                batch_json=excluded.batch_json""",
                (
                    batch["batch_id"],
                    batch["selection_id"],
                    batch["status"],
                    batch.get("created_at", timestamp),
                    timestamp,
                    json.dumps(batch, ensure_ascii=False),
                ),
            )

    def batch(self, batch_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT batch_json FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown batch_id: {batch_id}")
        return json.loads(row[0])

    def list_batches(self) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.connection.execute("SELECT batch_json FROM batches ORDER BY created_at DESC")
        ]

    def save_calibration_feedback(
        self,
        album_id: str,
        user_decision: str,
        match_feedback: str,
        rating_feedback: str,
        calibration_batch_id: str,
    ) -> dict[str, Any]:
        from .calibration import DECISIONS, MATCH_FEEDBACK, RATING_FEEDBACK

        if user_decision not in DECISIONS:
            raise ValueError("invalid user_decision")
        if match_feedback not in MATCH_FEEDBACK:
            raise ValueError("invalid match_feedback")
        if rating_feedback not in RATING_FEEDBACK:
            raise ValueError("invalid rating_feedback")
        review = self.review(album_id)
        marked_at = now()
        row = {
            "album_id": album_id,
            "music_score": review.music_score,
            "user_decision": user_decision,
            "match_feedback": match_feedback,
            "rating_feedback": rating_feedback,
            "marked_at": marked_at,
            "calibration_batch_id": calibration_batch_id,
        }
        with self.connection:
            self.connection.execute(
                """INSERT INTO calibration_feedback VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(album_id) DO UPDATE SET
                music_score=excluded.music_score, user_decision=excluded.user_decision,
                match_feedback=excluded.match_feedback, rating_feedback=excluded.rating_feedback,
                marked_at=excluded.marked_at, calibration_batch_id=excluded.calibration_batch_id""",
                tuple(row.values()),
            )
        return row

    def calibration_feedback(self, batch_id: str | None = None) -> dict[str, dict[str, Any]]:
        query = "SELECT * FROM calibration_feedback"
        values: tuple[str, ...] = ()
        if batch_id is not None:
            query += " WHERE calibration_batch_id=?"
            values = (batch_id,)
        query += " ORDER BY marked_at"
        return {
            row["album_id"]: dict(row)
            for row in self.connection.execute(query, values)
        }

    def save_personal_policy(self, value: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute("UPDATE personal_policies SET enabled=0")
            self.connection.execute(
                """INSERT INTO personal_policies VALUES ('active', 1, ?, ?)
                   ON CONFLICT(policy_id) DO UPDATE SET
                   enabled=excluded.enabled, policy_json=excluded.policy_json, updated_at=excluded.updated_at""",
                (json.dumps(value, ensure_ascii=False), now()),
            )

    def active_personal_policy(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT policy_json FROM personal_policies WHERE policy_id='active' AND enabled=1"
        ).fetchone()
        return json.loads(row[0]) if row else None

    def save_curator_report(self, run_id: str, report: dict[str, Any]) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO curator_runs VALUES (?, ?, ?)",
                (run_id, now(), json.dumps(report, ensure_ascii=False)),
            )

    def latest_curator_report(self) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT report_json FROM curator_runs ORDER BY created_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        if row is None:
            raise KeyError("no Personal Library Curator analysis is available")
        return json.loads(row[0])

    def save_rating_attempt(self, album_id: str, source: str, status: str, error: str | None = None) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT INTO rating_attempts VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(album_id, source) DO UPDATE SET
                status=excluded.status, error=excluded.error, attempted_at=excluded.attempted_at""",
                (album_id, source, status, error, now()),
            )

    def rating_attempt_counts(self, album_ids: set[str] | None = None) -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        query = "SELECT source, status, album_id FROM rating_attempts"
        rows = self.connection.execute(query)
        counts: dict[tuple[str, str], int] = {}
        for row in rows:
            if album_ids is not None and row["album_id"] not in album_ids:
                continue
            key = (row["source"], row["status"])
            counts[key] = counts.get(key, 0) + 1
        for (source, status), count in counts.items():
            result.setdefault(source, {})[status] = count
        return result

    def rating_attempts(self, source: str, album_ids: set[str] | None = None) -> dict[str, str]:
        return {
            row["album_id"]: row["status"]
            for row in self.connection.execute(
                "SELECT album_id, status FROM rating_attempts WHERE source=?",
                (source,),
            )
            if album_ids is None or row["album_id"] in album_ids
        }
