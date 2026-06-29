import shutil
from pathlib import Path

from src.csv_loader import _build_aggregate_source_file_label as build_raw_label
from src.csv_loader import _get_common_parent as get_raw_common_parent
from src.csv_loader import _resolve_source_files as resolve_raw_source_files
from src.generate_multifile_raw_pipeline_config import build_pipeline_config, extract_entity_name
from src.preload_profiler import _build_aggregate_source_file_label as build_profile_label
from src.preload_profiler import _get_common_parent as get_profile_common_parent
from src.preload_profiler import _merge_column_profiles
from src.preload_profiler import profile_csv_file
from src.preload_profiler import _resolve_source_files as resolve_profile_source_files


def test_resolve_source_files_supports_single_and_multiple_entries() -> None:
    single_path = r"C:\data\entity.csv"
    multi_paths = [r"C:\data\b.csv", r"C:\data\a.csv"]

    assert resolve_raw_source_files({"source_file": single_path}) == [Path(single_path)]
    assert resolve_profile_source_files({"source_file": single_path}) == [Path(single_path)]
    assert resolve_raw_source_files({"source_files": multi_paths}) == [
        Path(r"C:\data\a.csv"),
        Path(r"C:\data\b.csv"),
    ]
    assert resolve_profile_source_files({"source_files": multi_paths}) == [
        Path(r"C:\data\a.csv"),
        Path(r"C:\data\b.csv"),
    ]


def test_common_parent_and_label_use_shared_folder_for_multifile() -> None:
    source_files = [
        Path(r"C:\data\ClientSystem\BatchA\VOUCHER_1.csv"),
        Path(r"C:\data\ClientSystem\BatchB\VOUCHER_2.csv"),
    ]

    expected_parent = Path(r"C:\data\ClientSystem")
    assert get_raw_common_parent(source_files) == expected_parent
    assert get_profile_common_parent(source_files) == expected_parent
    assert build_raw_label(source_files) == "MULTI_FILE::2 files::C:\\data\\ClientSystem"
    assert build_profile_label(source_files) == "MULTI_FILE::2 files::C:\\data\\ClientSystem"


def test_merge_column_profiles_accumulates_counts_and_extremes() -> None:
    aggregate_profile = {
        "BUSINESS_UNIT": {
            "ordinal_position": 1,
            "minimum_non_null_value": "BAY10",
            "maximum_non_null_value": "BAY20",
            "max_string_length": 5,
            "blank_row_count": 1,
            "non_null_row_count": 3,
            "nullable_flag": 1,
            "nullability": "NULL",
            "defined_sql_type": "VARCHAR(100)",
        }
    }
    next_profile = {
        "BUSINESS_UNIT": {
            "ordinal_position": 1,
            "minimum_non_null_value": "BAY05",
            "maximum_non_null_value": "BAY30",
            "max_string_length": 7,
            "blank_row_count": 0,
            "non_null_row_count": 2,
            "nullable_flag": 0,
            "nullability": "NOT NULL",
            "defined_sql_type": "VARCHAR(100)",
        }
    }

    _merge_column_profiles(aggregate_profile, next_profile, Path(r"C:\data\VOUCHER.csv"))

    merged = aggregate_profile["BUSINESS_UNIT"]
    assert merged["blank_row_count"] == 1
    assert merged["non_null_row_count"] == 5
    assert merged["minimum_non_null_value"] == "BAY05"
    assert merged["maximum_non_null_value"] == "BAY30"
    assert merged["max_string_length"] == 7


def test_extract_entity_name_uses_prefix_before_date_ranges() -> None:
    assert extract_entity_name("VOUCHER_LINE_01012024_06302024_1.csv") == "VOUCHER_LINE"
    assert extract_entity_name("PAYMENT_TBL_01012022_09302024.csv") == "PAYMENT_TBL"
    assert extract_entity_name("custom_name.csv") == "custom_name"
    assert (
        extract_entity_name(
            "2024 BSEG Complete.csv",
            strip_prefix_regex=r"^\d{4}\s+",
            strip_suffix_regex=r"\s+Complete$",
        )
        == "BSEG"
    )


def test_build_pipeline_config_groups_duplicate_entities_across_folders() -> None:
    inbound_root = Path("tmp") / "test_multifile_ingest"
    if inbound_root.exists():
        shutil.rmtree(inbound_root)
    folder_a = inbound_root / "PCR Audit Data"
    folder_b = inbound_root / "PCR Audit Data 01202025"
    folder_a.mkdir(parents=True)
    folder_b.mkdir(parents=True)

    header = "BUSINESS_UNIT,VOUCHER_ID\nA,1\n"
    (folder_a / "VOUCHER_01012024_09302024.csv").write_text(header, encoding="utf-8")
    (folder_b / "VOUCHER_01012024_09302024.csv").write_text(header, encoding="utf-8")
    (folder_a / "VENDOR_01012022_09302024.csv").write_text(
        "SETID,VENDOR_ID\nSHARE,100\n",
        encoding="utf-8",
    )

    try:
        config = build_pipeline_config(inbound_root)

        assert sorted(config.keys()) == ["VENDOR", "VOUCHER"]
        assert config["VOUCHER"]["source_table"] == "raw.VOUCHER"
        assert len(config["VOUCHER"]["preload_profile"]["source_files"]) == 2
        assert config["VOUCHER"]["preload_profile"]["column_mappings"] == {
            "BUSINESS_UNIT": "BUSINESS_UNIT",
            "VOUCHER_ID": "VOUCHER_ID",
        }
    finally:
        if inbound_root.exists():
            shutil.rmtree(inbound_root)


def test_build_pipeline_config_can_split_schema_variants_and_canonicalize_headers() -> None:
    inbound_root = Path("tmp") / "test_variant_ingest"
    if inbound_root.exists():
        shutil.rmtree(inbound_root)
    inbound_root.mkdir(parents=True)

    (inbound_root / "2023 VENDOR.csv").write_text(
        "Doc. Date,,Amount,Amount\n2024-01-01,x,10,20\n",
        encoding="utf-8",
    )
    (inbound_root / "2024 VENDOR.csv").write_text(
        "Doc. Date,,Amount,Amount,Extra\n2024-01-01,x,10,20,y\n",
        encoding="utf-8",
    )

    try:
        config = build_pipeline_config(
            inbound_root,
            strip_prefix_regex=r"^\d{4}\s+",
            split_schema_variants=True,
            canonicalize_headers=True,
        )

        assert sorted(config) == ["VENDOR_V01", "VENDOR_V02"]
        first_profile = config["VENDOR_V01"]["preload_profile"]
        assert first_profile["canonicalize_headers"] is True
        assert first_profile["column_mappings"] == {
            "Doc_Date": "Doc_Date",
            "Column_002": "Column_002",
            "Amount": "Amount",
            "Amount_2": "Amount_2",
        }
    finally:
        if inbound_root.exists():
            shutil.rmtree(inbound_root)


def test_profile_csv_file_can_canonicalize_duplicate_headers(tmp_path: Path) -> None:
    source_file = tmp_path / "sap.csv"
    source_file.write_text("Amount,,Amount\n10,x,20\n", encoding="utf-8")

    profile = profile_csv_file(
        {
            "source_file": str(source_file),
            "column_mappings": {
                "Amount": "Amount",
                "Amount_2": "Amount_2",
                "Column_003": "Column_003",
            },
            "max_malformed_rows": 0,
            "has_header": True,
            "delimiter": ",",
            "canonicalize_headers": True,
        }
    )

    assert profile["row_count"] == 1
    assert list(profile["column_length_profile"]) == ["Amount", "Column_002", "Amount_2"]


def test_profile_csv_file_can_skip_report_preamble_to_header(tmp_path: Path) -> None:
    source_file = tmp_path / "workday_report.csv"
    source_file.write_text(
        "Report Title,,\nFilter,Active,\nTransaction,Payment Date,Amount\nACH-1,2026-01-01,10\n",
        encoding="utf-8",
    )

    profile = profile_csv_file(
        {
            "source_file": str(source_file),
            "column_mappings": {
                "Transaction": "Transaction",
                "Payment_Date": "Payment_Date",
                "Amount": "Amount",
            },
            "max_malformed_rows": 0,
            "has_header": True,
            "delimiter": ",",
            "canonicalize_headers": True,
            "header_row_number": 3,
        }
    )

    assert profile["row_count"] == 1
    assert profile["malformed_row_count"] == 0
    assert list(profile["column_length_profile"]) == [
        "Transaction",
        "Payment_Date",
        "Amount",
    ]
