#!/usr/bin/env python3
"""Classify permits into the project taxonomy (spec §5) and cache results.

Rules backend is free and instant — safe to run on everything. LLM backend
costs real money per call, so it defaults to a small --limit rather than
silently spending on the full ~60k permit set; raise --limit deliberately.

Only classifies permits matching IN_SCOPE_PERMIT_FILTER_SQL — new
construction, demolition, and multifamily permits are excluded up front
rather than classified and left to land in "other" (see README's
taxonomy-validation section for why).
"""

import argparse

import pandas as pd

from homeiq_ingest.classification_store import already_classified, save_classifications
from homeiq_ingest.config import IN_SCOPE_PERMIT_FILTER_SQL
from homeiq_ingest.db import get_engine

DEFAULT_LLM_LIMIT = 20


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=["rules", "llm"], default="rules")
    parser.add_argument("--limit", type=int, default=None, help="Cap permits processed (LLM backend only).")
    args = parser.parse_args()

    engine = get_engine()
    permits = pd.read_sql(
        f"SELECT permitnum, description FROM permits WHERE {IN_SCOPE_PERMIT_FILTER_SQL}", engine
    )

    if args.backend == "rules":
        from homeiq_ingest.classify_rules import classify_batch
        already = already_classified(engine, source="rules")
        todo = permits[~permits["permitnum"].isin(already)]
        print(f"{len(already)} already classified, {len(todo)} to go.")
        if todo.empty:
            return
        result = classify_batch(todo)
        save_classifications(engine, result)
        print(f"Classified {len(result)} permits with the rules backend.")
        return

    from homeiq_ingest.classify_llm import PROMPT_VERSION, classify_batch
    already = already_classified(engine, source="llm", prompt_version=PROMPT_VERSION)
    todo = permits[~permits["permitnum"].isin(already)]
    limit = args.limit if args.limit is not None else DEFAULT_LLM_LIMIT
    print(f"{len(already)} already classified at prompt_version={PROMPT_VERSION}. "
          f"Classifying up to {limit} more (of {len(todo)} remaining) — this calls a paid API.")
    if todo.empty:
        return
    result = classify_batch(todo, limit=limit)
    save_classifications(engine, result)
    print(f"Classified {len(result)} permits with the LLM backend.")


if __name__ == "__main__":
    main()
