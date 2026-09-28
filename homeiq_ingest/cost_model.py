"""Quantile cost model (spec §6): P25/P50/P75 EstProjectCost per project
class, fit on a temporal train/test split (train <= 2023, test >= 2024 —
spec is explicit that a random split "will flatter you and mean nothing").

HistGradientBoostingRegressor handles the ~20% missing-ResBldg-characteristic
rows natively (native NaN support) rather than needing an imputer that could
quietly bias sparse classes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from homeiq_ingest.features import CATEGORICAL_FEATURE_COLUMNS, FEATURE_COLUMNS

MIN_SAMPLE_SIZE = 30
QUANTILES = (0.25, 0.5, 0.75)
TRAIN_CUTOFF_YEAR = 2023


@dataclass
class ClassCostModel:
    project_class: str
    n_train: int
    n_test: int
    models: dict[float, HistGradientBoostingRegressor]
    categories: dict[str, list[str]]  # per categorical column, its training-time category list
    pinball_loss: dict[float, float] = field(default_factory=dict)
    coverage: float | None = None


def _prepare_X(df: pd.DataFrame, categories: dict[str, list[str]]) -> pd.DataFrame:
    X = df[FEATURE_COLUMNS].copy()
    for col, cats in categories.items():
        X[col] = pd.Categorical(X[col], categories=cats)
    for col in FEATURE_COLUMNS:
        if col not in CATEGORICAL_FEATURE_COLUMNS and X[col].dtype == bool:
            X[col] = X[col].astype(float)
    return X


def _pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, quantile: float) -> float:
    diff = y_true - y_pred
    return float(np.mean(np.maximum(quantile * diff, (quantile - 1) * diff)))


def fit_all_class_models(training_df: pd.DataFrame, min_n: int = MIN_SAMPLE_SIZE) -> dict[str, ClassCostModel]:
    results: dict[str, ClassCostModel] = {}
    for project_class, group in training_df.groupby("project_class"):
        if len(group) < min_n:
            continue  # spec §6: fall back to Zonda benchmark instead — see cost_model.estimate()

        train = group[group["permit_year"] <= TRAIN_CUTOFF_YEAR]
        test = group[group["permit_year"] > TRAIN_CUTOFF_YEAR]
        if len(train) < min_n:
            continue

        categories = {col: sorted(group[col].dropna().unique()) for col in CATEGORICAL_FEATURE_COLUMNS}
        X_train = _prepare_X(train, categories)
        y_train = train["cost_deflated"].values

        models = {}
        for q in QUANTILES:
            model = HistGradientBoostingRegressor(
                loss="quantile", quantile=q, categorical_features=CATEGORICAL_FEATURE_COLUMNS,
                max_iter=200, random_state=0,
            )
            model.fit(X_train, y_train)
            models[q] = model

        result = ClassCostModel(
            project_class=project_class, n_train=len(train), n_test=len(test),
            models=models, categories=categories,
        )

        if len(test) > 0:
            X_test = _prepare_X(test, categories)
            y_test = test["cost_deflated"].values
            preds = {q: models[q].predict(X_test) for q in QUANTILES}
            result.pinball_loss = {q: _pinball_loss(y_test, preds[q], q) for q in QUANTILES}
            in_band = (y_test >= preds[0.25]) & (y_test <= preds[0.75])
            result.coverage = float(in_band.mean())

        results[project_class] = result

    return results


@dataclass
class CostEstimate:
    p25: float | None
    p50: float | None
    p75: float | None
    n_comparables: int
    source: str  # "model" | "insufficient_data"


def estimate(class_model: ClassCostModel | None, pin_features: dict) -> CostEstimate:
    if class_model is None:
        return CostEstimate(p25=None, p50=None, p75=None, n_comparables=0, source="insufficient_data")

    row = {col: pin_features.get(col) for col in FEATURE_COLUMNS}
    X = _prepare_X(pd.DataFrame([row]), class_model.categories)
    preds = {q: float(class_model.models[q].predict(X)[0]) for q in QUANTILES}
    return CostEstimate(
        p25=preds[0.25], p50=preds[0.5], p75=preds[0.75],
        n_comparables=class_model.n_train, source="model",
    )
