"""Derive `review_path` from permit description text — a candidate cost
feature identified in the taxonomy codebook, not part of the taxonomy itself.

No dedicated field exists in the Socrata schema (confirmed against SDCI's
own column metadata: 39 fields, none of them this). The fields that *should*
proxy for it — `totaldaysplanreview`, `numberreviewcycles`,
`planreviewcompletedate` — turned out to be populated in <1% of residential
permits regardless of STFI status when checked against real data, so they
don't substitute for the text signal; SDCI's plan-review tracking system
apparently isn't used for the over-the-counter track most single-family work
goes through.

This is NOT a hand-labeling task like project_class, though — "does the
description say STFI" is a deterministic string match with no judgment call
involved, so it's derived here rather than added to the 300-permit sample.
"STFI" appears in ~54% of the in-scope permit population, so this is a
substantial, not marginal, feature.
"""

from __future__ import annotations

import re

import pandas as pd

FIELD_INSPECTION = "field_inspection"
PLAN_REVIEW = "plan_review"
UNKNOWN = "unknown"

_STFI_RE = re.compile(
    r"\bstfi\b|subject[\s-]to[\s-]field inspection|subject[\s-]to[\s-]field insp\b", re.I
)


def derive_review_path(description: str | None) -> str:
    """"plan_review" here means "not explicitly marked STFI" — SDCI's own
    process design treats STFI as the exception (abbreviated) path, so
    absence of the marker defaults to the standard track. This is a process
    inference from how the permit was issued, not a confirmed plan-review
    cycle count (those aren't reliably logged — see module docstring)."""
    if not description or not str(description).strip():
        return UNKNOWN
    return FIELD_INSPECTION if _STFI_RE.search(str(description)) else PLAN_REVIEW


def add_review_path_column(df: pd.DataFrame, description_col: str = "description") -> pd.DataFrame:
    df = df.copy()
    df["review_path"] = df[description_col].apply(derive_review_path)
    return df
