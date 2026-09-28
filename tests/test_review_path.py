from homeiq_ingest.review_path import FIELD_INSPECTION, PLAN_REVIEW, UNKNOWN, derive_review_path


def test_stfi_variants_detected():
    for desc in [
        "Kitchen remodel subject to field inspection (STFI).",
        "Reroof, like for like, subject-to-field inspection.",
        "Interior alterations  STFI.",
    ]:
        assert derive_review_path(desc) == FIELD_INSPECTION


def test_non_stfi_defaults_to_plan_review():
    assert derive_review_path("Full kitchen remodel, mid-range finishes, per plan") == PLAN_REVIEW


def test_empty_description_is_unknown():
    assert derive_review_path("") == UNKNOWN
    assert derive_review_path(None) == UNKNOWN
