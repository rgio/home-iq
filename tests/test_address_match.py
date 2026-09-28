from homeiq_ingest.address_match import (
    ZipAddressIndex,
    build_resbldg_address,
    normalize_address,
)


def test_normalize_address_handles_directional_and_suffix_variants():
    a = normalize_address("1550 North 115th Street")
    b = normalize_address("1550 N 115TH ST")
    assert a == b


def test_normalize_address_strips_trailing_zip():
    assert normalize_address("9822 37TH AVE SW 98126") == normalize_address("9822 37TH AVE SW")


def test_build_resbldg_address_matches_permit_normalization():
    resbldg = build_resbldg_address("10805", "", "11TH", "AVE", "NE")
    permit = normalize_address("10805 11TH AVE NE")
    assert resbldg == permit


def test_zip_index_exact_match_scores_100():
    idx = ZipAddressIndex()
    idx.add("98125", "123456", "0010", build_resbldg_address("10805", "", "11TH", "AVE", "NE"))
    match = idx.match(normalize_address("10805 11TH AVE NE"), "98125")
    assert match is not None
    assert match.major == "123456"
    assert match.score == 100.0


def test_zip_index_rejects_below_threshold():
    idx = ZipAddressIndex()
    idx.add("98125", "123456", "0010", build_resbldg_address("10805", "", "11TH", "AVE", "NE"))
    # Wrong street entirely — should not match even loosely.
    match = idx.match(normalize_address("450 Boren Ave N"), "98125", threshold=90)
    assert match is None


def test_zip_index_ignores_candidates_outside_zip():
    idx = ZipAddressIndex()
    idx.add("98125", "123456", "0010", build_resbldg_address("10805", "", "11TH", "AVE", "NE"))
    match = idx.match(normalize_address("10805 11TH AVE NE"), "98126")
    assert match is None
