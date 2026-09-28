from homeiq_ingest.classify_rules import classify_description
from homeiq_ingest.taxonomy import PROJECT_CLASSES, SCOPE_FLAGS


def test_kitchen_remodel():
    r = classify_description("Full kitchen remodel, mid-range finishes, keeping current layout")
    assert r["project_class"] == "kitchen_remodel"


def test_dadu_with_new_unit():
    r = classify_description("Construct new detached accessory dwelling unit (DADU) over garage", housingunitsadded=1)
    assert r["project_class"] == "adu_detached"
    assert "sqft_added" in r["scope_flags"]


def test_garage_conversion():
    r = classify_description("Convert existing garage to living space per plan")
    assert r["project_class"] == "garage_conversion"


def test_whole_house_keyword():
    r = classify_description("Complete remodel of entire house including kitchen, baths, and roof")
    assert r["project_class"] == "whole_house"


def test_unmatched_falls_back_to_other():
    r = classify_description("Miscellaneous repair per plan")
    assert r["project_class"] == "other"
    assert r["confidence"] < 0.5


def test_output_schema_is_always_valid():
    for desc in ["kitchen", "bath and roof", "", "random text with no keywords", "seismic retrofit of foundation"]:
        r = classify_description(desc)
        assert r["project_class"] in PROJECT_CLASSES
        assert 0.0 <= r["confidence"] <= 1.0
        assert all(f in SCOPE_FLAGS for f in r["scope_flags"])
