"""Few-shot LLM classification against the project taxonomy (spec §5),
with a strict JSON schema and disk/DB caching by permit number.

This is the classifier the spec actually wants shipped, gated on hitting
per-class F1 >= taxonomy.MIN_SHIPPABLE_F1 against the hand-labeled sample
(see sampling.py / validate.py). classify_rules.py is the zero-cost
fallback used everywhere this isn't available yet.

Requires OPENAI_API_KEY. The spec's own example schema is written in
Claude's tool-call format; this implements the equivalent contract against
OpenAI's structured outputs (response_format=json_schema, strict mode) since
that's the key available in this environment — the taxonomy, prompt, and
caching layer are provider-agnostic and don't need to change to swap that.
"""

from __future__ import annotations

import json
import logging
import os

import pandas as pd
from openai import OpenAI
from tqdm import tqdm

from homeiq_ingest.taxonomy import PROJECT_CLASSES, SCOPE_FLAGS

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("OPENAI_CLASSIFIER_MODEL", "gpt-4o-mini")

# Bump this whenever the prompt or schema changes meaningfully enough that
# cached results should not be reused — it's part of the cache key.
PROMPT_VERSION = "v2"

_SYSTEM_PROMPT = f"""You classify Seattle building permit descriptions into a fixed \
renovation project taxonomy for a home-value estimation tool.

Classes: {", ".join(PROJECT_CLASSES)}
Scope flags (apply any that are true, zero or more): {", ".join(SCOPE_FLAGS)}

Rules:
- Pick exactly one project_class. Use "whole_house" only when multiple major \
systems are being renovated together (e.g. kitchen + bath + roof in one permit), \
not just because a permit mentions two rooms.
- Use "not_residential" only if the permit is clearly not for a residential \
property despite being in this dataset (e.g. a commercial tenant improvement).
- "structural_work" covers both seismic retrofit AND foundation repair \
(helical piles, push piers, underpinning, foundation cracking/settlement) — \
these are grouped together as one bucket, not split.
- Use "other" when the description doesn't clearly match any specific class \
rather than guessing.
- confidence is your calibrated probability (0-1) that project_class is correct.

Examples:
description: "Full kitchen remodel, mid-range finishes, keeping current layout"
-> {{"project_class": "kitchen_remodel", "confidence": 0.95, "scope_flags": []}}

description: "Construct new detached accessory dwelling unit (DADU) over garage per plans"
-> {{"project_class": "adu_detached", "confidence": 0.95, "scope_flags": ["structural", "sqft_added"]}}

description: "Reconfigure kitchen layout, relocate sink plumbing, remove wall between kitchen and dining"
-> {{"project_class": "kitchen_remodel", "confidence": 0.9, "scope_flags": ["layout_change", "plumbing_moved", "structural"]}}

description: "Complete remodel of entire house including kitchen, both bathrooms, and new roof"
-> {{"project_class": "whole_house", "confidence": 0.85, "scope_flags": ["layout_change", "structural"]}}

description: "Reroof single family residence, like for like"
-> {{"project_class": "roof", "confidence": 0.95, "scope_flags": []}}

description: "Construct foundation repairs to include helical piles for single-family residence per plan"
-> {{"project_class": "structural_work", "confidence": 0.9, "scope_flags": ["structural"]}}
"""

_JSON_SCHEMA = {
    "name": "permit_classification",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "project_class": {"type": "string", "enum": PROJECT_CLASSES},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "scope_flags": {
                "type": "array",
                "items": {"type": "string", "enum": SCOPE_FLAGS},
            },
        },
        "required": ["project_class", "confidence", "scope_flags"],
        "additionalProperties": False,
    },
}


class LLMClassifier:
    def __init__(self, model: str = DEFAULT_MODEL, client: OpenAI | None = None):
        if client is None and not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY not set. Add it to .env before running LLM classification "
                "(homeiq_ingest.classify_rules.classify_batch works with no key as a fallback)."
            )
        self.model = model
        self.client = client or OpenAI()

    def classify_one(self, description: str) -> dict:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f'description: "{description or ""}"'},
            ],
            response_format={"type": "json_schema", "json_schema": _JSON_SCHEMA},
            temperature=0,
        )
        return json.loads(response.choices[0].message.content)


def classify_batch(
    permits: pd.DataFrame, classifier: LLMClassifier | None = None, limit: int | None = None,
) -> pd.DataFrame:
    """permits needs `permitnum`, `description`. Returns one row per permit
    actually classified (respects `limit`, for cost control on first runs)."""
    classifier = classifier or LLMClassifier()
    rows = permits if limit is None else permits.head(limit)

    records = []
    for row in tqdm(rows.itertuples(index=False), total=len(rows), desc="LLM classifying"):
        try:
            result = classifier.classify_one(row.description)
        except Exception:
            logger.exception("LLM classification failed for permit %s", row.permitnum)
            continue
        records.append({
            "permitnum": row.permitnum, "source": "llm", "prompt_version": PROMPT_VERSION, **result,
        })
    return pd.DataFrame(records)
