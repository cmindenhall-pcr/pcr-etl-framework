from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from src.numeric_normalization import normalize_numeric_candidate
from pathlib import Path

from src.csv_loader import BCP_FIELD_TERMINATOR

FORMAT_FILE_VERSION = "14.0"
BCP_ROW_TERMINATOR = "\r\n"


def build_bcp_format_files(
    reviewed_schema: dict[str, object],
    output_dir: Path,
    include_raw_metadata: bool = False,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    columns_by_table = _group_columns_by_table(reviewed_schema)
    generated_paths = []

    for table_name, columns in sorted(columns_by_table.items()):
        format_text = build_bcp_format_text(
            table_name=table_name,
            columns=columns,
            include_raw_metadata=include_raw_metadata,
        )
        output_path = output_dir / f"{table_name}.fmt"
        output_path.write_text(format_text, encoding="utf-8")
        generated_paths.append(output_path)

    return generated_paths


def serialize_rows_for_bcp(
    columns: list[dict[str, object]],
    rows: list[list[str | None]],
    include_raw_metadata: bool = False,
    load_date_text: str | None = None,
    source_file_text: str | None = None,
) -> str:
    serialized_lines = []

    for row in rows:
        if len(row) != len(columns):
            raise ValueError(
                f"BCP row length {len(row)} does not match approved column count {len(columns)}."
            )

        serialized_values = [
            normalize_value_for_sql_type(value, str(column.get("final_sql_type") or column.get("defined_sql_type") or "VARCHAR(100)"))
            for value, column in zip(row, columns)
        ]

        if include_raw_metadata:
            if load_date_text is None or source_file_text is None:
                raise ValueError(
                    "load_date_text and source_file_text are required when include_raw_metadata=True."
                )
            serialized_values.extend([load_date_text, source_file_text])

        serialized_lines.append(BCP_FIELD_TERMINATOR.join(serialized_values))

    return BCP_ROW_TERMINATOR.join(serialized_lines) + BCP_ROW_TERMINATOR


def normalize_value_for_sql_type(value: str | None, sql_type: str) -> str:
    if value is None:
        return ""

    normalized_value = value.strip()
    if normalized_value == "":
        return ""

    normalized_sql_type = sql_type.strip().upper()

    if normalized_sql_type == "BIT":
        parsed_bool = _parse_boolean_like(normalized_value)
        if parsed_bool is None:
            raise ValueError(f"Value '{value}' is not valid for SQL type {sql_type}.")
        return "1" if parsed_bool else "0"

    if normalized_sql_type in {"TINYINT", "SMALLINT", "INT", "BIGINT"}:
        return _normalize_integer_like(normalized_value, sql_type)

    if normalized_sql_type.startswith("DECIMAL("):
        return _normalize_decimal_like(normalized_value, sql_type)

    return normalized_value.replace("\r", " ").replace("\n", " ").replace(BCP_FIELD_TERMINATOR, " ")


def build_bcp_format_text(
    table_name: str,
    columns: list[dict[str, object]],
    include_raw_metadata: bool = False,
) -> str:
    field_specs = []

    for index, column in enumerate(columns, start=1):
        field_specs.append(
            _build_field_spec(
                field_order=index,
                server_order=index,
                column_name=str(column["column_name"]),
                sql_type=str(column.get("final_sql_type") or column.get("defined_sql_type") or "VARCHAR(100)"),
                terminator=BCP_FIELD_TERMINATOR,
            )
        )

    if include_raw_metadata:
        metadata_start = len(field_specs) + 1
        metadata_columns = [
            ("LoadDate", "DATETIME"),
            ("SFileName", "VARCHAR(255)"),
        ]
        for offset, (column_name, sql_type) in enumerate(metadata_columns):
            terminator = BCP_FIELD_TERMINATOR if offset < len(metadata_columns) - 1 else BCP_ROW_TERMINATOR
            field_specs.append(
                _build_field_spec(
                    field_order=metadata_start + offset,
                    server_order=metadata_start + offset,
                    column_name=column_name,
                    sql_type=sql_type,
                    terminator=terminator,
                )
            )
    elif field_specs:
        field_specs[-1]["terminator"] = _escape_terminator(BCP_ROW_TERMINATOR)

    lines = [FORMAT_FILE_VERSION, str(len(field_specs))]
    lines.extend(_render_field_spec(spec) for spec in field_specs)
    return "\n".join(lines) + "\n"


def _group_columns_by_table(reviewed_schema: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    columns_by_table: dict[str, list[dict[str, object]]] = {}
    for column in reviewed_schema.get("columns", []):
        table_name = str(column["table_name"])
        columns_by_table.setdefault(table_name, []).append(column)
    return columns_by_table


def _build_field_spec(
    field_order: int,
    server_order: int,
    column_name: str,
    sql_type: str,
    terminator: str,
) -> dict[str, object]:
    return {
        "field_order": field_order,
        "host_file_type": "SQLCHAR",
        "prefix_length": 0,
        "host_length": _estimate_host_length(sql_type),
        "terminator": _escape_terminator(terminator),
        "server_order": server_order,
        "column_name": column_name,
        "collation": _collation_for_type(sql_type),
    }


def _render_field_spec(field_spec: dict[str, object]) -> str:
    return (
        f"{field_spec['field_order']} "
        f"{field_spec['host_file_type']} "
        f"{field_spec['prefix_length']} "
        f"{field_spec['host_length']} "
        f"\"{field_spec['terminator']}\" "
        f"{field_spec['server_order']} "
        f"{field_spec['column_name']} "
        f"{field_spec['collation']}"
    )


def _estimate_host_length(sql_type: str) -> int:
    normalized = sql_type.strip().upper()
    if normalized == "BIT":
        return 5
    if normalized == "TINYINT":
        return 3
    if normalized == "SMALLINT":
        return 6
    if normalized == "INT":
        return 11
    if normalized == "BIGINT":
        return 20
    if normalized == "UNIQUEIDENTIFIER":
        return 36
    if normalized == "DATE":
        return 10
    if normalized == "TIME":
        return 16
    if normalized == "DATETIME":
        return 23
    if normalized == "DATETIME2":
        return 27
    if normalized == "DATETIMEOFFSET":
        return 34

    decimal_match = re.match(r"DECIMAL\((\d+),\s*(\d+)\)", normalized)
    if decimal_match:
        precision = int(decimal_match.group(1))
        scale = int(decimal_match.group(2))
        sign_chars = 1
        decimal_chars = 1 if scale > 0 else 0
        return precision + sign_chars + decimal_chars

    varchar_match = re.match(r"(VAR)?CHAR\((MAX|\d+)\)", normalized)
    if varchar_match:
        length_token = varchar_match.group(2)
        if length_token == "MAX":
            return 8000
        return int(length_token)

    nvarchar_match = re.match(r"N(VAR)?CHAR\((MAX|\d+)\)", normalized)
    if nvarchar_match:
        length_token = nvarchar_match.group(2)
        if length_token == "MAX":
            return 4000
        return int(length_token)

    return 4000


def _collation_for_type(sql_type: str) -> str:
    normalized = sql_type.strip().upper()
    if any(token in normalized for token in ("CHAR", "TEXT", "XML", "UNIQUEIDENTIFIER")):
        return "SQL_Latin1_General_CP1_CI_AS"
    return '""'


def _escape_terminator(terminator: str) -> str:
    escaped_chars: list[str] = []
    for char in terminator:
        if char == "\r":
            escaped_chars.append("\\r")
        elif char == "\n":
            escaped_chars.append("\\n")
        elif char == "\\":
            escaped_chars.append("\\\\")
        else:
            escaped_chars.append(char)
    return "".join(escaped_chars)


def _parse_boolean_like(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y"}:
        return True
    if normalized in {"0", "false", "f", "no", "n"}:
        return False
    return None


def _normalize_integer_like(value: str, sql_type: str) -> str:
    normalized_value = normalize_numeric_candidate(value)
    if normalized_value is None:
        raise ValueError(f"Value '{value}' is not valid for SQL type {sql_type}.")

    try:
        parsed_decimal = Decimal(normalized_value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Value '{value}' is not valid for SQL type {sql_type}.") from exc

    if parsed_decimal != parsed_decimal.to_integral_value():
        raise ValueError(f"Value '{value}' is not valid for SQL type {sql_type}.")

    return str(int(parsed_decimal))


def _normalize_decimal_like(value: str, sql_type: str) -> str:
    decimal_match = re.match(r"DECIMAL\((\d+),\s*(\d+)\)", sql_type.strip().upper())
    if decimal_match is None:
        return value

    normalized_value = normalize_numeric_candidate(value)
    if normalized_value is None:
        raise ValueError(f"Value '{value}' is not valid for SQL type {sql_type}.")

    try:
        parsed_decimal = Decimal(normalized_value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Value '{value}' is not valid for SQL type {sql_type}.") from exc

    scale = int(decimal_match.group(2))
    if scale == 0:
        if parsed_decimal != parsed_decimal.to_integral_value():
            raise ValueError(f"Value '{value}' is not valid for SQL type {sql_type}.")
        return str(int(parsed_decimal))

    normalized = format(parsed_decimal.normalize(), "f")
    if "." not in normalized:
        return normalized

    whole_part, fractional_part = normalized.split(".", maxsplit=1)
    fractional_part = fractional_part[:scale].ljust(scale, "0")
    return f"{whole_part}.{fractional_part}"
