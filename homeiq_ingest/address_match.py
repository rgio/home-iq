"""Fallback fuzzy address matching for the permit -> PIN join (spec §4).

Used only when the spatial join fails: no lat/long, or the point lands
outside every parcel polygon (e.g. geocoded to the street centerline / a
right-of-way). Normalizes both sides with usaddress where it succeeds,
falling back to a regex-based normalizer otherwise, then fuzzy-matches
within ZIP only and accepts nothing below the similarity threshold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import usaddress
from rapidfuzz import fuzz, process

# Canonicalize both long-form and common variant spellings down to a single
# USPS-style abbreviation so "AVENUE" vs "AVE" vs "AV" don't cost us match
# score for no real reason.
_SUFFIX_MAP = {
    "AVENUE": "AVE", "AV": "AVE",
    "STREET": "ST",
    "BOULEVARD": "BLVD",
    "DRIVE": "DR",
    "PLACE": "PL",
    "ROAD": "RD",
    "LANE": "LN",
    "COURT": "CT",
    "TERRACE": "TER",
    "CIRCLE": "CIR",
    "PARKWAY": "PKWY",
    "HIGHWAY": "HWY",
    "ALLEY": "ALY",
    "SQUARE": "SQ",
    "TRAIL": "TRL",
}
_DIRECTIONAL_MAP = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}
_CANON_MAP = {**_SUFFIX_MAP, **_DIRECTIONAL_MAP}

_PUNCT_RE = re.compile(r"[.,#]")
_WS_RE = re.compile(r"\s+")
_ZIP_SUFFIX_RE = re.compile(r"\b\d{5}(-\d{4})?\s*$")


def _canonicalize_tokens(text: str) -> str:
    text = _ZIP_SUFFIX_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text.upper())
    text = _WS_RE.sub(" ", text).strip()
    tokens = [_CANON_MAP.get(tok, tok) for tok in text.split(" ")]
    return " ".join(tok for tok in tokens if tok)


_USADDRESS_ORDER = (
    "AddressNumber",
    "StreetNamePreDirectional",
    "StreetNamePreType",
    "StreetName",
    "StreetNamePostType",
    "StreetNamePostDirectional",
)


def normalize_address(text: str) -> str:
    """Best-effort canonical form of a free-text street address, dropping
    unit/apartment info and the trailing zip if present. Never raises."""
    if not text or not text.strip():
        return ""
    try:
        tagged, _addr_type = usaddress.tag(text)
        parts = [tagged[k] for k in _USADDRESS_ORDER if k in tagged]
        if parts:
            return _canonicalize_tokens(" ".join(parts))
    except Exception:  # noqa: BLE001 — usaddress can raise on ambiguous input
        pass
    return _canonicalize_tokens(text)


def build_resbldg_address(
    building_number: str, direction_prefix: str, street_name: str,
    street_type: str, direction_suffix: str,
) -> str:
    """EXTR_ResBldg.csv already has address components split into columns —
    build the same canonical string directly rather than re-parsing text,
    since that's strictly more reliable than round-tripping through
    usaddress."""
    raw = " ".join(
        p.strip() for p in
        (building_number, direction_prefix, street_name, street_type, direction_suffix)
        if p and p.strip()
    )
    return _canonicalize_tokens(raw)


@dataclass(frozen=True)
class AddressMatch:
    major: str
    minor: str
    score: float
    matched_address: str


class ZipAddressIndex:
    """Candidate addresses grouped by ZIP, for fast within-ZIP fuzzy lookup."""

    def __init__(self) -> None:
        self._by_zip: dict[str, list[tuple[str, str, str]]] = {}

    def add(self, zip_code: str, major: str, minor: str, normalized_address: str) -> None:
        if not zip_code or not normalized_address:
            return
        self._by_zip.setdefault(zip_code, []).append((normalized_address, major, minor))

    def match(self, query_address: str, zip_code: str, threshold: float = 90.0) -> AddressMatch | None:
        candidates = self._by_zip.get(zip_code)
        if not candidates or not query_address:
            return None
        choices = [c[0] for c in candidates]
        result = process.extractOne(
            query_address, choices, scorer=fuzz.token_sort_ratio, score_cutoff=threshold
        )
        if result is None:
            return None
        matched_text, score, idx = result
        _, major, minor = candidates[idx]
        return AddressMatch(major=major, minor=minor, score=score, matched_address=matched_text)
