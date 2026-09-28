#!/usr/bin/env python3
"""Pull Seattle SDCI residential permits since 2015 and cache to parquet."""

import logging

from homeiq_ingest.config import PROCESSED_DIR
from homeiq_ingest.socrata import fetch_residential_permits

logging.basicConfig(level=logging.INFO, format="%(message)s")


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    permits = fetch_residential_permits(since="2015-01-01")
    out_path = PROCESSED_DIR / "permits.parquet"
    permits.to_parquet(out_path, index=False)
    print(f"Wrote {len(permits)} permits to {out_path}")
    print(f"Missing geocode: {(~permits['has_geocode']).sum()} ({(~permits['has_geocode']).mean():.2%})")


if __name__ == "__main__":
    main()
