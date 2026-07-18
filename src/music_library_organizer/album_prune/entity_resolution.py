from __future__ import annotations

import re
import unicodedata
from typing import Any

from .models import LocalAlbum
from .normalize import normalization_trace, normalized_text

HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[\uac00-\ud7af]")
CHINESE_LANGUAGES = {"zh", "zho", "chi", "cmn", "yue", "chinese", "mandarin", "cantonese"}
JAPANESE_LANGUAGES = {"ja", "jpn", "japanese"}
KOREAN_LANGUAGES = {"ko", "kor", "korean"}
CHINESE_REGIONS = {"cn", "china", "hk", "hong kong", "mo", "macau", "tw", "taiwan"}
JAPANESE_REGIONS = {"jp", "japan"}
KOREAN_REGIONS = {"kr", "south korea", "kp", "north korea"}
CANTONESE_MARKERS = {"yue", "cantonese", "cantopop", "粤语", "粵語"}
JAPANESE_LABEL_MARKERS = (
    "avex",
    "japan",
    "jvc",
    "king records",
    "nippon columbia",
    "pony canyon",
    "toho",
    "victor entertainment",
)
JAPANESE_CATALOG = re.compile(r"^(?:AV|BV|CO|ES|KIC|PCC|SR|SVW|VIC)[A-Z0-9-]*\d", re.IGNORECASE)


def language_route(local: LocalAlbum) -> dict[str, Any]:
    primary_artist_identity = " ".join(
        value for value in [local.artist, local.artist_sort_name] if value
    )
    alias_identity = " ".join(value for value in local.artist_aliases if value)
    album_identity = f"{local.album} {local.relative_path}"
    languages = {value.casefold() for value in local.languages}
    release_countries = {value.casefold() for value in local.release_countries}
    artist_countries = {value.casefold() for value in local.artist_countries}
    countries = release_countries | artist_countries
    scripts = {value.casefold() for value in local.scripts}
    genre_text = " ".join(local.genres).casefold()
    label_text = (local.label or "").casefold()
    language_evidence: list[str] = []
    country_evidence: list[str] = []
    resolver_sources: list[str] = ["local_tags", "script_analysis"]
    decision_trace: list[dict[str, Any]] = []
    signals: set[str] = set()
    specialty_with_latin_artist = bool(
        local.category in {"Classical", "Jazz"}
        and not any(pattern.search(primary_artist_identity) for pattern in (HAN, KANA, HANGUL))
    )
    if languages & CHINESE_LANGUAGES or scripts & {"hans", "hant"}:
        signals.add("ZH")
        language_evidence.append("explicit Chinese language/script")
    if any(marker in genre_text for marker in ("华语", "国语", "粤语", "中文", "mandopop", "cantopop")):
        signals.add("ZH")
        language_evidence.append("Chinese genre marker")
    if (
        (languages & JAPANESE_LANGUAGES and not specialty_with_latin_artist)
        or scripts & {"jpan", "kana"}
        or KANA.search(primary_artist_identity)
        or (KANA.search(alias_identity) and HAN.search(primary_artist_identity))
        or artist_countries & JAPANESE_REGIONS
    ):
        signals.add("JA")
        language_evidence.append("Japanese language/script/primary-identity evidence")
    elif languages & JAPANESE_LANGUAGES and specialty_with_latin_artist:
        language_evidence.append("Japanese release language ignored for Latin-script specialty artist")
    if HAN.search(primary_artist_identity) and KANA.search(album_identity):
        signals.add("JA")
        language_evidence.append("Han artist identity with kana album evidence")
    elif KANA.search(album_identity):
        signals.add("JA")
        language_evidence.append("kana album identity")
    if (
        (languages & KOREAN_LANGUAGES and not specialty_with_latin_artist)
        or scripts & {"kore", "hang"}
        or HANGUL.search(primary_artist_identity)
        or artist_countries & KOREAN_REGIONS
    ):
        signals.add("KO")
        language_evidence.append("Korean language/script/primary-identity evidence")
    if HAN.search(primary_artist_identity) and countries & CHINESE_REGIONS:
        signals.add("ZH")
        country_evidence.append("Han artist identity and Chinese-region evidence")
    if HAN.search(primary_artist_identity) and countries & JAPANESE_REGIONS:
        signals.add("JA")
        country_evidence.append("Han artist identity and Japanese-region evidence")
    if countries & KOREAN_REGIONS:
        signals.add("KO")
        country_evidence.append("Korean region evidence")
    if (
        HAN.search(primary_artist_identity)
        and not signals
        and (any(marker in label_text for marker in JAPANESE_LABEL_MARKERS)
             or (local.catalog_number and JAPANESE_CATALOG.search(local.catalog_number)))
    ):
        signals.add("JA")
        language_evidence.append("Japanese label/catalog evidence with Han artist identity")
    cantonese = bool(
        languages & CANTONESE_MARKERS
        or any(marker in genre_text for marker in CANTONESE_MARKERS)
        or (HAN.search(primary_artist_identity) and countries & {"hk", "hong kong", "mo", "macau"})
    )
    if cantonese and signals <= {"ZH"}:
        bucket = "HK_TW_CANTONESE"
        confidence = 0.95
        signals.add("ZH")
        language_evidence.append("Cantonese or Hong Kong/Macau evidence")
    elif len(signals) > 1:
        bucket = "MIXED_CJK"
        confidence = 0.7
    elif signals == {"ZH"}:
        bucket = "ZH_CONFIRMED"
        confidence = 0.95
    elif signals == {"JA"}:
        bucket = "JA_CONFIRMED"
        confidence = 0.95
    elif signals == {"KO"}:
        bucket = "KO_CONFIRMED"
        confidence = 0.95
    elif HAN.search(primary_artist_identity):
        bucket = "UNKNOWN_CJK"
        confidence = 0.35
        language_evidence.append("Han artist identity without decisive language evidence")
    elif HAN.search(album_identity) and normalized_text(local.artist) in {
        "original soundtrack",
        "soundtrack",
        "unknown artist",
        "various artists",
        "原声大碟",
    }:
        bucket = "UNKNOWN_CJK"
        confidence = 0.3
        language_evidence.append("generic artist with Han album identity")
    else:
        bucket = "NON_CJK"
        confidence = 0.9
        if HAN.search(album_identity):
            language_evidence.append("Han appears only in album/path annotation; artist identity is non-CJK")
        else:
            language_evidence.append("no CJK artist identity signal")
    country_status = "UNKNOWN"
    if countries & JAPANESE_REGIONS:
        country_status = "JP"
    elif countries & KOREAN_REGIONS:
        country_status = "KR"
    elif countries & {"hk", "hong kong"}:
        country_status = "HK"
    elif countries & {"tw", "taiwan"}:
        country_status = "TW"
    elif countries & {"cn", "china"}:
        country_status = "CN"
    detected_scripts = {
        name for name, pattern in (("HAN", HAN), ("KANA", KANA), ("HANGUL", HANGUL))
        if pattern.search(primary_artist_identity)
    }
    script_status = "MIXED" if len(detected_scripts) > 1 else (
        sorted(scripts)[0].upper() if scripts else next(iter(detected_scripts), "UNKNOWN")
    )
    if local.artist_countries or local.artist_areas or local.artist_aliases:
        resolver_sources.append("musicbrainz_artist_metadata")
    decision_trace.extend([
        {"step": "primary_artist_identity", "value": primary_artist_identity},
        {"step": "artist_aliases", "value": list(local.artist_aliases)},
        {"step": "signals", "value": sorted(signals)},
        {"step": "album_annotation_han", "value": bool(HAN.search(album_identity))},
        {"step": "decision", "value": bucket, "confidence": confidence},
    ])
    return {
        "bucket": bucket,
        "language_status": bucket,
        "country_status": country_status,
        "script_status": script_status,
        "language_evidence": sorted(set([*languages, *language_evidence])),
        "country_evidence": sorted(set([*countries, *local.artist_areas, *country_evidence])),
        "script_evidence": sorted(scripts),
        "resolver_sources": resolver_sources,
        "resolver_source": "multi_evidence_cjk_v4",
        "confidence": confidence,
        "decision_trace": decision_trace,
        "evidence": language_evidence + country_evidence,
    }


def build_classical_identity(local: LocalAlbum) -> dict[str, Any]:
    return {
        "composer_ids": [],
        "work_ids": [local.work] if local.work else [],
        "performer_ids": [],
        "conductor": local.conductor,
        "orchestra": local.orchestra,
        "soloists": list(local.soloists),
        "recording_year": local.original_release_year,
        "label": local.label,
        "catalog_number": local.catalog_number,
        "release_id": local.release_mbid,
        "release_group_id": local.release_group_mbid,
        "edition": local.edition,
    }


def build_jazz_identity(local: LocalAlbum) -> dict[str, Any]:
    return {
        "leader": local.leader or local.artist,
        "primary_artist": local.artist,
        "session_personnel": list(local.session_personnel),
        "recording_date": local.recording_date,
        "session_location": None,
        "live_studio": local.live_studio,
        "label": local.label,
        "catalog_number": local.catalog_number,
        "original_release_year": local.original_release_year,
        "master_id": None,
        "release_id": local.release_mbid or local.discogs_id,
        "edition": local.edition,
        "track_count": local.track_count,
    }


def _different(left: str | None, right: str | None) -> bool:
    return bool(left and right and normalized_text(left) != normalized_text(right))


def same_classical_recording(left: dict[str, Any], right: dict[str, Any]) -> bool:
    for key in ("conductor", "orchestra", "recording_year"):
        if _different(str(left.get(key) or ""), str(right.get(key) or "")):
            return False
    left_release = left.get("release_id") or left.get("release_group_id")
    right_release = right.get("release_id") or right.get("release_group_id")
    if left_release and right_release:
        return left_release == right_release
    return bool(
        (left.get("work_ids") and right.get("work_ids"))
        and set(left["work_ids"]) == set(right["work_ids"])
        and (left.get("conductor") or left.get("orchestra") or left.get("recording_year"))
    )


def same_jazz_session(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if _different(left.get("leader"), right.get("leader")):
        return False
    if _different(left.get("recording_date"), right.get("recording_date")):
        return False
    if _different(left.get("live_studio"), right.get("live_studio")):
        return False
    left_master, right_master = left.get("master_id"), right.get("master_id")
    if left_master and right_master:
        return left_master == right_master
    return bool(left.get("recording_date") and right.get("recording_date"))


def query_variants(local: LocalAlbum) -> list[dict[str, Any]]:
    artists = [local.artist, *local.artist_aliases]
    if local.artist_sort_name:
        artists.append(local.artist_sort_name)
    unique_artists = list(dict.fromkeys(value for value in artists if value))
    album_trace = normalization_trace(local.album, strip_edition=True, strip_disc=True)
    variants = []
    for artist in unique_artists:
        variants.append({
            "artist": artist,
            "album": local.album,
            "kind": "original" if artist == local.artist else "artist_alias",
            "artist_trace": normalization_trace(artist, strip_the=True),
            "album_trace": normalization_trace(local.album),
        })
    normalized_album = str(album_trace["normalized_value"])
    if normalized_album and normalized_album != normalized_text(local.album):
        variants.append({
            "artist": local.artist,
            "album": normalized_album,
            "kind": "normalized_edition_disc",
            "artist_trace": normalization_trace(local.artist, strip_the=True),
            "album_trace": album_trace,
        })
    # MusicBrainz searches aliases itself; these Unicode variants make NFKC and
    # Japanese punctuation differences explicit without weakening acceptance.
    punctuation_album = unicodedata.normalize("NFKC", local.album).replace("・", " ").replace("〜", "~")
    if punctuation_album and normalized_text(punctuation_album) != normalized_text(local.album):
        variants.append({
            "artist": local.artist,
            "album": punctuation_album,
            "kind": "cjk_punctuation",
            "artist_trace": normalization_trace(local.artist, strip_the=True),
            "album_trace": normalization_trace(punctuation_album),
        })
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for variant in variants:
        unique[(str(variant["artist"]), str(variant["album"]))] = variant
    return list(unique.values())
