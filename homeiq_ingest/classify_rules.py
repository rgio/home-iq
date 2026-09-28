"""Keyword/regex baseline classifier for the project taxonomy (spec §5).

Not the classifier the spec actually wants shipped — that's the few-shot LLM
classifier in classify_llm.py, validated against hand labels to a per-class
F1 bar. This one exists for three things that don't need an LLM or hand
labels at all:
  1. bucketing permits into rough classes so the stratified hand-labeling
     sample (spec §5's 300-permit set) actually covers rare classes like
     ADUs, not just whatever's most common in the raw permit stream
  2. a zero-config fallback so the rest of the pipeline (feature engineering,
     cost model) can run end-to-end before an LLM API key is configured
  3. a sanity baseline to compare the LLM classifier against

Confidence values are heuristic, not calibrated probabilities.
"""

from __future__ import annotations

import re

import pandas as pd

from homeiq_ingest.taxonomy import PROJECT_CLASSES

_PATTERNS: dict[str, re.Pattern] = {
    "adu_detached": re.compile(r"\b(dadu|detached\s+accessory|detached\s+adu)\b", re.I),
    "adu_attached": re.compile(r"\b(aadu|attached\s+accessory|attached\s+adu|accessory\s+dwelling|\badu\b)\b", re.I),
    "garage_conversion": re.compile(r"\bgarage\b.{0,30}\bconver|\bconver\w*\b.{0,30}\bgarage\b", re.I),
    "basement_finish": re.compile(r"\bbasement\b", re.I),
    "kitchen_remodel": re.compile(r"\bkitchen", re.I),
    "bath_remodel": re.compile(r"\bbath(room)?s?\b", re.I),
    "addition_sqft": re.compile(r"\baddition\b|\badd(ing)?\b.{0,20}\bsq\.?\s?ft", re.I),
    "deck_porch": re.compile(r"\b(deck|porch)\b", re.I),
    "roof": re.compile(r"\broof(ing)?\b", re.I),
    "siding_windows": re.compile(r"\b(siding|windows?)\b", re.I),
    "structural_work": re.compile(
        r"\bseismic\b|\bretrofit\b|\bfoundation\s+repair|\bpush\s+pier|\bhelical\s+pile|\bunderpin", re.I
    ),
}

_WHOLE_HOUSE_RE = re.compile(
    r"\bwhole\s+house\b|\bentire\s+house\b|\bfull(ly)?\s+remodel\b|\bcomplete\s+remodel\b|\bgut\b", re.I
)

# Priority order when multiple classes match and it's not a whole-house job —
# roughly "most structurally significant first".
_PRIORITY = [
    "adu_detached", "adu_attached", "addition_sqft", "basement_finish",
    "garage_conversion", "structural_work", "kitchen_remodel", "bath_remodel",
    "roof", "siding_windows", "deck_porch",
]

_STRUCTURAL_CLASSES = {
    "addition_sqft", "adu_attached", "adu_detached", "basement_finish",
    "garage_conversion", "structural_work", "whole_house",
}
_SQFT_ADDED_CLASSES = {"addition_sqft", "adu_attached", "adu_detached"}
_LAYOUT_CHANGE_CLASSES = {"kitchen_remodel", "whole_house", "basement_finish"}
_PLUMBING_CLASSES = {"kitchen_remodel", "bath_remodel", "adu_attached", "adu_detached"}

_LAYOUT_KEYWORDS_RE = re.compile(r"\breconfigur|\blayout\b|\bfloor\s?plan\b|\bopen\s+concept\b|\bcombine\b", re.I)
_PLUMBING_KEYWORDS_RE = re.compile(r"\bplumbing\b|\brelocat\w*\s+(sink|toilet|plumbing)\b", re.I)
_STRUCTURAL_KEYWORDS_RE = re.compile(r"\bstructural\b|\bload[\s-]?bearing\b", re.I)


def classify_description(description: str, housingunitsadded: float = 0.0) -> dict:
    text = description or ""
    matches = [cls for cls, pat in _PATTERNS.items() if pat.search(text)]

    if (housingunitsadded or 0) >= 1 and any(c.startswith("adu_") for c in matches):
        project_class = "adu_detached" if "adu_detached" in matches else "adu_attached"
        confidence = 0.75
    elif _WHOLE_HOUSE_RE.search(text) or len(set(matches)) >= 3:
        project_class = "whole_house"
        confidence = 0.6
    elif len(matches) == 1:
        project_class = matches[0]
        confidence = 0.7
    elif len(matches) > 1:
        project_class = next(c for c in _PRIORITY if c in matches)
        confidence = 0.5
    else:
        project_class = "other"
        confidence = 0.2

    scope_flags = []
    if project_class in _STRUCTURAL_CLASSES or _STRUCTURAL_KEYWORDS_RE.search(text):
        scope_flags.append("structural")
    if project_class in _SQFT_ADDED_CLASSES or (housingunitsadded or 0) > 0:
        scope_flags.append("sqft_added")
    if project_class in _LAYOUT_CHANGE_CLASSES or _LAYOUT_KEYWORDS_RE.search(text):
        scope_flags.append("layout_change")
    if project_class in _PLUMBING_CLASSES or _PLUMBING_KEYWORDS_RE.search(text):
        scope_flags.append("plumbing_moved")

    return {"project_class": project_class, "confidence": confidence, "scope_flags": scope_flags}


def classify_batch(permits: pd.DataFrame) -> pd.DataFrame:
    """permits needs `permitnum`, `description`; `housingunitsadded` optional."""
    has_units = "housingunitsadded" in permits.columns
    records = []
    for row in permits.itertuples(index=False):
        units = getattr(row, "housingunitsadded", 0.0) if has_units else 0.0
        description = row.description if isinstance(row.description, str) else ""
        result = classify_description(description, units)
        records.append({"permitnum": row.permitnum, "source": "rules", **result})
    df = pd.DataFrame(records)
    assert set(df["project_class"]).issubset(set(PROJECT_CLASSES))
    return df
