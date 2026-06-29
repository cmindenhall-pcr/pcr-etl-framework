from src.harmonized_stg_loader import (
    _build_union_columns,
    build_create_harmonized_staging_sql,
    build_insert_variant_sql,
)


def test_build_create_harmonized_staging_sql_adds_autoid_last() -> None:
    sql = build_create_harmonized_staging_sql(
        staging_table="stg.BSAK",
        union_columns=["CoCd", "Supplier"],
    )

    assert "[CoCd] VARCHAR(4000) NULL" in sql
    assert "[SourceVariant] VARCHAR(128) NOT NULL" in sql
    assert "[SFileName] VARCHAR(255) NOT NULL" in sql
    assert sql.index("[SFileName] VARCHAR(255) NOT NULL") < sql.index(
        "[AutoId] INT IDENTITY(1,1) NOT NULL"
    )


def test_build_insert_variant_sql_fills_missing_columns_with_null() -> None:
    sql = build_insert_variant_sql(
        staging_table="stg.BSAK",
        variant={"pipeline_name": "BSAK_V01", "source_table": "raw.BSAK_V01"},
        union_columns=["CoCd", "Supplier", "VariantOnly"],
        column_types=None,
        source_column_map={"CoCd": "CoCd", "Supplier": "Supplier"},
    )

    assert "CAST(NULLIF(LTRIM(RTRIM([CoCd])), '') AS VARCHAR(4000)) AS [CoCd]" in sql
    assert "CAST(NULL AS VARCHAR(4000)) AS [VariantOnly]" in sql
    assert "CAST('BSAK_V01' AS VARCHAR(128)) AS [SourceVariant]" in sql
    assert "[LoadDate] AS [LoadDate]" in sql
    assert "[SFileName] AS [SFileName]" in sql
    assert "[AutoId]" not in sql


def test_build_union_columns_drops_empty_generated_blank_header_columns() -> None:
    variants = [
        {"pipeline_name": "BSAK_V01", "source_table": "raw.BSAK_V01"},
        {"pipeline_name": "BSAK_V02", "source_table": "raw.BSAK_V02"},
    ]
    profile_rows = {
        "raw.BSAK_V01": [
            {"column_name": "CoCd", "ordinal_position": 1, "non_null_row_count": 10},
            {"column_name": "Column_004", "ordinal_position": 4, "non_null_row_count": 0},
            {"column_name": "Column_031", "ordinal_position": 31, "non_null_row_count": 1},
        ],
        "raw.BSAK_V02": [
            {"column_name": "CoCd", "ordinal_position": 1, "non_null_row_count": 20},
            {"column_name": "Column_004", "ordinal_position": 4, "non_null_row_count": 0},
            {"column_name": "Column_031", "ordinal_position": 31, "non_null_row_count": 0},
        ],
    }

    assert _build_union_columns(variants, profile_rows) == ["CoCd", "Column_031"]


def test_harmonized_loader_normalizes_spaces_and_removes_dots() -> None:
    variants = [{"pipeline_name": "EKKO_V01", "source_table": "raw.EKKO_V01"}]
    profile_rows = {
        "raw.EKKO_V01": [
            {"column_name": "Pur. Doc.", "ordinal_position": 1, "non_null_row_count": 10},
            {"column_name": "Doc Date", "ordinal_position": 2, "non_null_row_count": 10},
        ],
    }

    assert _build_union_columns(variants, profile_rows) == ["Pur_Doc", "Doc_Date"]

    sql = build_insert_variant_sql(
        staging_table="hrm.EKKO",
        variant={"pipeline_name": "EKKO_V01", "source_table": "raw.EKKO_V01"},
        union_columns=["Pur_Doc", "Doc_Date"],
        column_types=None,
        source_column_map={"Pur_Doc": "Pur. Doc.", "Doc_Date": "Doc Date"},
    )

    assert "LTRIM(RTRIM([Pur. Doc.]))" in sql
    assert "AS [Pur_Doc]" in sql
    assert "LTRIM(RTRIM([Doc Date]))" in sql
    assert "AS [Doc_Date]" in sql
