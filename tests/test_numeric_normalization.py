from src.numeric_normalization import normalize_numeric_candidate


def test_normalize_numeric_candidate_accepts_accounting_currency_and_commas() -> None:
    assert normalize_numeric_candidate("(1,234.56)") == "-1234.56"
    assert normalize_numeric_candidate("$(1,234.56)") == "-1234.56"
    assert normalize_numeric_candidate("($1,234.56)") == "-1234.56"
    assert normalize_numeric_candidate("$0.00") == "0.00"
    assert normalize_numeric_candidate("12,345,678") == "12345678"


def test_normalize_numeric_candidate_rejects_punctuation_text() -> None:
    assert normalize_numeric_candidate("Smith, John") is None
    assert normalize_numeric_candidate("(see attached)") is None
    assert normalize_numeric_candidate("PO-12345") is None
