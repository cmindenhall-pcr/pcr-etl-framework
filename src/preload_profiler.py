import csv
import os
from pathlib import Path
from itertools import chain

from src.csv_row_repair import iter_repaired_csv_rows
from src.csv_utils import (
    build_canonical_column_names,
    build_ordinal_column_names,
    ensure_max_csv_field_size,
    trim_trailing_blank_header_columns,
    trim_trailing_blank_row_values,
)
from src.logger import get_logger

logger = get_logger()
ENCODING_CANDIDATES = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
ensure_max_csv_field_size()


def profile_csv_file(
    profile_config: dict,
    run_id: str | None = None,
    enforce_malformed_threshold: bool = True,
) -> dict:
    source_files = _resolve_source_files(profile_config)
    delimiter = profile_config["delimiter"]
    has_header = profile_config["has_header"]
    max_malformed_rows = profile_config.get("max_malformed_rows", 0)
    row_repair_strategy = profile_config.get("row_repair_strategy")
    canonicalize_headers = bool(profile_config.get("canonicalize_headers", False))
    header_row_number = int(profile_config.get("header_row_number", 1))
    prefix = f"[run_id={run_id}] " if run_id else ""

    aggregate_source_file = _build_aggregate_source_file_label(source_files)
    aggregate_source_folder = str(_get_common_parent(source_files))
    aggregate_profile: dict[str, object] | None = None
    malformed_threshold_exceeded = False
    duplicate_headers: set[str] = set()
    total_row_count = 0
    total_malformed_row_count = 0
    total_repaired_row_count = 0

    for source_file in source_files:
        single_profile = _profile_single_csv_file(
            source_file=source_file,
            delimiter=delimiter,
            has_header=has_header,
            max_malformed_rows=max_malformed_rows,
            row_repair_strategy=row_repair_strategy,
            canonicalize_headers=canonicalize_headers,
            header_row_number=header_row_number,
        )
        if aggregate_profile is None:
            aggregate_profile = single_profile
        else:
            _merge_column_profiles(
                aggregate_profile["column_length_profile"],
                single_profile["column_length_profile"],
                source_file,
            )

        total_row_count += int(single_profile["row_count"])
        total_malformed_row_count += int(single_profile["malformed_row_count"])
        total_repaired_row_count += int(single_profile["repaired_row_count"])
        duplicate_headers.update(single_profile["duplicate_headers"])
        malformed_threshold_exceeded = (
            malformed_threshold_exceeded
            or bool(single_profile["malformed_threshold_exceeded"])
        )

    if aggregate_profile is None or total_row_count == 0:
        raise ValueError(
            f"Pre-load profile failed: CSV files have no data rows: {aggregate_source_file}"
        )

    if malformed_threshold_exceeded and enforce_malformed_threshold:
        raise ValueError(
            f"Pre-load profile failed: malformed row count {total_malformed_row_count} exceeds "
            f"allowed threshold {max_malformed_rows} across configured source files."
        )

    if duplicate_headers:
        logger.warning(
            f"{prefix}Pre-load profile warning: duplicate headers found across configured "
            f"source files: {sorted(duplicate_headers)}"
        )

    if total_malformed_row_count:
        logger.warning(
            f"{prefix}Pre-load profile warning: malformed rows found across configured "
            f"source files: {total_malformed_row_count}"
        )

    return {
        "source_file": aggregate_source_file,
        "source_folder_path": aggregate_source_folder,
        "row_count": total_row_count,
        "malformed_row_count": total_malformed_row_count,
        "repaired_row_count": total_repaired_row_count,
        "max_malformed_rows": max_malformed_rows,
        "malformed_threshold_exceeded": malformed_threshold_exceeded,
        "duplicate_headers": sorted(duplicate_headers),
        "column_length_profile": aggregate_profile["column_length_profile"],
    }


def _profile_single_csv_file(
    source_file: Path,
    delimiter: str,
    has_header: bool,
    max_malformed_rows: int,
    row_repair_strategy: str | None,
    canonicalize_headers: bool,
    header_row_number: int,
) -> dict[str, object]:
    if not source_file.exists():
        raise FileNotFoundError(f"Pre-load profile failed: source file not found: {source_file}")

    candidate_encodings = _build_candidate_encodings(_detect_encoding(source_file))

    last_error: UnicodeDecodeError | None = None
    for encoding in candidate_encodings:
        try:
            with open(source_file, newline="", encoding=encoding) as f:
                return _profile_open_csv_reader(
                    source_file=source_file,
                    reader=csv.reader(f, delimiter=delimiter),
                    has_header=has_header,
                    max_malformed_rows=max_malformed_rows,
                    row_repair_strategy=row_repair_strategy,
                    canonicalize_headers=canonicalize_headers,
                    header_row_number=header_row_number,
                )
        except UnicodeDecodeError as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise ValueError(
            f"Pre-load profile failed: unable to decode {source_file} with supported encodings."
        ) from last_error

    raise ValueError(f"Pre-load profile failed: unable to profile {source_file}.")


def _profile_open_csv_reader(
    source_file: Path,
    reader,
    has_header: bool,
    max_malformed_rows: int,
    row_repair_strategy: str | None,
    canonicalize_headers: bool,
    header_row_number: int,
) -> dict[str, object]:
    try:
        first_row = _read_header_row(reader, header_row_number)
    except StopIteration as e:
        raise ValueError(
            f"Pre-load profile failed: CSV file does not contain header row "
            f"{header_row_number}: {source_file}"
        ) from e

    if has_header:
        raw_columns = trim_trailing_blank_header_columns(first_row)
        duplicate_headers = sorted(
            {
                column
                for column in raw_columns
                if column and raw_columns.count(column) > 1
            }
        )
        actual_columns = (
            build_canonical_column_names(raw_columns)
            if canonicalize_headers
            else raw_columns
        )
        data_rows = reader
    else:
        actual_columns = build_ordinal_column_names(len(first_row))
        duplicate_headers = []
        data_rows = chain([first_row], reader)

    column_length_profile = {
        column: _build_initial_column_profile(index + 1)
        for index, column in enumerate(actual_columns)
    }
    expected_column_count = len(actual_columns)
    data_row_count = 0
    malformed_row_count = 0
    repaired_row_count = 0

    for _, repaired_row, was_repaired in iter_repaired_csv_rows(
        _iter_trimmed_reader_rows(data_rows, expected_column_count),
        expected_column_count,
        row_repair_strategy,
    ):
        if repaired_row is None:
            malformed_row_count += 1
            continue

        data_row_count += 1
        if was_repaired:
            repaired_row_count += 1

        for column_index, column_name in enumerate(actual_columns):
            value = (repaired_row[column_index] or "").strip()
            profile = column_length_profile[column_name]

            if value == "":
                profile["blank_row_count"] += 1
                continue

            profile["non_null_row_count"] += 1
            value_length = len(value)
            current_max = profile["max_string_length"]

            if current_max is None or value_length > current_max:
                profile["max_string_length"] = value_length

            _update_profile_values(profile, value)

    if data_row_count == 0:
        raise ValueError(f"Pre-load profile failed: CSV file has no data rows: {source_file}")

    malformed_threshold_exceeded = malformed_row_count > max_malformed_rows

    return {
        "row_count": data_row_count,
        "malformed_row_count": malformed_row_count,
        "repaired_row_count": repaired_row_count,
        "malformed_threshold_exceeded": malformed_threshold_exceeded,
        "duplicate_headers": duplicate_headers,
        "column_length_profile": {
            column_name: _finalize_column_profile(profile)
            for column_name, profile in column_length_profile.items()
        },
    }


def _iter_trimmed_reader_rows(reader, expected_column_count: int):
    for row in reader:
        yield trim_trailing_blank_row_values(row, expected_column_count)


def _read_header_row(reader, header_row_number: int) -> list[str]:
    if header_row_number < 1:
        raise ValueError("header_row_number must be at least 1.")

    for row_number, row in enumerate(reader, start=1):
        if row_number == header_row_number:
            return row

    raise StopIteration


def _merge_column_profiles(
    aggregate_profile: dict[str, dict[str, int | str | None]],
    next_profile: dict[str, dict[str, int | str | None]],
    source_file: Path,
) -> None:
    if list(aggregate_profile.keys()) != list(next_profile.keys()):
        raise ValueError(
            f"Pre-load profile failed: column drift detected in {source_file}. "
            "All files for a pipeline must share the same header order."
        )

    for column_name, next_column_profile in next_profile.items():
        aggregate_column_profile = aggregate_profile[column_name]
        aggregate_column_profile["blank_row_count"] = int(
            aggregate_column_profile["blank_row_count"] or 0
        ) + int(next_column_profile["blank_row_count"] or 0)
        aggregate_column_profile["non_null_row_count"] = int(
            aggregate_column_profile["non_null_row_count"] or 0
        ) + int(next_column_profile["non_null_row_count"] or 0)

        next_max_length = next_column_profile["max_string_length"]
        aggregate_max_length = aggregate_column_profile["max_string_length"]
        if next_max_length is not None and (
            aggregate_max_length is None or int(next_max_length) > int(aggregate_max_length)
        ):
            aggregate_column_profile["max_string_length"] = next_max_length

        next_minimum = next_column_profile["minimum_non_null_value"]
        aggregate_minimum = aggregate_column_profile["minimum_non_null_value"]
        if next_minimum is not None and (
            aggregate_minimum is None or str(next_minimum) < str(aggregate_minimum)
        ):
            aggregate_column_profile["minimum_non_null_value"] = next_minimum

        next_maximum = next_column_profile["maximum_non_null_value"]
        aggregate_maximum = aggregate_column_profile["maximum_non_null_value"]
        if next_maximum is not None and (
            aggregate_maximum is None or str(next_maximum) > str(aggregate_maximum)
        ):
            aggregate_column_profile["maximum_non_null_value"] = next_maximum

        nullable = bool(aggregate_column_profile["blank_row_count"])
        aggregate_column_profile["nullable_flag"] = 1 if nullable else 0
        aggregate_column_profile["nullability"] = "NULL" if nullable else "NOT NULL"
        aggregate_column_profile["defined_sql_type"] = "VARCHAR(4000)"


def _build_initial_column_profile(ordinal_position: int) -> dict[str, int | bool | str | None]:
    return {
        "ordinal_position": ordinal_position,
        "minimum_non_null_value": None,
        "maximum_non_null_value": None,
        "max_string_length": None,
        "blank_row_count": 0,
        "non_null_row_count": 0,
    }


def _update_profile_values(profile: dict[str, int | bool | str | None], value: str) -> None:
    normalized = value.strip()
    current_minimum_value = _normalize_profile_value(profile["minimum_non_null_value"])
    current_maximum_value = _normalize_profile_value(profile["maximum_non_null_value"])

    if current_minimum_value is None or normalized < current_minimum_value:
        profile["minimum_non_null_value"] = normalized

    if current_maximum_value is None or normalized > current_maximum_value:
        profile["maximum_non_null_value"] = normalized


def _normalize_profile_value(value: int | bool | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _finalize_column_profile(
    profile: dict[str, int | str | None]
) -> dict[str, int | str | None]:
    blank_row_count = int(profile["blank_row_count"] or 0)
    non_null_row_count = int(profile["non_null_row_count"] or 0)
    max_string_length = profile["max_string_length"]

    nullable = blank_row_count > 0
    nullability = "NULL" if nullable else "NOT NULL"
    defined_sql_type = "VARCHAR(4000)"

    return {
        "ordinal_position": int(profile["ordinal_position"] or 0),
        "minimum_non_null_value": profile["minimum_non_null_value"],
        "maximum_non_null_value": profile["maximum_non_null_value"],
        "max_string_length": max_string_length,
        "blank_row_count": blank_row_count,
        "non_null_row_count": non_null_row_count,
        "nullable_flag": 1 if nullable else 0,
        "nullability": nullability,
        "defined_sql_type": defined_sql_type,
    }


def _resolve_source_files(profile_config: dict) -> list[Path]:
    if "source_files" in profile_config:
        source_files = [Path(path) for path in profile_config["source_files"]]
    elif "source_file" in profile_config:
        source_files = [Path(profile_config["source_file"])]
    else:
        raise ValueError("Pre-load profile failed: source_file or source_files is required.")

    return sorted(source_files)


def _build_aggregate_source_file_label(source_files: list[Path]) -> str:
    if len(source_files) == 1:
        return str(source_files[0])
    return f"MULTI_FILE::{len(source_files)} files::{_get_common_parent(source_files)}"


def _get_common_parent(source_files: list[Path]) -> Path:
    common_path = Path(source_files[0].parent)
    for source_file in source_files[1:]:
        common_path = Path(os.path.commonpath([str(common_path), str(source_file.parent)]))
    return common_path


def _build_candidate_encodings(preferred_encoding: str) -> list[str]:
    candidates = [preferred_encoding, "cp1252", "latin-1", "utf-8-sig", "utf-8"]
    unique_candidates: list[str] = []
    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)
    return unique_candidates


def _detect_encoding(source_file: Path, sample_bytes: int = 16384) -> str:
    with source_file.open("rb") as file:
        raw_sample = file.read(sample_bytes)
    if raw_sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"

    for encoding in ENCODING_CANDIDATES:
        try:
            raw_sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue

    return "latin-1"
