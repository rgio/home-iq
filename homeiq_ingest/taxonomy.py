"""Project taxonomy (spec §5): canonical classes and scope flags every
classifier implementation (rules-based or LLM) must emit.

One deviation from the spec's literal 14-class list: `seismic_retrofit` is
renamed `structural_work` and broadened to also cover foundation repair
(helical piles, push piers, underpinning) — reading a sample of what the
rules classifier was dumping into `other` found ~576 of these, a real,
economically distinct category with no other home in the taxonomy, and
close enough to seismic work (both are foundation/structural) to share a
bucket rather than add an entirely new one. See README's taxonomy-validation
section for how this was found.
"""

from __future__ import annotations

PROJECT_CLASSES = [
    "kitchen_remodel",
    "bath_remodel",
    "addition_sqft",
    "adu_attached",
    "adu_detached",
    "basement_finish",
    "garage_conversion",
    "deck_porch",
    "roof",
    "siding_windows",
    "structural_work",
    "whole_house",
    "other",
    "not_residential",
]

SCOPE_FLAGS = [
    "layout_change",
    "structural",
    "plumbing_moved",
    "sqft_added",
]

# Classifier outputs below this per-class F1 (spec §5) collapse into "other"
# once validated against the hand-labeled sample — see homeiq_ingest.validate.
MIN_SHIPPABLE_F1 = 0.80
