from __future__ import annotations

import re
import unicodedata

EDITION_PATTERNS = (
    r"\bsuper deluxe(?: edition)?\b",
    r"\bdeluxe(?: edition)?\b",
    r"\bremaster(?:ed)?(?: edition)?(?: \d{4})?\b",
    r"\banniversary edition\b",
    r"\bexpanded edition\b",
    r"\bbonus tracks?(?: version)?\b",
    r"\bjapan(?:ese)? edition\b",
    r"\bjapan bonus tracks?\b",
    r"\bcollector'?s edition\b",
    r"\bmono(?: version| mix)?\b",
    r"\bstereo(?: version| mix)?\b",
    r"\boriginal motion picture soundtrack\b",
)
DISC_SUFFIXES = (
    r"\b(?:cd|disc|disk)[ ._-]*\d+\b",
    r"\bvol(?:ume)?[ ._-]*\d+\b",
)


def normalized_text(
    value: str,
    *,
    strip_edition: bool = False,
    strip_the: bool = False,
    strip_disc: bool = False,
) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = re.sub(r"\s*\(\d+\)\s*$", "", value)  # Discogs artist disambiguator, e.g. "Adele (3)".
    value = value.replace("＆", "&")
    value = re.sub(r"\b(feat(?:uring)?\.?|with)\b.*$", "", value)
    if strip_edition:
        for pattern in EDITION_PATTERNS:
            value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    if strip_disc:
        for pattern in DISC_SUFFIXES:
            value = re.sub(pattern, " ", value, flags=re.IGNORECASE)
    value = re.sub(r"[\[\]【】()（）{}「」『』《》]", " ", value)
    value = re.sub(r"[\W_]+", " ", value, flags=re.UNICODE).strip()
    if strip_the and value.startswith("the "):
        value = value[4:]
    return re.sub(r"\s+", " ", value)


def exact_identity(artist: str, album: str) -> tuple[str, str]:
    return normalized_text(artist), normalized_text(album)


def canonical_identity(artist: str, album: str) -> tuple[str, str]:
    return (
        normalized_text(artist, strip_the=True),
        normalized_text(album, strip_edition=True, strip_disc=True),
    )


def normalization_trace(
    value: str,
    *,
    strip_edition: bool = False,
    strip_the: bool = False,
    strip_disc: bool = False,
) -> dict[str, object]:
    normalized = normalized_text(
        value,
        strip_edition=strip_edition,
        strip_the=strip_the,
        strip_disc=strip_disc,
    )
    steps = ["NFKC", "casefold", "punctuation_and_space"]
    if strip_the:
        steps.append("leading_the")
    if strip_edition:
        steps.append("edition_suffix")
    if strip_disc:
        steps.append("disc_suffix")
    return {"original_value": value, "normalized_value": normalized, "normalization_steps": steps}


def parse_year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(?:19|20)\d{2}", value)
    return int(match.group()) if match else None
