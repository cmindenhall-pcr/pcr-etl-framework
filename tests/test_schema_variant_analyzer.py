from pathlib import Path

from src.schema_variant_analyzer import (
    analyze_schema_variants,
    build_canonical_column_names,
    extract_entity_name,
)


def test_extract_entity_name_uses_generic_prefix_and_suffix_rules() -> None:
    entity_name = extract_entity_name(
        "2024 BSEG Complete.csv",
        strip_prefix_regex=r"^\d{4}\s+",
        strip_suffix_regex=r"\s+Complete$",
    )

    assert entity_name == "BSEG"


def test_build_canonical_column_names_handles_blanks_duplicates_and_punctuation() -> None:
    columns = build_canonical_column_names(
        [
            "Doc. Date",
            "",
            "Amount",
            "Amount",
            "123 Code",
            "Pur. Doc.",
            "A.B",
            ".Name",
            "Name.",
        ]
    )

    assert columns == [
        "Doc_Date",
        "Column_002",
        "Amount",
        "Amount_2",
        "Column_123_Code",
        "Pur_Doc",
        "A_B",
        "Name",
        "Name_2",
    ]


def test_analyze_schema_variants_flags_column_count_drift(tmp_path: Path) -> None:
    (tmp_path / "2023 VENDOR.csv").write_text("A,B\n1,2\n", encoding="utf-8")
    (tmp_path / "2024 VENDOR.csv").write_text("A,B,C\n1,2,3\n", encoding="utf-8")

    report = analyze_schema_variants(
        folder=tmp_path,
        strip_prefix_regex=r"^\d{4}\s+",
        delimiter=",",
        client_name="TestClient",
    )

    vendor = report["entities"]["VENDOR"]
    assert vendor["file_count"] == 2
    assert vendor["variant_count"] == 2
    assert vendor["has_column_count_drift"] is True
    assert vendor["recommendation"] == "schema_variant_review_required"


def test_analyze_schema_variants_is_nonrecursive_by_default(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "ROOT.csv").write_text("A\n1\n", encoding="utf-8")
    (nested / "NESTED.csv").write_text("A\n1\n", encoding="utf-8")

    report = analyze_schema_variants(folder=tmp_path, delimiter=",")

    assert report["file_count"] == 1
    assert set(report["entities"]) == {"ROOT"}
