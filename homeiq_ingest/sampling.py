"""Stratified sample for the spec §5 hand-labeling exercise (300 permits).

Stratifies on the *rules* classifier's predicted bucket rather than sampling
randomly, so rare-but-important classes (garage_conversion, bath_remodel)
get enough labeled examples to compute a meaningful per-class F1, instead of
those slots being eaten by whatever's most common in the raw permit stream
(which the rules baseline puts at "other," "addition_sqft," and ADUs).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine

from homeiq_ingest.config import IN_SCOPE_PERMIT_FILTER_SQL

DEFAULT_N_TOTAL = 300


def stratified_sample_for_labeling(
    engine: Engine, n_total: int = DEFAULT_N_TOTAL, seed: int = 42,
) -> pd.DataFrame:
    query = f"""
        SELECT p.permitnum, p.description, p.permittypedesc, p.statuscurrent,
               p.estprojectcost, p.originaladdress1,
               c.project_class AS rules_predicted_class, c.confidence AS rules_confidence
        FROM permits p
        JOIN permit_classification c ON c.permitnum = p.permitnum AND c.source = 'rules'
        WHERE {IN_SCOPE_PERMIT_FILTER_SQL}
    """
    pool = pd.read_sql(query, engine)
    classes = sorted(pool["rules_predicted_class"].unique())
    target_per_class = max(1, n_total // len(classes))

    picked = []
    for cls in classes:
        subset = pool[pool["rules_predicted_class"] == cls]
        picked.append(subset.sample(n=min(len(subset), target_per_class), random_state=seed))
    sample = pd.concat(picked, ignore_index=True)

    shortfall = n_total - len(sample)
    if shortfall > 0:
        remaining = pool[~pool["permitnum"].isin(sample["permitnum"])]
        sample = pd.concat(
            [sample, remaining.sample(n=min(shortfall, len(remaining)), random_state=seed)],
            ignore_index=True,
        )

    sample = sample.sample(frac=1, random_state=seed).reset_index(drop=True)  # shuffle order
    sample["human_project_class"] = ""
    sample["human_scope_flags"] = ""
    sample["notes"] = ""
    return sample
