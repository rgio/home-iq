import pandas as pd

from homeiq_ingest.validate import validate_classifier


def test_validate_classifier_separates_shippable_from_collapsed():
    # kitchen_remodel: perfect predictions -> high F1, shippable.
    # roof: always predicted as "other" -> zero F1, collapses.
    labeled = pd.DataFrame({
        "human_project_class": ["kitchen_remodel"] * 10 + ["roof"] * 10,
        "predicted_class": ["kitchen_remodel"] * 10 + ["other"] * 10,
    })
    result = validate_classifier(labeled, predicted_col="predicted_class")
    assert "kitchen_remodel" in result.shippable_classes
    assert "roof" in result.collapsed_classes


def test_validate_classifier_ignores_unlabeled_rows():
    labeled = pd.DataFrame({
        "human_project_class": ["kitchen_remodel", "kitchen_remodel", ""],
        "predicted_class": ["kitchen_remodel", "kitchen_remodel", "roof"],
    })
    result = validate_classifier(labeled, predicted_col="predicted_class")
    assert result.macro_f1 == 1.0
