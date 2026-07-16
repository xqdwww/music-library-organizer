from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from mutagen import File
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover

from .errors import OrganizerError

SUPPORTED = {".mp3", ".flac", ".m4a"}
FIELDS = ("title", "artist", "album", "tracknumber", "discnumber")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_metadata(path: Path) -> dict[str, str]:
    try:
        audio = File(path, easy=True)
    except Exception as exc:
        raise OrganizerError(f"cannot read media metadata: {path.name}: {exc}") from exc
    if audio is None:
        raise OrganizerError(f"unsupported or invalid media file: {path.name}")
    result: dict[str, str] = {}
    for field in FIELDS:
        value = audio.get(field, [])
        if value:
            result[field] = str(value[0]).strip()
    return result


def write_metadata(path: Path, metadata: dict[str, str], cover: Path | None = None) -> None:
    try:
        audio = File(path, easy=True)
        if audio is None:
            raise OrganizerError(f"unsupported or invalid media file: {path.name}")
        for field in FIELDS:
            value = metadata.get(field)
            if value:
                audio[field] = [str(value)]
        audio.save()
        if cover:
            _embed_cover(path, cover)
    except OrganizerError:
        raise
    except Exception as exc:
        raise OrganizerError(f"cannot write media metadata: {path.name}: {exc}") from exc


def _cover_data(path: Path) -> tuple[bytes, str]:
    if not path.is_file() or path.is_symlink():
        raise OrganizerError("cover must be a regular local file")
    data = path.read_bytes()
    if len(data) > 20 * 1024 * 1024:
        raise OrganizerError("cover exceeds 20 MiB")
    mime = mimetypes.guess_type(path.name)[0]
    if mime not in {"image/jpeg", "image/png"}:
        raise OrganizerError("cover must be JPEG or PNG")
    signatures = {"image/jpeg": b"\xff\xd8\xff", "image/png": b"\x89PNG\r\n\x1a\n"}
    if not data.startswith(signatures[mime]):
        raise OrganizerError("cover content does not match its file type")
    return data, mime


def _embed_cover(path: Path, cover: Path) -> None:
    data, mime = _cover_data(cover)
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            tags = ID3()
        tags.delall("APIC")
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
        tags.save(path)
    elif suffix == ".flac":
        audio = FLAC(path)
        audio.clear_pictures()
        picture = Picture()
        picture.type = 3
        picture.mime = mime
        picture.desc = "Cover"
        picture.data = data
        audio.add_picture(picture)
        audio.save()
    elif suffix == ".m4a":
        audio = MP4(path)
        image_format = MP4Cover.FORMAT_JPEG if mime == "image/jpeg" else MP4Cover.FORMAT_PNG
        audio["covr"] = [MP4Cover(data, imageformat=image_format)]
        audio.save()


def extract_cover(source: Path, output: Path, force: bool = False) -> dict[str, Any]:
    if source.suffix.lower() not in SUPPORTED or not source.is_file() or source.is_symlink():
        raise OrganizerError("source must be a regular MP3, FLAC, or M4A file")
    if output.exists() and not force:
        raise OrganizerError("output exists; use --force to replace it")
    suffix = source.suffix.lower()
    data: bytes | None = None
    mime = "application/octet-stream"
    if suffix == ".mp3":
        tags = ID3(source)
        pictures = tags.getall("APIC")
        if pictures:
            data, mime = pictures[0].data, pictures[0].mime
    elif suffix == ".flac":
        pictures = FLAC(source).pictures
        if pictures:
            data, mime = pictures[0].data, pictures[0].mime
    else:
        covers = MP4(source).get("covr", [])
        if covers:
            data = bytes(covers[0])
            mime = "image/png" if covers[0].imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
    if data is None:
        raise OrganizerError("media file has no embedded cover")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            temporary.replace(output)
        else:
            os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {"status": "written", "output": str(output), "mime": mime, "bytes": len(data)}


def clean_component(value: str, fallback: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", value).strip().strip(".")
    value = re.sub(r"\s+", " ", value)
    return value[:120] or fallback


def track_number(value: str | None) -> int:
    if not value:
        return 0
    match = re.match(r"\s*(\d+)", value)
    return int(match.group(1)) if match else 0
