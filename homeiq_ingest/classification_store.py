"""Cache classifier output by permit number in Postgres (spec §5: "batched
and cached by permit number") so a partial or interrupted classification run
— particularly relevant for the paid LLM backend — never re-pays for a
permit it already classified."""

from __future__ import annotations

import pandas as pd
from sqlalchemy import Engine, text


def already_classified(engine: Engine, source: str, prompt_version: str = "") -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT permitnum FROM permit_classification "
                "WHERE source = :source AND prompt_version = :prompt_version"
            ),
            {"source": source, "prompt_version": prompt_version},
        ).all()
    return {r[0] for r in rows}


def save_classifications(engine: Engine, classifications: pd.DataFrame) -> None:
    if classifications.empty:
        return
    df = classifications.copy()
    if "prompt_version" not in df.columns:
        df["prompt_version"] = ""
    # psycopg2 needs Python lists (not numpy arrays) to bind to a TEXT[] column.
    df["scope_flags"] = df["scope_flags"].apply(list)
    df[["permitnum", "source", "prompt_version", "project_class", "confidence", "scope_flags"]].to_sql(
        "permit_classification", engine, if_exists="append", index=False, method="multi", chunksize=500,
    )


def load_classifications(engine: Engine, source: str, prompt_version: str = "") -> pd.DataFrame:
    query = text(
        "SELECT permitnum, project_class, confidence, scope_flags FROM permit_classification "
        "WHERE source = :source AND prompt_version = :prompt_version"
    )
    with engine.connect() as conn:
        return pd.read_sql(query, conn, params={"source": source, "prompt_version": prompt_version})
