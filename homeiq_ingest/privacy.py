"""Enforce the licensing note in spec §3: BuyerName/SellerName (and, for
public-facing exports, raw street address) must never leave the ingestion
layer or enter version control."""

from __future__ import annotations

import pandas as pd

from homeiq_ingest.config import PII_COLUMNS


def strip_pii(df: pd.DataFrame) -> pd.DataFrame:
    """Drop any column that identifies a buyer/seller by name. Safe to call
    on every table before it's persisted to disk, written to Postgres, or
    handed to anything downstream of the ingestion layer."""
    cols_to_drop = [c for c in df.columns if c in PII_COLUMNS]
    return df.drop(columns=cols_to_drop)


def to_public_safe(df: pd.DataFrame, address_columns: list[str]) -> pd.DataFrame:
    """Additionally drop raw street address columns, for anything that will
    be rendered in the UI or returned from a public-facing tool call. Internal
    joins should use strip_pii() only, since address is needed for the fuzzy
    fallback match."""
    df = strip_pii(df)
    cols_to_drop = [c for c in address_columns if c in df.columns]
    return df.drop(columns=cols_to_drop)
