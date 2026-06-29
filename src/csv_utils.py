import csv
import re
from collections import Counter
import sys


def ensure_max_csv_field_size() -> int:
    limit = sys.maxsize

    while True:
        try:
            return csv.field_size_limit(limit)
        except OverflowError:
            limit = limit // 10
            if limit <= 0:
                raise RuntimeError("Unable to configure CSV field size limit.")


def trim_trailing_blank_header_columns(header: list[str]) -> list[str]:
    normalized_header = [column.strip() for column in header]
    last_named_index = -1

    for index, column_name in enumerate(normalized_header):
        if column_name:
            last_named_index = index

    if last_named_index == -1:
        return normalized_header

    return normalized_header[: last_named_index + 1]


def trim_trailing_blank_row_values(row: list[str], expected_column_count: int) -> list[str]:
    if len(row) <= expected_column_count:
        return row

    trailing_values = row[expected_column_count:]
    if all(not (value or "").strip() for value in trailing_values):
        return row[:expected_column_count]

    return row


def build_canonical_column_names(header: list[str]) -> list[str]:
    occurrence_counts: Counter[str] = Counter()
    canonical_columns = []

    for position, column_name in enumerate(header, start=1):
        stripped_column_name = (column_name or "").strip()
        base_name = (
            sanitize_column_name(stripped_column_name)
            if stripped_column_name
            else f"Column_{position:03d}"
        )
        occurrence_counts[base_name] += 1
        if occurrence_counts[base_name] == 1:
            canonical_columns.append(base_name)
        else:
            canonical_columns.append(f"{base_name}_{occurrence_counts[base_name]}")

    return canonical_columns


def build_ordinal_column_names(column_count: int) -> list[str]:
    return [f"Column_{position:03d}" for position in range(1, column_count + 1)]


def sanitize_column_name(column_name: str) -> str:
    dot_separated_words = re.sub(r"(?<=[A-Za-z])\.\s*(?=[A-Za-z])", "_", column_name.strip())
    without_remaining_dots = dot_separated_words.replace(".", "")
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", without_remaining_dots)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        return "Column"
    if normalized[0].isdigit():
        normalized = f"Column_{normalized}"
    return normalized[:120]
