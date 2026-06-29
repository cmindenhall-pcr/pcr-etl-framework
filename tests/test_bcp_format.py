from src.bcp_format import (
    BCP_ROW_TERMINATOR,
    normalize_value_for_sql_type,
    serialize_rows_for_bcp,
)


def test_normalize_value_for_bit_sql_type() -> None:
    assert normalize_value_for_sql_type("Y", "BIT") == "1"
    assert normalize_value_for_sql_type("n", "BIT") == "0"


def test_normalize_value_for_decimal_zero_scale_sql_type() -> None:
    assert normalize_value_for_sql_type("24.000", "DECIMAL(18,0)") == "24"
    assert normalize_value_for_sql_type("100.00", "DECIMAL(18,0)") == "100"


def test_normalize_value_for_decimal_sql_type_accepts_accounting_currency() -> None:
    assert normalize_value_for_sql_type("$(1,234.56)", "DECIMAL(18,2)") == "-1234.56"
    assert normalize_value_for_sql_type("($1,234)", "DECIMAL(18,0)") == "-1234"


def test_serialize_rows_for_bcp_appends_metadata_and_crlf() -> None:
    columns = [
        {"column_name": "BATCH-OPTION", "final_sql_type": "BIT"},
        {"column_name": "INC-WH-PCT", "final_sql_type": "DECIMAL(18,0)"},
        {"column_name": "NAME", "final_sql_type": "VARCHAR(255)"},
    ]
    rows = [["Y", "24.000", "Example Health"]]

    rendered = serialize_rows_for_bcp(
        columns=columns,
        rows=rows,
        include_raw_metadata=True,
        load_date_text="2026-03-17 13:00:00",
        source_file_text=r"tmp\review_probe\apcompany.csv",
    )

    assert rendered == (
        "1\x1f24\x1fExample Health\x1f2026-03-17 13:00:00\x1ftmp\\review_probe\\apcompany.csv"
        + BCP_ROW_TERMINATOR
    )
