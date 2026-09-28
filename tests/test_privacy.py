import pandas as pd

from homeiq_ingest.privacy import strip_pii, to_public_safe


def test_strip_pii_drops_buyer_and_seller_name():
    df = pd.DataFrame({
        "Major": ["1"], "Minor": ["2"],
        "SellerName": ["ANDERSON SUSAN"], "BuyerName": ["FLOYD LEONARD"],
        "SalePrice": [103500],
    })
    out = strip_pii(df)
    assert "SellerName" not in out.columns
    assert "BuyerName" not in out.columns
    assert "SalePrice" in out.columns


def test_to_public_safe_also_drops_address():
    df = pd.DataFrame({
        "Major": ["1"], "originaladdress1": ["1550 N 115TH ST"], "estprojectcost": [3000],
    })
    out = to_public_safe(df, address_columns=["originaladdress1"])
    assert "originaladdress1" not in out.columns
    assert "estprojectcost" in out.columns
