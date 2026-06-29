from src.identifier_normalization import is_identifier_like_column


def test_identifier_like_columns_include_camelcase_codes_and_counts() -> None:
    assert is_identifier_like_column("Company")
    assert is_identifier_like_column("PaidVendor")
    assert is_identifier_like_column("BaseCurrencyNumberOfDecimals")
    assert is_identifier_like_column("Status")
    assert is_identifier_like_column("TransactionNumber")
    assert is_identifier_like_column("TransactionIDNumber")
    assert is_identifier_like_column("PurchaseOrder")
    assert is_identifier_like_column("Buyer")


def test_identifier_like_columns_do_not_include_plain_descriptive_names() -> None:
    assert not is_identifier_like_column("PaidName")
    assert not is_identifier_like_column("Description")
