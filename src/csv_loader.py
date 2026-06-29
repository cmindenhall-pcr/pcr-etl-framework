import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime
from itertools import chain
from pathlib import Path

from tempfile import NamedTemporaryFile

from src.audit_logger import insert_load_execution_log
from src.csv_row_repair import iter_repaired_csv_rows
from src.csv_utils import (
    build_canonical_column_names,
    build_ordinal_column_names,
    ensure_max_csv_field_size,
    trim_trailing_blank_header_columns,
    trim_trailing_blank_row_values,
)
from src.db_connection import get_connection_settings, get_connection
from src.logger import get_logger
from src.project_paths import PROJECT_ROOT

logger = get_logger()
ensure_max_csv_field_size()
ENCODING_CANDIDATES = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
RAW_METADATA_COLUMNS = ["LoadDate", "SFileName"]
BCP_FIELD_TERMINATOR = "\x1f"
BCP_BATCH_SIZE = 50000
BCP_PACKET_SIZE = 65535
BCP_MIN_ROW_COUNT = 1000
BCP_TIMEOUT_SECONDS = 300
BCP_ERROR_SAMPLE_LIMIT = 5
PYODBC_FALLBACK_BATCH_SIZE = 5000
BCP_DISABLED_REASON: str | None = None
BCP_EXE_CANDIDATES = [
    Path(r"C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\170\Tools\Binn\bcp.exe"),
    Path(r"C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\180\Tools\Binn\bcp.exe"),
]
BCP_FILE_ENCODING = "utf-8"
BCP_ROW_TERMINATOR = "\r\n"
MAX_BCP_FIELD_LENGTH = 4000


def _resolve_bcp_executable() -> str:
    for candidate in BCP_EXE_CANDIDATES:
        if candidate.exists():
            return str(candidate)
    return "bcp"


def _get_bcp_connection_flags(bcp_executable: str) -> list[str]:
    normalized = bcp_executable.lower()
    if "\\170\\" in normalized:
        return []
    return ["-Yo", "-u"]


def _resolve_bcp_server_name(server: str) -> str:
    normalized = server.strip().lower()
    if normalized in {"localhost", ".", "(local)", "127.0.0.1", "::1"}:
        return "tcp:localhost,1433"
    return server


class BcpLoadError(RuntimeError):
    def __init__(self, message: str, diagnostics: dict[str, object]):
        super().__init__(message)
        self.diagnostics = diagnostics


def _resolve_source_files(profile_config: dict) -> list[Path]:
    if "source_files" in profile_config:
        source_files = [Path(path) for path in profile_config["source_files"]]
    elif "source_file" in profile_config:
        source_files = [Path(profile_config["source_file"])]
    else:
        raise ValueError("Raw load failed: source_file or source_files is required.")

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


def load_csv_to_table(
    table_name: str,
    profile_config: dict,
    run_id: str | None = None,
    normalize_loaded_columns: bool = True,
    allow_metadata_backfill: bool = True,
) -> dict[str, int | str]:
    global BCP_DISABLED_REASON
    BCP_DISABLED_REASON = None

    source_files = _resolve_source_files(profile_config)
    column_mappings = profile_config["column_mappings"]
    source_columns = list(column_mappings.keys())
    target_columns = list(column_mappings.values())
    column_sql_types = _build_target_column_sql_types(
        target_columns,
        profile_config.get("column_sql_types"),
    )
    delimiter = profile_config["delimiter"]
    has_header = profile_config["has_header"]
    chunk_size_mb = profile_config.get("chunk_size_mb")
    row_repair_strategy = profile_config.get("row_repair_strategy")
    max_malformed_rows = profile_config.get("max_malformed_rows", 0)
    canonicalize_headers = bool(profile_config.get("canonicalize_headers", False))
    header_row_number = int(profile_config.get("header_row_number", 1))

    for source_file in source_files:
        if not source_file.exists():
            raise FileNotFoundError(f"Raw load failed: source file not found: {source_file}")

    for source_file in source_files:
        source_file_string = str(source_file)
        if len(source_file_string) > 255:
            raise ValueError(
                f"Raw load failed: source file path exceeds 255 characters for {source_file}"
            )

    aggregate_source_file = _build_aggregate_source_file_label(source_files)
    aggregate_source_folder = str(_get_common_parent(source_files))
    prefix = f"[run_id={run_id}] " if run_id else ""
    logger.info(f"{prefix}Preparing {table_name} for CSV load from {len(source_files)} source file(s)")
    prepared_inputs: list[tuple[Path, Path]] = []
    chunk_dirs: list[Path] = []
    for source_file in source_files:
        input_files, chunk_dir = _prepare_input_files(
            source_file=source_file,
            delimiter=delimiter,
            has_header=has_header,
            chunk_size_mb=chunk_size_mb,
            row_repair_strategy=row_repair_strategy,
            max_malformed_rows=max_malformed_rows,
            header_row_number=header_row_number,
            prefix=prefix,
        )
        prepared_inputs.extend((input_file, source_file) for input_file in input_files)
        if chunk_dir is not None:
            chunk_dirs.append(chunk_dir)

    insert_columns = target_columns + ["LoadDate", "SFileName"]
    column_definitions = ", ".join(
        f"[{column}] {column_sql_types[column]} NULL" for column in target_columns
    )
    schema_name, object_name = table_name.split(".", maxsplit=1)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        _ensure_schema_exists(cursor, schema_name)

        if _table_exists(cursor, schema_name, object_name):
            if allow_metadata_backfill:
                _ensure_raw_table_metadata_columns(cursor, schema_name, object_name)
            actual_columns = _get_table_columns(cursor, schema_name, object_name)
            expected_columns = target_columns + RAW_METADATA_COLUMNS
            if actual_columns != expected_columns:
                raise RuntimeError(
                    f"Raw load failed for {table_name}: existing table schema drift detected. "
                    f"Expected columns: {expected_columns}. Actual columns: {actual_columns}."
                )
        else:
            cursor.execute(
                f"""
                CREATE TABLE {schema_name}.{object_name} (
                    {column_definitions},
                    [LoadDate] DATETIME NOT NULL,
                    [SFileName] VARCHAR(255) NOT NULL
                );
                """
            )

        inserted_row_count = 0
        malformed_row_count = 0
        repaired_row_count = 0

        cursor.execute(f"TRUNCATE TABLE {schema_name}.{object_name}")
        # bcp runs through a separate connection, so the truncate must be committed
        # before bulk load begins or the new session can block on our open transaction.
        conn.commit()
        _ensure_raw_table_text_column_types(
            cursor,
            schema_name,
            object_name,
            column_sql_types,
        )
        conn.commit()

        for input_file, source_file in prepared_inputs:
            load_timestamp = datetime.now()
            inserted_count, malformed_count, repaired_count = _load_chunk_with_bcp(
                cursor=cursor,
                table_name=table_name,
                target_columns=target_columns,
                input_file=input_file,
                source_columns=source_columns,
                delimiter=delimiter,
                has_header=has_header,
                row_repair_strategy=row_repair_strategy,
                max_malformed_rows=max_malformed_rows,
                canonicalize_headers=canonicalize_headers,
                header_row_number=header_row_number,
                load_timestamp=load_timestamp,
                source_file_string=str(source_file),
                run_id=run_id,
                prefix=prefix,
            )
            inserted_row_count += inserted_count
            malformed_row_count += malformed_count
            repaired_row_count += repaired_count

        if normalize_loaded_columns:
            _normalize_loaded_raw_columns(cursor, schema_name, object_name, target_columns)

        if inserted_row_count == 0:
            raise ValueError(
                f"Raw load failed: CSV files have no data rows to load: {aggregate_source_file}"
            )

        conn.commit()

        if malformed_row_count:
            logger.warning(
                f"{prefix}Raw load warning: skipped malformed rows in configured source files: "
                f"{malformed_row_count}"
            )

        logger.info(
            f"{prefix}Loaded {inserted_row_count} rows from configured source files into {table_name}"
        )
        return {
            "source_file": aggregate_source_file,
            "source_folder_path": aggregate_source_folder,
            "inserted_row_count": inserted_row_count,
            "malformed_row_count": malformed_row_count,
            "repaired_row_count": repaired_row_count,
        }

    except Exception as e:
        conn.rollback()
        logger.exception(f"{prefix}Failed loading CSV into {table_name}")
        raise RuntimeError(
            f"Raw load failed for {table_name} from configured source files: {e}"
        ) from e

    finally:
        conn.close()
        for chunk_dir in chunk_dirs:
            _cleanup_chunk_dir(chunk_dir)


def _normalize_csv_value(value: str | None) -> str | None:
    if value is None:
        return None

    stripped_value = value.strip()
    if stripped_value == "":
        return None

    return stripped_value


def _build_target_column_sql_types(
    target_columns: list[str],
    configured_column_sql_types: dict[str, str] | None,
) -> dict[str, str]:
    configured_column_sql_types = configured_column_sql_types or {}
    column_sql_types: dict[str, str] = {}

    for column_name in target_columns:
        configured_sql_type = configured_column_sql_types.get(column_name, "VARCHAR(4000)")
        column_sql_types[column_name] = _normalize_raw_text_sql_type(configured_sql_type)

    return column_sql_types


def _build_candidate_encodings(preferred_encoding: str) -> list[str]:
    candidates = [preferred_encoding, "cp1252", "latin-1", "utf-8-sig", "utf-8"]
    unique_candidates: list[str] = []
    for candidate in candidates:
        if candidate not in unique_candidates:
            unique_candidates.append(candidate)
    return unique_candidates


def _detect_encoding(source_file: Path, sample_bytes: int = 16384) -> str:
    raw_sample = source_file.read_bytes()[:sample_bytes]
    if raw_sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"

    for encoding in ENCODING_CANDIDATES:
        try:
            raw_sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue

    return "latin-1"


def _run_with_csv_reader_fallback(
    source_file: Path,
    delimiter: str,
    processor,
) -> tuple[object, str]:
    candidate_encodings = _build_candidate_encodings(_detect_encoding(source_file))
    last_error: UnicodeDecodeError | None = None

    for encoding in candidate_encodings:
        try:
            with open(source_file, newline="", encoding=encoding) as source:
                reader = csv.reader(source, delimiter=delimiter)
                return processor(reader), encoding
        except UnicodeDecodeError as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise ValueError(
            f"Raw load failed: unable to decode {source_file} with supported encodings."
        ) from last_error

    raise ValueError(f"Raw load failed: unable to read {source_file}.")


def _load_chunk_with_bcp(
    cursor,
    table_name: str,
    target_columns: list[str],
    input_file: Path,
    source_columns: list[str],
    delimiter: str,
    has_header: bool,
    row_repair_strategy: str | None,
    max_malformed_rows: int,
    canonicalize_headers: bool,
    header_row_number: int,
    load_timestamp: datetime,
    source_file_string: str,
    run_id: str | None,
    prefix: str,
) -> tuple[int, int, int]:
    global BCP_DISABLED_REASON

    def _process_reader(reader) -> tuple[int, int, int, int, Path]:
        try:
            first_row = _read_header_row(reader, header_row_number)
        except StopIteration as e:
            raise ValueError(
                f"Raw load failed: CSV file does not contain header row "
                f"{header_row_number}: {input_file}"
            ) from e

        if has_header:
            raw_header = trim_trailing_blank_header_columns(first_row)
            header = (
                build_canonical_column_names(raw_header)
                if canonicalize_headers
                else raw_header
            )
            data_rows = reader
        else:
            header = build_ordinal_column_names(len(first_row))
            data_rows = chain([first_row], reader)

        header_indexes = {column: header.index(column) for column in source_columns}
        load_timestamp_text = load_timestamp.strftime("%Y-%m-%d %H:%M:%S")
        row_count = 0
        malformed_row_count = 0
        repaired_row_count = 0
        expected_column_count = len(header)
        max_field_length = 0

        with NamedTemporaryFile(
            mode="w",
            encoding=BCP_FILE_ENCODING,
            newline="",
            suffix=".txt",
            delete=False,
            dir=PROJECT_ROOT / "tmp",
        ) as bulk_file:
            bulk_file_path = Path(bulk_file.name)
            try:
                for row_number, repaired_row, was_repaired in iter_repaired_csv_rows(
                    _iter_trimmed_reader_rows(data_rows, expected_column_count),
                    expected_column_count,
                    row_repair_strategy,
                ):
                    if repaired_row is None:
                        malformed_row_count += 1
                        if malformed_row_count > max_malformed_rows:
                            raise ValueError(
                                f"Raw load failed: malformed row count {malformed_row_count} "
                                f"exceeds allowed threshold {max_malformed_rows} in {input_file}"
                            )
                        continue
                    if was_repaired:
                        repaired_row_count += 1

                    values = [
                        _normalize_bcp_value(
                            _normalize_csv_value(repaired_row[header_indexes[column]])
                        )
                        for column in source_columns
                    ]
                    values.extend([load_timestamp_text, source_file_string])
                    max_field_length = max(max_field_length, *(len(value) for value in values))
                    bulk_file.write(BCP_FIELD_TERMINATOR.join(values) + BCP_ROW_TERMINATOR)
                    row_count += 1
            finally:
                bulk_file.flush()

        return row_count, malformed_row_count, repaired_row_count, max_field_length, bulk_file_path

    (
        row_count,
        malformed_row_count,
        repaired_row_count,
        max_field_length,
        bulk_file_path,
    ), source_encoding = _run_with_csv_reader_fallback(
        source_file=input_file,
        delimiter=delimiter,
        processor=_process_reader,
    )
    logger.info(f"{prefix}Reading {input_file.name} using encoding {source_encoding}")

    if row_count == 0:
        os.remove(bulk_file_path)
        return 0, malformed_row_count, repaired_row_count

    use_bcp = (
        row_count >= BCP_MIN_ROW_COUNT
        and BCP_DISABLED_REASON is None
        and max_field_length <= MAX_BCP_FIELD_LENGTH
    )

    if use_bcp:
        logger.info(
            f"{prefix}Using bcp bulk load for {table_name} chunk with {row_count} rows"
        )
        bcp_started_at = datetime.now()
        try:
            diagnostics = _run_bcp_load(table_name, bulk_file_path, prefix)
            inserted_row_count = _get_loaded_chunk_row_count(
                cursor=cursor,
                table_name=table_name,
                load_timestamp=load_timestamp,
                source_file_string=source_file_string,
            )
            if inserted_row_count != row_count:
                _delete_loaded_chunk_rows(
                    cursor=cursor,
                    table_name=table_name,
                    load_timestamp=load_timestamp,
                    source_file_string=source_file_string,
                )
                raise BcpLoadError(
                    (
                        f"{prefix}BCP load row-count mismatch for {table_name}: "
                        f"expected {row_count}, inserted {inserted_row_count}"
                    ),
                    diagnostics=diagnostics,
                )
            bcp_finished_at = datetime.now()
            insert_load_execution_log(
                run_id=run_id,
                pipeline_name=_pipeline_name_from_table(table_name),
                table_name=table_name,
                source_file=source_file_string,
                load_method="BCP",
                load_status="SUCCESS",
                chunk_row_count=row_count,
                started_at=bcp_started_at,
                finished_at=bcp_finished_at,
                duration_ms=int((bcp_finished_at - bcp_started_at).total_seconds() * 1000),
                server_name=str(diagnostics["server_name"]),
                executable_path=str(diagnostics["executable_path"]),
                exit_code=int(diagnostics["exit_code"]),
                detail_message="BCP chunk load completed successfully.",
                bcp_error_row_count=_safe_int(diagnostics.get("error_row_count")),
                bcp_error_summary=_safe_text(diagnostics.get("error_summary_json")),
                stdout_text=str(diagnostics["stdout_text"]),
                stderr_text=str(diagnostics["stderr_text"]),
            )
        except BcpLoadError as exc:
            bcp_finished_at = datetime.now()
            diagnostics = exc.diagnostics
            BCP_DISABLED_REASON = str(exc)
            insert_load_execution_log(
                run_id=run_id,
                pipeline_name=_pipeline_name_from_table(table_name),
                table_name=table_name,
                source_file=source_file_string,
                load_method="BCP",
                load_status="FAILED",
                chunk_row_count=row_count,
                started_at=bcp_started_at,
                finished_at=bcp_finished_at,
                duration_ms=int((bcp_finished_at - bcp_started_at).total_seconds() * 1000),
                server_name=str(diagnostics["server_name"]),
                executable_path=str(diagnostics["executable_path"]),
                exit_code=_safe_int(diagnostics.get("exit_code")),
                detail_message=str(exc),
                bcp_error_row_count=_safe_int(diagnostics.get("error_row_count")),
                bcp_error_summary=_safe_text(diagnostics.get("error_summary_json")),
                stdout_text=_safe_text(diagnostics.get("stdout_text")),
                stderr_text=_safe_text(diagnostics.get("stderr_text")),
            )
            logger.warning(
                f"{prefix}BCP unavailable; falling back to direct insert for current and subsequent chunks. Reason: {BCP_DISABLED_REASON}"
            )
            _load_chunk_with_pyodbc(
                cursor=cursor,
                table_name=table_name,
                target_columns=target_columns,
                bulk_file_path=bulk_file_path,
                row_count=row_count,
                source_file_string=source_file_string,
                run_id=run_id,
                detail_message="BCP failed for this chunk; pyodbc fallback succeeded.",
                contains_large_fields=max_field_length > MAX_BCP_FIELD_LENGTH,
            )
    else:
        if BCP_DISABLED_REASON:
            logger.info(
                f"{prefix}Using direct insert for {table_name} chunk with {row_count} rows because BCP is disabled"
            )
        elif max_field_length > MAX_BCP_FIELD_LENGTH:
            logger.info(
                f"{prefix}Using direct insert for {table_name} chunk with {row_count} rows "
                f"because a field exceeds {MAX_BCP_FIELD_LENGTH} characters"
            )
        else:
            logger.info(
                f"{prefix}Using direct insert for {table_name} chunk with {row_count} rows"
            )
        _load_chunk_with_pyodbc(
            cursor=cursor,
            table_name=table_name,
            target_columns=target_columns,
            bulk_file_path=bulk_file_path,
            row_count=row_count,
            source_file_string=source_file_string,
            run_id=run_id,
            detail_message=(
                "BCP disabled earlier in this load session; pyodbc direct insert used."
                if BCP_DISABLED_REASON
                else (
                    f"Chunk contains field values longer than {MAX_BCP_FIELD_LENGTH}; "
                    "pyodbc direct insert used."
                )
                if max_field_length > MAX_BCP_FIELD_LENGTH
                else "Chunk below BCP threshold; pyodbc direct insert used."
            ),
            contains_large_fields=max_field_length > MAX_BCP_FIELD_LENGTH,
        )

    os.remove(bulk_file_path)
    return row_count, malformed_row_count, repaired_row_count


def _iter_trimmed_reader_rows(reader, expected_column_count: int):
    for row in reader:
        yield trim_trailing_blank_row_values(row, expected_column_count)


def _normalize_bcp_value(value: str | None) -> str:
    if value is None:
        return ""

    cleaned = value.replace("\r", " ").replace("\n", " ").replace(BCP_FIELD_TERMINATOR, " ")
    return cleaned


def _run_bcp_load(table_name: str, bulk_file_path: Path, prefix: str) -> dict[str, object]:
    settings = get_connection_settings()
    bcp_executable = _resolve_bcp_executable()
    server = settings["server"] or ""
    bcp_server = _resolve_bcp_server_name(server)
    database = settings["database"] or ""
    username = settings["username"] or ""
    password = settings["password"] or ""

    command = [
        bcp_executable,
        table_name,
        "in",
        str(bulk_file_path),
        "-S",
        bcp_server,
        "-d",
        database,
        "-l",
        "15",
        "-C",
        "65001",
        "-q",
        "-b",
        str(BCP_BATCH_SIZE),
        "-a",
        str(BCP_PACKET_SIZE),
        "-h",
        "TABLOCK",
    ]

    if username and password:
        command.extend(["-U", username, "-P", password])
    else:
        command.append("-T")

    command.extend(_get_bcp_connection_flags(bcp_executable))

    diagnostics = {
        "server_name": bcp_server,
        "executable_path": bcp_executable,
        "exit_code": None,
        "stdout_text": "",
        "stderr_text": "",
        "error_row_count": None,
        "error_summary_json": None,
    }

    error_file = NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        suffix=".err",
        delete=False,
        dir=PROJECT_ROOT / "tmp",
    )
    error_file_path = Path(error_file.name)
    error_file.close()
    command.extend(["-e", str(error_file_path)])

    format_file = NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        suffix=".fmt",
        delete=False,
        dir=PROJECT_ROOT / "tmp",
    )
    format_file_path = Path(format_file.name)
    format_file.write(_build_raw_bcp_format_text(table_name))
    format_file.close()
    command.extend(["-f", str(format_file_path)])

    try:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=BCP_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            diagnostics["stdout_text"] = exc.stdout or ""
            diagnostics["stderr_text"] = exc.stderr or ""
            error_summary = _parse_bcp_error_file(error_file_path)
            diagnostics["error_row_count"] = error_summary["error_row_count"]
            diagnostics["error_summary_json"] = json.dumps(error_summary)
            raise BcpLoadError(
                f"{prefix}BCP load timed out for {table_name} after {BCP_TIMEOUT_SECONDS} seconds",
                diagnostics=diagnostics,
            ) from exc

        diagnostics["exit_code"] = result.returncode
        diagnostics["stdout_text"] = result.stdout or ""
        diagnostics["stderr_text"] = result.stderr or ""
        error_summary = _parse_bcp_error_file(error_file_path)
        diagnostics["error_row_count"] = error_summary["error_row_count"]
        diagnostics["error_summary_json"] = json.dumps(error_summary)

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            detail = stderr or stdout or "Unknown bcp error"
            if error_summary["error_row_count"]:
                detail = (
                    f"{detail} | parsed_bcp_errors={error_summary['error_row_count']} "
                    f"| sample_messages={'; '.join(error_summary['sample_messages'])}"
                )
            raise BcpLoadError(
                f"{prefix}BCP load failed for {table_name}: {detail}",
                diagnostics=diagnostics,
            )
    finally:
        if error_file_path.exists():
            os.remove(error_file_path)
        if format_file_path.exists():
            os.remove(format_file_path)

    return diagnostics


def _build_raw_bcp_format_text(table_name: str) -> str:
    column_specs = []
    schema_name, object_name = table_name.split(".", maxsplit=1)
    raw_columns = _get_raw_columns(schema_name, object_name)

    for index, column_name in enumerate(raw_columns, start=1):
        if index == len(raw_columns):
            terminator = BCP_ROW_TERMINATOR
        else:
            terminator = BCP_FIELD_TERMINATOR
        host_length = 255 if column_name == "SFileName" else 4000
        column_specs.append(
            _render_bcp_format_line(
                field_order=index,
                host_length=host_length,
                terminator=terminator,
                server_order=index,
                column_name=column_name,
            )
        )

    return "\n".join(["14.0", str(len(column_specs)), *column_specs]) + "\n"


def _get_raw_columns(schema_name: str, object_name: str) -> list[str]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
            """,
            schema_name,
            object_name,
        )
        return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def _render_bcp_format_line(
    field_order: int,
    host_length: int,
    terminator: str,
    server_order: int,
    column_name: str,
) -> str:
    return (
        f"{field_order} SQLCHAR 0 {host_length} "
        f"\"{_escape_bcp_format_terminator(terminator)}\" "
        f"{server_order} {column_name} SQL_Latin1_General_CP1_CI_AS"
    )


def _escape_bcp_format_terminator(terminator: str) -> str:
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


def _parse_bcp_error_file(error_file_path: Path) -> dict[str, object]:
    if not error_file_path.exists():
        return _empty_bcp_error_summary()

    raw_text = error_file_path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw_text:
        return _empty_bcp_error_summary()

    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    row_numbers = []
    column_names = []
    normalized_messages = []

    for line in lines:
        row_match = re.search(r"row\s+(\d+)", line, flags=re.IGNORECASE)
        if row_match:
            row_numbers.append(int(row_match.group(1)))

        column_match = re.search(r"column\s+([^\s,:;]+)", line, flags=re.IGNORECASE)
        if column_match:
            column_names.append(column_match.group(1))

        normalized_messages.append(_normalize_bcp_error_message(line))

    unique_rows = sorted(set(row_numbers))
    message_counts = Counter(normalized_messages)
    top_messages = [
        {"message": message, "count": count}
        for message, count in message_counts.most_common(BCP_ERROR_SAMPLE_LIMIT)
    ]

    return {
        "error_line_count": len(lines),
        "error_row_count": len(unique_rows) if unique_rows else len(lines),
        "sample_row_numbers": unique_rows[:BCP_ERROR_SAMPLE_LIMIT],
        "sample_column_names": sorted(set(column_names))[:BCP_ERROR_SAMPLE_LIMIT],
        "sample_messages": [item["message"] for item in top_messages],
        "top_messages": top_messages,
    }


def _normalize_bcp_error_message(line: str) -> str:
    normalized = re.sub(r"\brow\s+\d+\b", "row <n>", line, flags=re.IGNORECASE)
    normalized = re.sub(r"\bline\s+\d+\b", "line <n>", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b\d+\b", "<n>", normalized)
    return normalized


def _empty_bcp_error_summary() -> dict[str, object]:
    return {
        "error_line_count": 0,
        "error_row_count": 0,
        "sample_row_numbers": [],
        "sample_column_names": [],
        "sample_messages": [],
        "top_messages": [],
    }


def _load_chunk_with_pyodbc(
    cursor,
    table_name: str,
    target_columns: list[str],
    bulk_file_path: Path,
    row_count: int,
    source_file_string: str,
    run_id: str | None,
    detail_message: str,
    contains_large_fields: bool = False,
    ) -> None:
    insert_columns = target_columns + ["LoadDate", "SFileName"]
    quoted_columns = ", ".join(f"[{column}]" for column in insert_columns)
    placeholders = ", ".join("?" for _ in insert_columns)
    insert_sql = f"INSERT INTO {table_name} ({quoted_columns}) VALUES ({placeholders})"
    batch_rows: list[tuple[str, ...]] = []
    has_rows = False

    started_at = datetime.now()
    cursor.fast_executemany = not contains_large_fields

    with open(bulk_file_path, encoding=BCP_FILE_ENCODING, newline="") as bulk_file:
        for line in bulk_file:
            line = line.rstrip("\r\n")
            batch_rows.append(tuple(line.split(BCP_FIELD_TERMINATOR)))
            if len(batch_rows) >= PYODBC_FALLBACK_BATCH_SIZE:
                if contains_large_fields:
                    for batch_row in batch_rows:
                        cursor.execute(insert_sql, batch_row)
                else:
                    cursor.executemany(insert_sql, batch_rows)
                batch_rows.clear()
                has_rows = True

    if batch_rows:
        if contains_large_fields:
            for batch_row in batch_rows:
                cursor.execute(insert_sql, batch_row)
        else:
            cursor.executemany(insert_sql, batch_rows)
        has_rows = True

    if not has_rows:
        return

    finished_at = datetime.now()
    insert_load_execution_log(
        run_id=run_id,
        pipeline_name=_pipeline_name_from_table(table_name),
        table_name=table_name,
        source_file=source_file_string,
        load_method="PYODBC",
        load_status="SUCCESS",
        chunk_row_count=row_count,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=int((finished_at - started_at).total_seconds() * 1000),
        detail_message=detail_message,
    )


def _get_loaded_chunk_row_count(
    cursor,
    table_name: str,
    load_timestamp: datetime,
    source_file_string: str,
) -> int:
    load_timestamp_text = load_timestamp.strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        f"""
        SELECT COUNT(*)
        FROM {table_name}
        WHERE [LoadDate] = ?
          AND [SFileName] = ?
        """,
        load_timestamp_text,
        source_file_string,
    )
    result = cursor.fetchone()
    return int(result[0]) if result else 0


def _delete_loaded_chunk_rows(
    cursor,
    table_name: str,
    load_timestamp: datetime,
    source_file_string: str,
) -> None:
    load_timestamp_text = load_timestamp.strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        f"""
        DELETE FROM {table_name}
        WHERE [LoadDate] = ?
          AND [SFileName] = ?
        """,
        load_timestamp_text,
        source_file_string,
    )


def _normalize_loaded_raw_columns(
    cursor,
    schema_name: str,
    object_name: str,
    target_columns: list[str],
) -> None:
    if not target_columns:
        return

    set_clause = ", ".join(
        f"[{column}] = NULLIF(LTRIM(RTRIM([{column}])), '')" for column in target_columns
    )
    cursor.execute(
        f"""
        UPDATE {schema_name}.{object_name}
        SET {set_clause};
        """
    )


def _prepare_input_files(
    source_file: Path,
    delimiter: str,
    has_header: bool,
    chunk_size_mb: int | float | None,
    row_repair_strategy: str | None,
    max_malformed_rows: int,
    header_row_number: int,
    prefix: str,
) -> tuple[list[Path], Path | None]:
    if not has_header:
        return [source_file], None

    if not chunk_size_mb or source_file.stat().st_size <= chunk_size_mb * 1024 * 1024:
        return [source_file], None

    chunk_dir = PROJECT_ROOT / "tmp" / "csv_chunks" / _build_chunk_dir_name(source_file)
    if chunk_dir.exists():
        shutil.rmtree(chunk_dir)
    chunk_dir.mkdir(parents=True, exist_ok=True)

    target_chunk_bytes = int(chunk_size_mb * 1024 * 1024)
    chunk_paths = []
    malformed_row_count = 0

    def _process_reader(reader) -> None:
        nonlocal malformed_row_count

        try:
            first_row = _read_header_row(reader, header_row_number)
        except StopIteration:
            return

        if has_header:
            header = first_row
            data_rows = reader
        else:
            header = build_ordinal_column_names(len(first_row))
            data_rows = chain([first_row], reader)

        chunk_index = 1
        chunk_path, chunk_handle, writer = _open_chunk_file(
            chunk_dir, source_file.stem, chunk_index, delimiter, header
        )
        chunk_paths.append(chunk_path)
        rows_in_chunk = 0
        expected_column_count = len(header)
        try:
            for row_number, repaired_row, _ in iter_repaired_csv_rows(
                data_rows, expected_column_count, row_repair_strategy
            ):
                if repaired_row is None:
                    malformed_row_count += 1
                    if malformed_row_count > max_malformed_rows:
                        raise ValueError(
                            f"Raw load failed: malformed row count {malformed_row_count} exceeds "
                            f"allowed threshold {max_malformed_rows} in {source_file}"
                        )
                    continue

                writer.writerow(repaired_row)
                rows_in_chunk += 1

                if rows_in_chunk % 1000 == 0:
                    chunk_handle.flush()
                    if chunk_path.stat().st_size >= target_chunk_bytes:
                        chunk_handle.close()
                        chunk_index += 1
                        chunk_path, chunk_handle, writer = _open_chunk_file(
                            chunk_dir, source_file.stem, chunk_index, delimiter, header
                        )
                        chunk_paths.append(chunk_path)
                        rows_in_chunk = 0
        finally:
            chunk_handle.close()

    _, source_encoding = _run_with_csv_reader_fallback(
        source_file=source_file,
        delimiter=delimiter,
        processor=_process_reader,
    )
    logger.info(f"{prefix}Reading {source_file.name} using encoding {source_encoding} for chunking")

    logger.info(
        f"{prefix}Split {source_file.name} into {len(chunk_paths)} chunk file(s) "
        f"using target size {chunk_size_mb} MB"
    )
    if malformed_row_count:
        logger.warning(
            f"{prefix}Raw load warning: skipped malformed rows during chunking in "
            f"{source_file}: {malformed_row_count}"
        )
    return chunk_paths, chunk_dir


def _open_chunk_file(
    chunk_dir: Path, file_stem: str, chunk_index: int, delimiter: str, header: list[str]
) -> tuple[Path, object, csv.writer]:
    chunk_path = chunk_dir / f"{file_stem}_chunk_{chunk_index:03d}.csv"
    chunk_handle = open(chunk_path, "w", newline="", encoding="utf-8-sig")
    writer = csv.writer(chunk_handle, delimiter=delimiter)
    writer.writerow(header)
    return chunk_path, chunk_handle, writer


def _read_header_row(reader, header_row_number: int) -> list[str]:
    if header_row_number < 1:
        raise ValueError("header_row_number must be at least 1.")

    for row_number, row in enumerate(reader, start=1):
        if row_number == header_row_number:
            return row

    raise StopIteration


def _cleanup_chunk_dir(chunk_dir: Path | None) -> None:
    if chunk_dir is not None and chunk_dir.exists():
        shutil.rmtree(chunk_dir)


def _build_chunk_dir_name(source_file: Path) -> str:
    source_key = str(source_file).encode("utf-8")
    source_hash = hashlib.sha1(source_key).hexdigest()[:10]
    return f"{source_file.stem}_{source_hash}"


def _table_exists(cursor, schema_name: str, object_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
        """,
        schema_name,
        object_name,
    )
    return cursor.fetchone() is not None


def _ensure_schema_exists(cursor, schema_name: str) -> None:
    cursor.execute(
        """
        SELECT 1
        FROM sys.schemas
        WHERE name = ?
        """,
        schema_name,
    )
    if cursor.fetchone() is None:
        cursor.execute(f"EXEC('CREATE SCHEMA [{schema_name}]')")


def _get_table_columns(cursor, schema_name: str, object_name: str) -> list[str]:
    cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION
        """,
        schema_name,
        object_name,
    )
    return [row[0] for row in cursor.fetchall()]


def _ensure_raw_table_metadata_columns(cursor, schema_name: str, object_name: str) -> None:
    qualified_table = f"{schema_name}.{object_name}"

    if not _column_exists(cursor, schema_name, object_name, "LoadDate"):
        cursor.execute(
            f"""
            ALTER TABLE {qualified_table}
            ADD [LoadDate] DATETIME NULL;
            """
        )
        cursor.execute(
            f"""
            UPDATE {qualified_table}
            SET [LoadDate] = GETDATE()
            WHERE [LoadDate] IS NULL;
            """
        )
        cursor.execute(
            f"""
            ALTER TABLE {qualified_table}
            ALTER COLUMN [LoadDate] DATETIME NOT NULL;
            """
        )

    if not _column_exists(cursor, schema_name, object_name, "SFileName"):
        cursor.execute(
            f"""
            ALTER TABLE {qualified_table}
            ADD [SFileName] VARCHAR(255) NULL;
            """
        )
        cursor.execute(
            f"""
            UPDATE {qualified_table}
            SET [SFileName] = ''
            WHERE [SFileName] IS NULL;
            """
        )
        cursor.execute(
            f"""
            ALTER TABLE {qualified_table}
            ALTER COLUMN [SFileName] VARCHAR(255) NOT NULL;
            """
        )

def _ensure_raw_table_text_column_types(
    cursor,
    schema_name: str,
    object_name: str,
    column_sql_types: dict[str, str],
) -> None:
    if not column_sql_types:
        return

    cursor.execute(
        """
        SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
        """,
        schema_name,
        object_name,
    )
    column_details = {
        row[0]: {
            "data_type": str(row[1]).lower(),
            "max_length": row[2],
        }
        for row in cursor.fetchall()
    }

    for column_name, expected_sql_type in column_sql_types.items():
        details = column_details.get(column_name)
        if details is None:
            continue

        if _column_matches_expected_sql_type(details, expected_sql_type):
            continue

        cursor.execute(
            f"""
            ALTER TABLE {schema_name}.{object_name}
            ALTER COLUMN [{column_name}] {expected_sql_type} NULL;
            """
        )


def _column_matches_expected_sql_type(
    details: dict[str, object | None],
    expected_sql_type: str,
) -> bool:
    expected_upper = expected_sql_type.upper()
    data_type = str(details["data_type"]).upper()
    max_length = details["max_length"]

    if expected_upper == "VARCHAR(MAX)":
        return data_type == "VARCHAR" and max_length == -1

    if expected_upper == "VARCHAR(4000)":
        return data_type == "VARCHAR" and max_length == 4000

    return False


def _normalize_raw_text_sql_type(sql_type: str) -> str:
    normalized = sql_type.strip().upper()
    if normalized in {"VARCHAR(4000)", "VARCHAR(MAX)"}:
        return normalized

    raise ValueError(
        "Raw load failed: only VARCHAR(4000) and VARCHAR(MAX) are supported "
        f"for column_sql_types overrides, got {sql_type!r}."
    )


def _column_exists(cursor, schema_name: str, object_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = ?
        """,
        schema_name,
        object_name,
        column_name,
    )
    return cursor.fetchone() is not None


def _pipeline_name_from_table(table_name: str) -> str:
    return table_name.split(".", maxsplit=1)[1]


def _safe_int(value: object | None) -> int | None:
    if value is None:
        return None
    return int(value)


def _safe_text(value: object | None) -> str | None:
    if value is None:
        return None
    return str(value)
