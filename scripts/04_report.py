#!/usr/bin/env python3
"""Print the match-rate report and write the full quarantine detail to CSV
for manual review, per spec §4's acceptance criterion."""

from homeiq_ingest.config import QUARANTINE_DIR
from homeiq_ingest.db import get_engine
from homeiq_ingest.report import compute_match_report, format_report, quarantine_detail_frame


def main() -> None:
    engine = get_engine()
    report = compute_match_report(engine)
    print(format_report(report))

    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    detail = quarantine_detail_frame(engine)
    out_path = QUARANTINE_DIR / "permit_quarantine_detail.csv"
    detail.to_csv(out_path, index=False)
    print(f"\nFull quarantine detail written to {out_path}")


if __name__ == "__main__":
    main()
