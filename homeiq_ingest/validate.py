"""Score a classifier against the hand-labeled sample (spec §5): per-class
precision/recall/F1, with the ship/collapse decision at MIN_SHIPPABLE_F1."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sklearn.metrics import classification_report

from homeiq_ingest.taxonomy import MIN_SHIPPABLE_F1


@dataclass
class ClassifierValidation:
    report: pd.DataFrame       # per-class precision/recall/f1/support
    macro_f1: float
    shippable_classes: list[str]
    collapsed_classes: list[str]


def validate_classifier(labeled: pd.DataFrame, predicted_col: str, label_col: str = "human_project_class") -> ClassifierValidation:
    """`labeled` needs `label_col` (ground truth) and `predicted_col`
    (the classifier's prediction) filled in for every row — i.e. the
    returned hand-labeling CSV, joined back to predictions if not already
    present as columns."""
    df = labeled[labeled[label_col].notna() & (labeled[label_col] != "")]
    if df.empty:
        raise ValueError(f"No rows have {label_col} filled in — nothing to validate against.")

    report_dict = classification_report(
        df[label_col], df[predicted_col], output_dict=True, zero_division=0,
    )
    report = pd.DataFrame(report_dict).T
    macro_f1 = report_dict["macro avg"]["f1-score"]

    per_class = report.drop(index=["accuracy", "macro avg", "weighted avg"], errors="ignore")
    shippable = per_class[per_class["f1-score"] >= MIN_SHIPPABLE_F1].index.tolist()
    collapsed = per_class[per_class["f1-score"] < MIN_SHIPPABLE_F1].index.tolist()

    return ClassifierValidation(
        report=report, macro_f1=macro_f1, shippable_classes=shippable, collapsed_classes=collapsed,
    )


def format_validation(v: ClassifierValidation) -> str:
    lines = [
        v.report.round(3).to_string(),
        "",
        f"Macro F1: {v.macro_f1:.3f}",
        f"Shippable classes (F1 >= {MIN_SHIPPABLE_F1}): {', '.join(v.shippable_classes) or '(none)'}",
        f"Collapse into 'other' (F1 < {MIN_SHIPPABLE_F1}): {', '.join(v.collapsed_classes) or '(none)'}",
    ]
    return "\n".join(lines)
