from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import OrganizerError
from .media import SUPPORTED, sha256, write_metadata


def _target_path(destination: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts or pure.suffix.lower() not in SUPPORTED:
        raise OrganizerError(f"unsafe target path: {relative}")
    target = destination.joinpath(*pure.parts)
    try:
        target.resolve(strict=False).relative_to(destination.resolve())
    except ValueError as exc:
        raise OrganizerError(f"target escapes destination: {relative}") from exc
    return target


def load_plan(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise OrganizerError("plan must be a regular JSON file")
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OrganizerError(f"cannot read plan: {exc}") from exc
    if plan.get("schema_version") != 1 or not isinstance(plan.get("items"), list):
        raise OrganizerError("unsupported plan schema")
    claimed_digest = plan.get("plan_sha256")
    unsigned = {key: value for key, value in plan.items() if key != "plan_sha256"}
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    if not isinstance(claimed_digest, str) or hashlib.sha256(canonical).hexdigest() != claimed_digest:
        raise OrganizerError("plan integrity check failed; create a new plan")
    return plan


def apply(plan: dict[str, Any], destination: Path, execute: bool = False, cover: Path | None = None) -> dict[str, Any]:
    source_root = Path(str(plan.get("source_root", ""))).expanduser().resolve()
    destination = destination.expanduser().absolute()
    if not source_root.is_dir():
        raise OrganizerError("plan source root is unavailable")
    if destination.exists() and destination.is_symlink():
        raise OrganizerError("destination cannot be a symlink")
    destination = destination.resolve()
    prepared: list[tuple[Path, Path, dict[str, str]]] = []
    seen: set[Path] = set()
    for item in plan["items"]:
        if not isinstance(item, dict):
            raise OrganizerError("invalid plan item")
        source_rel = PurePosixPath(str(item.get("source", "")))
        if source_rel.is_absolute() or ".." in source_rel.parts:
            raise OrganizerError("unsafe source path in plan")
        source = source_root.joinpath(*source_rel.parts)
        try:
            source.resolve().relative_to(source_root)
        except ValueError as exc:
            raise OrganizerError("source escapes plan root") from exc
        if source.is_symlink() or not source.is_file():
            raise OrganizerError(f"source is not a regular file: {source_rel}")
        if sha256(source) != item.get("source_sha256"):
            raise OrganizerError(f"source changed after planning: {source_rel}")
        target = _target_path(destination, str(item.get("target", "")))
        if target in seen or target.exists() or target.is_symlink():
            raise OrganizerError(f"target conflict: {item.get('target')}")
        seen.add(target)
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            raise OrganizerError("invalid metadata in plan")
        prepared.append((source, target, {str(k): str(v) for k, v in metadata.items()}))
    report = {"status": "dry-run" if not execute else "applied", "planned": len(prepared), "written": []}
    if not execute:
        report["targets"] = [str(target.relative_to(destination)) for _, target, _ in prepared]
        return report
    created: list[Path] = []
    try:
        destination.mkdir(parents=True, exist_ok=True)
        for source, target, metadata in prepared:
            target.parent.mkdir(parents=True, exist_ok=True)
            relative_parent = target.parent.relative_to(destination)
            destination_cursor = destination
            parent_chain = [destination]
            for part in relative_parent.parts:
                destination_cursor = destination_cursor / part
                parent_chain.append(destination_cursor)
            if any(parent.is_symlink() for parent in parent_chain):
                raise OrganizerError("destination path contains a symlink")
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{target.stem}.", suffix=f".tmp{target.suffix.lower()}", dir=target.parent
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_stream:
                    shutil.copyfileobj(input_stream, output)
                    output.flush()
                    os.fsync(output.fileno())
                write_metadata(temporary, metadata, cover)
                os.link(temporary, target)
                created.append(target)
            finally:
                temporary.unlink(missing_ok=True)
            report["written"].append(str(target.relative_to(destination)))
        return report
    except Exception:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        raise
