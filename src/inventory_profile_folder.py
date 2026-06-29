import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from src.schema_inference import compare_review_artifacts, initialize_review_artifact

SNIFFER_SAMPLE_BYTES = 16384
ENCODING_CANDIDATES = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
SNIFFER_DELIMITERS = [",", "|", "\t", ";"]
HIGH_BLANK_RATIO_THRESHOLD = 0.5
OVERSIZED_TEXT_THRESHOLD = 255
DQI_SAMPLE_LIMIT = 5


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inventory and profile CSV files in a folder for onboarding."
    )
    parser.add_argument("folder", help="Folder containing inbound CSV files")
    parser.add_argument(
        "--delimiter",
        default=None,
        help="Optional delimiter override. When omitted, the tool attempts to sniff the delimiter.",
    )
    parser.add_argument(
        "--sample-rows",
        type=int,
        default=5,
        help="Number of sample data rows to capture per file. Default is 5.",
    )
    parser.add_argument(
        "--sniffer-sample-bytes",
        type=int,
        default=SNIFFER_SAMPLE_BYTES,
        help="Number of bytes to sample when detecting encoding and dialect. Default is 16384.",
    )
    parser.add_argument(
        "--output",
        help="Optional path to write the profiling summary as JSON.",
    )
    parser.add_argument(
        "--infer-types",
        action="store_true",
        help="Infer SQL-oriented column types and include a schema review section in the output.",
    )
    parser.add_argument(
        "--compare-to",
        help="Optional path to a prior review artifact JSON file for drift comparison.",
    )
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")

    if not folder.is_dir():
        raise ValueError(f"Path is not a folder: {folder}")

    summaries = profile_folder(
        folder=folder,
        delimiter=args.delimiter,
        sample_rows=args.sample_rows,
        sniffer_sample_bytes=args.sniffer_sample_bytes,
        infer_types=args.infer_types,
    )

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "folder": str(folder),
        "file_count": len(summaries),
        "infer_types": args.infer_types,
        "files": summaries,
    }
    if args.infer_types:
        result = initialize_review_artifact(result)

    if args.compare_to:
        prior_artifact_path = Path(args.compare_to)
        if not prior_artifact_path.exists():
            raise FileNotFoundError(f"Prior review artifact not found: {prior_artifact_path}")
        prior_artifact = json.loads(prior_artifact_path.read_text(encoding="utf-8"))
        result["drift_summary"] = compare_review_artifacts(result, prior_artifact)

    output_text = json.dumps(result, indent=2)
    print(output_text)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(output_text, encoding="utf-8")


def profile_folder(
    folder: Path,
    delimiter: str | None,
    sample_rows: int,
    sniffer_sample_bytes: int = SNIFFER_SAMPLE_BYTES,
    infer_types: bool = False,
) -> list[dict]:
    summaries = []
    for file_path in sorted(folder.glob("*.csv")):
        summaries.append(
            profile_csv_file(
                file_path=file_path,
                delimiter=delimiter,
                sample_rows=sample_rows,
                sniffer_sample_bytes=sniffer_sample_bytes,
                infer_types=infer_types,
            )
        )
    return summaries


def profile_csv_file(
    file_path: Path,
    delimiter: str | None,
    sample_rows: int,
    sniffer_sample_bytes: int = SNIFFER_SAMPLE_BYTES,
    infer_types: bool = False,
) -> dict:
    stat = file_path.stat()
    detection = detect_file_settings(
        file_path=file_path,
        delimiter_override=delimiter,
        sample_bytes=sniffer_sample_bytes,
    )
    summary = {
        "file_name": file_path.name,
        "table_name": file_path.stem,
        "full_path": str(file_path),
        "size_bytes": stat.st_size,
        "last_modified": stat.st_mtime,
        "is_empty": stat.st_size == 0,
        "detected_encoding": detection["encoding"],
        "encoding_detection_mode": detection["encoding_mode"],
        "delimiter": detection["delimiter"],
        "delimiter_detection_mode": detection["delimiter_mode"],
        "dialect": detection["dialect"],
        "has_header": detection["has_header"],
        "column_count": 0,
        "columns": [],
        "duplicate_headers": [],
        "empty_headers": [],
        "data_row_count": 0,
        "malformed_row_count": 0,
        "blank_row_count": 0,
        "sample_rows": [],
        "data_quality_issues": [],
        "column_quality_issues": [],
    }

    if stat.st_size == 0:
        summary["status"] = "empty_file"
        return summary

    with open(file_path, newline="", encoding=detection["encoding"]) as f:
        reader = csv.reader(f, delimiter=detection["delimiter"])

        try:
            header = next(reader)
        except StopIteration:
            summary["status"] = "empty_file"
            return summary

        columns = [column.strip() for column in header]
        expected_column_count = len(columns)
        empty_headers = [
            f"column_{index + 1}"
            for index, column in enumerate(columns)
            if not column
        ]
        duplicate_headers = sorted(
            {
                column
                for column in columns
                if column and columns.count(column) > 1
            }
        )

        summary["columns"] = columns
        summary["column_count"] = expected_column_count
        summary["duplicate_headers"] = duplicate_headers
        summary["empty_headers"] = empty_headers
        column_profiles = {
            _profile_column_name(column_name, index): {
                "column_name": column_name,
                "max_string_length": None,
                "blank_row_count": 0,
                "non_null_row_count": 0,
                "contains_control_characters": False,
            }
            for index, column_name in enumerate(columns)
        }
        seen_row_signatures: set[tuple[str, ...]] = set()
        duplicate_data_row_count = 0

        for row in reader:
            if not any((value or "").strip() for value in row):
                summary["blank_row_count"] += 1
                continue

            if len(row) != expected_column_count:
                summary["malformed_row_count"] += 1
                continue

            summary["data_row_count"] += 1
            normalized_row = tuple((value or "").strip() for value in row)
            if normalized_row in seen_row_signatures:
                duplicate_data_row_count += 1
            else:
                seen_row_signatures.add(normalized_row)

            if len(summary["sample_rows"]) < sample_rows:
                summary["sample_rows"].append(row)

            for column_index, column_name in enumerate(columns):
                value = (row[column_index] or "").strip()
                profile = column_profiles[_profile_column_name(column_name, column_index)]
                if value == "":
                    profile["blank_row_count"] += 1
                    continue

                profile["non_null_row_count"] += 1
                current_max = profile["max_string_length"]
                value_length = len(value)
                if current_max is None or value_length > current_max:
                    profile["max_string_length"] = value_length
                if _contains_control_characters(value):
                    profile["contains_control_characters"] = True

    if duplicate_headers:
        summary["status"] = "warning"
        summary["status_reasons"] = ["duplicate_headers"]
    elif summary["malformed_row_count"] > 0:
        summary["status"] = "warning"
        summary["status_reasons"] = ["malformed_rows"]
    else:
        summary["status"] = "ok"
        summary["status_reasons"] = []

    dqi_result = _build_dqi_summary(
        columns=columns,
        column_profiles=column_profiles,
        data_row_count=summary["data_row_count"],
        duplicate_headers=duplicate_headers,
        empty_headers=empty_headers,
        malformed_row_count=summary["malformed_row_count"],
        duplicate_data_row_count=duplicate_data_row_count,
    )
    summary["data_quality_issues"] = dqi_result["file_issues"]
    summary["column_quality_issues"] = dqi_result["column_issues"]
    summary["duplicate_data_row_count"] = duplicate_data_row_count

    if dqi_result["has_warning"] and summary["status"] == "ok":
        summary["status"] = "warning"
    summary["status_reasons"] = sorted(set(summary["status_reasons"] + dqi_result["status_reasons"]))

    if infer_types:
        summary["schema_review"] = {
            "columns": [
                _build_fixed_schema_review_column(
                    column_name=column_name,
                    max_string_length=column_profiles[_profile_column_name(column_name, index)]["max_string_length"],
                    blank_row_count=column_profiles[_profile_column_name(column_name, index)]["blank_row_count"],
                    non_null_row_count=column_profiles[_profile_column_name(column_name, index)]["non_null_row_count"],
                )
                for index, column_name in enumerate(columns)
            ]
        }

    return summary


def detect_file_settings(
    file_path: Path,
    delimiter_override: str | None,
    sample_bytes: int = SNIFFER_SAMPLE_BYTES,
) -> dict[str, object]:
    raw_sample = file_path.read_bytes()[:sample_bytes]

    if not raw_sample:
        return {
            "encoding": "utf-8-sig",
            "encoding_mode": "default_empty_file",
            "delimiter": delimiter_override or ",",
            "delimiter_mode": "override" if delimiter_override else "default_empty_file",
            "has_header": True,
            "dialect": _dialect_summary(None, delimiter_override or ","),
        }

    encoding, encoding_mode = _detect_encoding(raw_sample)
    decoded_sample = raw_sample.decode(encoding, errors="replace")

    sniffed_dialect = None
    has_header = True
    delimiter = delimiter_override
    delimiter_mode = "override" if delimiter_override else "default"

    if delimiter_override is None:
        sniffed_dialect = _sniff_dialect(decoded_sample)
        if sniffed_dialect is not None:
            delimiter = sniffed_dialect.delimiter
            delimiter_mode = "sniffed"
        else:
            delimiter = ","
            delimiter_mode = "default"
    else:
        sniffed_dialect = _sniff_dialect(decoded_sample, preferred_delimiter=delimiter_override)

    if decoded_sample.strip():
        has_header = _safe_has_header(decoded_sample)

    return {
        "encoding": encoding,
        "encoding_mode": encoding_mode,
        "delimiter": delimiter,
        "delimiter_mode": delimiter_mode,
        "has_header": has_header,
        "dialect": _dialect_summary(sniffed_dialect, delimiter),
    }


def _detect_encoding(raw_sample: bytes) -> tuple[str, str]:
    if raw_sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", "bom"

    for encoding in ENCODING_CANDIDATES:
        try:
            raw_sample.decode(encoding)
            if encoding in {"utf-8-sig", "utf-8"}:
                return encoding, "strict_utf8"
            return encoding, "fallback_candidate"
        except UnicodeDecodeError:
            continue

    return "latin-1", "lossy_fallback"


def _sniff_dialect(
    decoded_sample: str,
    preferred_delimiter: str | None = None,
) -> csv.Dialect | None:
    if not decoded_sample.strip():
        return None

    delimiters = preferred_delimiter or "".join(SNIFFER_DELIMITERS)
    try:
        return csv.Sniffer().sniff(decoded_sample, delimiters=delimiters)
    except csv.Error:
        return None


def _safe_has_header(decoded_sample: str) -> bool:
    try:
        return csv.Sniffer().has_header(decoded_sample)
    except csv.Error:
        return True


def _dialect_summary(
    dialect: csv.Dialect | None,
    delimiter: str,
) -> dict[str, object]:
    if dialect is None:
        return {
            "delimiter": delimiter,
            "quotechar": '"',
            "doublequote": True,
            "escapechar": None,
            "skipinitialspace": False,
            "lineterminator": "\n",
            "quoting": csv.QUOTE_MINIMAL,
        }

    return {
        "delimiter": dialect.delimiter,
        "quotechar": dialect.quotechar,
        "doublequote": dialect.doublequote,
        "escapechar": dialect.escapechar,
        "skipinitialspace": dialect.skipinitialspace,
        "lineterminator": dialect.lineterminator,
        "quoting": dialect.quoting,
    }


def _build_dqi_summary(
    columns: list[str],
    column_profiles: dict[str, dict[str, object]],
    data_row_count: int,
    duplicate_headers: list[str],
    empty_headers: list[str],
    malformed_row_count: int,
    duplicate_data_row_count: int,
) -> dict[str, object]:
    file_issues = []
    column_issues = []
    status_reasons = []

    if empty_headers:
        file_issues.append(
            {
                "issue_type": "empty_headers",
                "severity": "warning",
                "message": f"{len(empty_headers)} empty header positions detected.",
                "sample_headers": empty_headers[:DQI_SAMPLE_LIMIT],
            }
        )
        status_reasons.append("empty_headers")

    if duplicate_headers:
        file_issues.append(
            {
                "issue_type": "duplicate_headers",
                "severity": "warning",
                "message": f"{len(duplicate_headers)} duplicate header names detected.",
                "sample_headers": duplicate_headers[:DQI_SAMPLE_LIMIT],
            }
        )
        status_reasons.append("duplicate_headers")

    if malformed_row_count:
        file_issues.append(
            {
                "issue_type": "malformed_rows",
                "severity": "warning",
                "message": f"{malformed_row_count} malformed rows detected.",
            }
        )
        status_reasons.append("malformed_rows")

    if duplicate_data_row_count:
        file_issues.append(
            {
                "issue_type": "duplicate_data_rows",
                "severity": "warning",
                "message": f"{duplicate_data_row_count} duplicate data rows detected.",
            }
        )
        status_reasons.append("duplicate_data_rows")

    for index, column_name in enumerate(columns):
        profile = column_profiles[_profile_column_name(column_name, index)]
        display_name = column_name or f"column_{index + 1}"
        non_null_row_count = int(profile["non_null_row_count"] or 0)
        blank_row_count = int(profile["blank_row_count"] or 0)
        total_observed_rows = non_null_row_count + blank_row_count
        blank_ratio = 0.0 if total_observed_rows == 0 else blank_row_count / total_observed_rows
        max_length = profile["max_string_length"]

        if total_observed_rows > 0 and blank_ratio >= HIGH_BLANK_RATIO_THRESHOLD:
            column_issues.append(
                {
                    "column_name": display_name,
                    "issue_type": "high_blank_ratio",
                    "severity": "warning",
                    "blank_ratio": round(blank_ratio, 4),
                    "message": (
                        f"Column '{display_name}' is blank in {blank_ratio:.1%} of observed rows."
                    ),
                }
            )
            status_reasons.append("high_blank_ratio")

        if max_length is not None and int(max_length) > OVERSIZED_TEXT_THRESHOLD:
            column_issues.append(
                {
                    "column_name": display_name,
                    "issue_type": "oversized_text",
                    "severity": "warning",
                    "max_string_length": int(max_length),
                    "message": (
                        f"Column '{display_name}' has values longer than {OVERSIZED_TEXT_THRESHOLD} characters."
                    ),
                }
            )
            status_reasons.append("oversized_text")

        if bool(profile.get("contains_control_characters")):
            column_issues.append(
                {
                    "column_name": display_name,
                    "issue_type": "control_characters",
                    "severity": "warning",
                    "message": f"Column '{display_name}' contains control characters in sampled values.",
                }
            )
            status_reasons.append("control_characters")

    return {
        "file_issues": file_issues,
        "column_issues": column_issues[: max(DQI_SAMPLE_LIMIT * 10, len(column_issues))],
        "status_reasons": status_reasons,
        "has_warning": bool(file_issues or column_issues),
    }


def _profile_column_name(column_name: str, index: int) -> str:
    return column_name if column_name else f"__blank_column_{index + 1}"


def _contains_control_characters(value: str) -> bool:
    return bool(re.search(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", value))


def _build_fixed_schema_review_column(
    column_name: str,
    max_string_length: int | None,
    blank_row_count: int,
    non_null_row_count: int,
) -> dict[str, int | float | str | None]:
    nullable_flag = 1 if blank_row_count > 0 else 0
    nullability = "NULL" if nullable_flag else "NOT NULL"
    return {
        "column_name": column_name,
        "defined_sql_type": "VARCHAR(4000)",
        "inferred_sql_type": "VARCHAR(4000)",
        "confidence_pct": 100.0,
        "matched_value_count": non_null_row_count,
        "non_null_row_count": non_null_row_count,
        "blank_row_count": blank_row_count,
        "max_string_length": max_string_length,
        "nullable_flag": nullable_flag,
        "nullability": nullability,
        "inference_reason": "fixed_varchar_4000_policy",
    }


if __name__ == "__main__":
    main()
