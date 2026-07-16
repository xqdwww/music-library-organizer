from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import OrganizerError
from .media import SUPPORTED, clean_component, read_metadata, sha256, track_number


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def scan(root: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise OrganizerError("library root is not a directory")
    files: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in SUPPORTED:
            continue
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not _inside(root, path) or not path.is_file():
            skipped.append({"source": relative, "reason": "not a regular in-root file"})
            continue
        try:
            files.append({
                "source": relative,
                "format": path.suffix.lower()[1:],
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
                "metadata": read_metadata(path),
            })
        except OrganizerError as exc:
            skipped.append({"source": relative, "reason": str(exc)})
    return {"schema_version": 1, "library": root.name, "files": files, "skipped": skipped}


def _load_overrides(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    if not path.is_file() or path.is_symlink():
        raise OrganizerError("metadata override must be a regular JSON or CSV file")
    if path.suffix.lower() == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise OrganizerError("JSON metadata must map source paths to field objects")
        return {
            str(key): {str(k): str(v) for k, v in fields.items()}
            for key, fields in value.items()
            if isinstance(fields, dict)
        }
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if any(not row.get("source") for row in rows):
            raise OrganizerError("CSV metadata requires a source column")
        return {str(row["source"]): {k: v for k, v in row.items() if k != "source" and v} for row in rows}
    raise OrganizerError("metadata override must use .json or .csv")


def create_plan(root: Path, overrides_path: Path | None = None) -> dict[str, Any]:
    root = root.expanduser().resolve()
    report = scan(root)
    overrides = _load_overrides(overrides_path)
    known = {row["source"] for row in report["files"]}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise OrganizerError(f"metadata references unknown source: {unknown[0]}")
    items: list[dict[str, Any]] = []
    targets: set[str] = set()
    for row in report["files"]:
        metadata = dict(row["metadata"])
        metadata.update(overrides.get(row["source"], {}))
        artist = clean_component(metadata.get("artist", ""), "Unknown Artist")
        album = clean_component(metadata.get("album", ""), "Unknown Album")
        title = clean_component(metadata.get("title", Path(row["source"]).stem), "Untitled")
        number = track_number(metadata.get("tracknumber"))
        target = f"{artist}/{album}/{number:02d} - {title}{Path(row['source']).suffix.lower()}"
        folded = target.casefold()
        if folded in targets:
            raise OrganizerError(f"target collision: {target}")
        targets.add(folded)
        items.append({"source": row["source"], "source_sha256": row["sha256"], "target": target, "metadata": metadata})
    payload: dict[str, Any] = {
        "schema_version": 1,
        "source_root": str(root),
        "items": items,
        "skipped": report["skipped"],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    payload["plan_sha256"] = hashlib.sha256(canonical).hexdigest()
    return payload


def write_json(value: dict[str, Any], output: Path, force: bool = False) -> None:
    if output.exists() and not force:
        raise OrganizerError("output exists; use --force to replace it")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            temporary.replace(output)
        else:
            os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
