from src.reviewed_stg_loader import (
    build_column_specs,
    build_insert_stg_sql,
    build_reviewed_output_columns,
)


def test_build_reviewed_output_columns_drops_unnamed_and_source_rename_columns() -> None:
    output_columns = build_reviewed_output_columns(
        hrm_columns=[
            "Amount_LC",
            "Amount_in_LC",
            "Column_031",
            "SourceVariant",
            "AutoId",
        ],
        drop_columns={"Column_031"},
        rename_pairs={"Amount_LC": "Amount_in_LC"},
    )

    assert output_columns == ["Amount_in_LC", "SourceVariant"]


def test_build_insert_stg_sql_coalesces_into_candidate_column() -> None:
    column_specs = [
        {"column_name": "Amount_in_LC", "sql_type": "MONEY", "nullability": "NOT NULL"},
        {"column_name": "SourceVariant", "sql_type": "VARCHAR(4000)", "nullability": "NOT NULL"},
    ]
    sql = build_insert_stg_sql(
        stg_table="stg.BSIK",
        hrm_table="hrm.BSIK",
        column_specs=column_specs,
        rename_pairs={"Amount_LC": "Amount_in_LC"},
    )

    assert "ISNULL(TRY_CAST(CASE WHEN REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(" in sql
    assert "NULLIF(LTRIM(RTRIM([Amount_LC])), ''))" in sql
    assert "), ',', ''), '$', '') LIKE '(%)' THEN '-'" in sql
    assert "CAST(0 AS MONEY)) AS [Amount_in_LC]" in sql
    assert "[SourceVariant] AS [SourceVariant]" in sql
    assert "[Amount_LC]" not in sql.split("INSERT INTO stg.BSIK", maxsplit=1)[1].split(
        "SELECT", maxsplit=1
    )[0]


def test_build_column_specs_uses_profile_types_and_stg_nullability() -> None:
    specs = build_column_specs(
        output_columns=["Doc_Date", "Amount_in_LC", "Supplier"],
        rename_pairs={"Amount_LC": "Amount_in_LC"},
        type_map={
            "Doc_Date": ["DATE"],
            "Amount_in_LC": ["MONEY"],
            "Amount_LC": ["MONEY"],
            "Supplier": ["VARCHAR(100)"],
        },
    )

    assert specs == [
        {"column_name": "Doc_Date", "sql_type": "DATE", "nullability": "NULL"},
        {"column_name": "Amount_in_LC", "sql_type": "MONEY", "nullability": "NOT NULL"},
        {"column_name": "Supplier", "sql_type": "VARCHAR(100)", "nullability": "NOT NULL"},
    ]


def test_build_insert_stg_sql_normalizes_identifier_zero_decimals() -> None:
    sql = build_insert_stg_sql(
        stg_table="stg.Vendor",
        hrm_table="hrm.Vendor",
        column_specs=[
            {"column_name": "Vendor", "sql_type": "VARCHAR(100)", "nullability": "NOT NULL"},
        ],
        rename_pairs={},
    )

    assert "WHEN NULLIF(LTRIM(RTRIM([Vendor])), '') LIKE '[0-9]%.%'" in sql
    assert "THEN LEFT(NULLIF(LTRIM(RTRIM([Vendor])), ''), CHARINDEX('.', NULLIF(LTRIM(RTRIM([Vendor])), '')) - 1)" in sql


def test_build_insert_stg_sql_nulls_1900_date_sentinel() -> None:
    sql = build_insert_stg_sql(
        stg_table="stg.Invoice",
        hrm_table="hrm.Invoice",
        column_specs=[
            {"column_name": "Invoice_Date", "sql_type": "DATE", "nullability": "NULL"},
        ],
        rename_pairs={},
    )

    assert "WHEN TRY_CAST(NULLIF(LTRIM(RTRIM([Invoice_Date])), '') AS DATE) = CAST('19000101' AS DATE) THEN NULL" in sql
    assert "ELSE TRY_CAST(NULLIF(LTRIM(RTRIM([Invoice_Date])), '') AS DATE)" in sql
