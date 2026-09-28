#!/usr/bin/env python3
"""Produce the spec §5 300-permit stratified sample for hand-labeling.

Output columns human_project_class (one of homeiq_ingest.taxonomy.PROJECT_CLASSES)
and human_scope_flags (comma-separated, from homeiq_ingest.taxonomy.SCOPE_FLAGS)
are left blank for manual entry. rules_predicted_class/confidence are shown
for reference only — don't just copy them in, that defeats the point.
"""

from homeiq_ingest.config import PROCESSED_DIR
from homeiq_ingest.db import get_engine
from homeiq_ingest.sampling import stratified_sample_for_labeling
from homeiq_ingest.taxonomy import PROJECT_CLASSES, SCOPE_FLAGS


def main() -> None:
    engine = get_engine()
    sample = stratified_sample_for_labeling(engine)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / "labeling_sample.csv"
    sample.to_csv(out_path, index=False)

    print(f"Wrote {len(sample)} permits to {out_path}")
    print(f"\nFill in human_project_class using one of:\n  {', '.join(PROJECT_CLASSES)}")
    print(f"\nFill in human_scope_flags as a comma-separated subset of (or leave blank):\n  {', '.join(SCOPE_FLAGS)}")
    print("\nrules_predicted_class/rules_confidence are shown for reference only.")


if __name__ == "__main__":
    main()
