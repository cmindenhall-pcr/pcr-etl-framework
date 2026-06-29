import re
from datetime import datetime

from src.db_connection import get_connection
from src.numeric_normalization import normalize_numeric_candidate


def _ensure_audit_schema_exists(cursor) -> None:
    cursor.execute(
        """
        IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'audit')
        BEGIN
            EXEC('CREATE SCHEMA audit')
        END
        """
    )


def _ensure_pipeline_run_log_exists(cursor) -> None:
    _ensure_audit_schema_exists(cursor)
    cursor.execute(
        """
        IF OBJECT_ID('audit.PipelineRunLog', 'U') IS NULL
        BEGIN
            CREATE TABLE audit.PipelineRunLog (
                PipelineRunID INT IDENTITY(1,1) PRIMARY KEY,
                RunID VARCHAR(50) NOT NULL,
                PipelineName VARCHAR(100) NOT NULL,
                StartTime DATETIME2 NOT NULL,
                EndTime DATETIME2 NULL,
                Status VARCHAR(20) NOT NULL,
                SourceTable VARCHAR(200) NULL,
                TargetTable VARCHAR(200) NULL,
                SourceRowCount INT NULL,
                TargetRowCount INT NULL,
                ErrorMessage VARCHAR(4000) NULL
            );
        END

        IF COL_LENGTH('audit.PipelineRunLog', 'DurationMs') IS NULL
        BEGIN
            ALTER TABLE audit.PipelineRunLog
            ADD DurationMs INT NULL;
        END
        """
    )


def _ensure_table_row_count_log_exists(cursor) -> None:
    _ensure_audit_schema_exists(cursor)
    cursor.execute(
        """
        IF OBJECT_ID('audit.TableRowCountLog', 'U') IS NULL
        BEGIN
            CREATE TABLE audit.TableRowCountLog (
                TableRowCountLogID INT IDENTITY(1,1) PRIMARY KEY,
                RunID VARCHAR(50) NOT NULL,
                PipelineName VARCHAR(100) NOT NULL,
                TableName VARCHAR(200) NOT NULL,
                SourceFile VARCHAR(4000) NULL,
                SourceFolderPath VARCHAR(4000) NULL,
                CountStage VARCHAR(50) NOT NULL,
                ProperRowCount INT NOT NULL,
                MalformedRowCount INT NOT NULL,
                RepairedRowCount INT NOT NULL CONSTRAINT DF_TableRowCountLog_RepairedRowCount DEFAULT (0),
                CapturedAt DATETIME2 NOT NULL
            );
        END

        IF COL_LENGTH('audit.TableRowCountLog', 'SourceFolderPath') IS NULL
        BEGIN
            ALTER TABLE audit.TableRowCountLog
            ADD SourceFolderPath VARCHAR(4000) NULL;
        END

        IF COL_LENGTH('audit.TableRowCountLog', 'RepairedRowCount') IS NULL
        BEGIN
            ALTER TABLE audit.TableRowCountLog
            ADD RepairedRowCount INT NOT NULL
                CONSTRAINT DF_TableRowCountLog_RepairedRowCount DEFAULT (0);
        END
        """
    )


def _ensure_column_profile_log_exists(cursor) -> None:
    _ensure_audit_schema_exists(cursor)
    cursor.execute(
        """
        IF OBJECT_ID('audit.ColumnProfileLog', 'U') IS NULL
        BEGIN
            CREATE TABLE audit.ColumnProfileLog (
                RunID VARCHAR(50) NOT NULL,
                PipelineName VARCHAR(100) NOT NULL,
                TableName VARCHAR(200) NOT NULL,
                SourceFile VARCHAR(4000) NULL,
                SourceFolderPath VARCHAR(4000) NULL,
                CountStage VARCHAR(50) NOT NULL,
                OrdinalPosition INT NULL,
                ColumnName VARCHAR(200) NOT NULL,
                MinimumNonNullValue NVARCHAR(4000) NULL,
                MaximumNonNullValue NVARCHAR(4000) NULL,
                MaxStringLength INT NULL,
                BlankRowCount INT NOT NULL,
                NonNullRowCount INT NOT NULL,
                NullableFlag BIT NOT NULL,
                Nullability VARCHAR(10) NOT NULL,
                RecommendedSqlType VARCHAR(100) NULL,
                DefinedSqlType VARCHAR(100) NOT NULL,
                MinMaxValueTypeMismatchFlag BIT NOT NULL
                    CONSTRAINT DF_ColumnProfileLog_MinMaxValueTypeMismatchFlag DEFAULT (0),
                CapturedAt DATETIME2 NOT NULL,
                AutoId BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY
            );
        END

        IF COL_LENGTH('audit.ColumnProfileLog', 'MinimumNonNullValue') IS NULL
        BEGIN
            ALTER TABLE audit.ColumnProfileLog
            ADD MinimumNonNullValue NVARCHAR(4000) NULL;
        END

        IF COL_LENGTH('audit.ColumnProfileLog', 'MaximumNonNullValue') IS NULL
        BEGIN
            ALTER TABLE audit.ColumnProfileLog
            ADD MaximumNonNullValue NVARCHAR(4000) NULL;
        END

        IF COL_LENGTH('audit.ColumnProfileLog', 'SourceFolderPath') IS NULL
        BEGIN
            ALTER TABLE audit.ColumnProfileLog
            ADD SourceFolderPath VARCHAR(4000) NULL;
        END

        IF COL_LENGTH('audit.ColumnProfileLog', 'OrdinalPosition') IS NULL
        BEGIN
            ALTER TABLE audit.ColumnProfileLog
            ADD OrdinalPosition INT NULL;
        END

        IF COL_LENGTH('audit.ColumnProfileLog', 'RecommendedSqlType') IS NULL
        BEGIN
            ALTER TABLE audit.ColumnProfileLog
            ADD RecommendedSqlType VARCHAR(100) NULL;
        END

        IF COL_LENGTH('audit.ColumnProfileLog', 'MinMaxValueTypeMismatchFlag') IS NULL
        BEGIN
            ALTER TABLE audit.ColumnProfileLog
            ADD MinMaxValueTypeMismatchFlag BIT NOT NULL
                CONSTRAINT DF_ColumnProfileLog_MinMaxValueTypeMismatchFlag DEFAULT (0)
                WITH VALUES;
        END

        IF COL_LENGTH('audit.ColumnProfileLog', 'AutoId') IS NOT NULL
           AND NOT EXISTS (
               SELECT 1
               FROM sys.indexes
               WHERE name = 'UX_audit_ColumnProfileLog_AutoId'
                 AND object_id = OBJECT_ID('audit.ColumnProfileLog')
           )
        BEGIN
            CREATE UNIQUE INDEX [UX_audit_ColumnProfileLog_AutoId]
            ON audit.ColumnProfileLog ([AutoId]);
        END
        """
    )


def _ensure_load_execution_log_exists(cursor) -> None:
    _ensure_audit_schema_exists(cursor)
    cursor.execute(
        """
        IF OBJECT_ID('audit.LoadExecutionLog', 'U') IS NULL
        BEGIN
            CREATE TABLE audit.LoadExecutionLog (
                LoadExecutionLogID INT IDENTITY(1,1) PRIMARY KEY,
                RunID VARCHAR(50) NULL,
                PipelineName VARCHAR(100) NULL,
                TableName VARCHAR(200) NOT NULL,
                SourceFile VARCHAR(4000) NULL,
                LoadMethod VARCHAR(50) NOT NULL,
                LoadStatus VARCHAR(50) NOT NULL,
                ChunkRowCount INT NOT NULL,
                StartedAt DATETIME2 NOT NULL,
                FinishedAt DATETIME2 NOT NULL,
                DurationMs INT NOT NULL,
                ServerName VARCHAR(255) NULL,
                ExecutablePath VARCHAR(4000) NULL,
                ExitCode INT NULL,
                DetailMessage NVARCHAR(4000) NULL,
                BcpErrorRowCount INT NULL,
                BcpErrorSummary NVARCHAR(4000) NULL,
                StdOut NVARCHAR(4000) NULL,
                StdErr NVARCHAR(4000) NULL
            );
        END

        IF COL_LENGTH('audit.LoadExecutionLog', 'BcpErrorRowCount') IS NULL
        BEGIN
            ALTER TABLE audit.LoadExecutionLog
            ADD BcpErrorRowCount INT NULL;
        END

        IF COL_LENGTH('audit.LoadExecutionLog', 'BcpErrorSummary') IS NULL
        BEGIN
            ALTER TABLE audit.LoadExecutionLog
            ADD BcpErrorSummary NVARCHAR(4000) NULL;
        END
        """
    )


def truncate_audit_tables() -> None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        _ensure_audit_schema_exists(cursor)
        cursor.execute(
            """
            IF OBJECT_ID('audit.ColumnProfileLog', 'U') IS NOT NULL
                TRUNCATE TABLE audit.ColumnProfileLog;

            IF OBJECT_ID('audit.TableRowCountLog', 'U') IS NOT NULL
                TRUNCATE TABLE audit.TableRowCountLog;

            IF OBJECT_ID('audit.LoadExecutionLog', 'U') IS NOT NULL
                TRUNCATE TABLE audit.LoadExecutionLog;

            IF OBJECT_ID('audit.PipelineRunLog', 'U') IS NOT NULL
                TRUNCATE TABLE audit.PipelineRunLog;
            """
        )
        conn.commit()
    finally:
        conn.close()


def insert_pipeline_run_start(
    run_id: str,
    pipeline_name: str,
    start_time: datetime,
    source_table: str,
    target_table: str,
) -> None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        _ensure_pipeline_run_log_exists(cursor)
        cursor.execute(
            """
            INSERT INTO audit.PipelineRunLog
                (RunID, PipelineName, StartTime, Status, SourceTable, TargetTable)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            run_id,
            pipeline_name,
            start_time,
            "RUNNING",
            source_table,
            target_table,
        )
        conn.commit()
    finally:
        conn.close()


def update_pipeline_run_end(
    run_id: str,
    end_time: datetime,
    status: str,
    source_row_count: int | None = None,
    target_row_count: int | None = None,
    error_message: str | None = None,
) -> None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        _ensure_pipeline_run_log_exists(cursor)

        cursor.execute(
            """
            SELECT StartTime
            FROM audit.PipelineRunLog
            WHERE RunID = ?
            """,
            run_id,
        )
        row = cursor.fetchone()

        duration_ms = None
        if row and row[0]:
            start_time = row[0]
            duration_ms = int((end_time - start_time).total_seconds() * 1000)

        cursor.execute(
            """
            UPDATE audit.PipelineRunLog
            SET
                EndTime = ?,
                Status = ?,
                SourceRowCount = ?,
                TargetRowCount = ?,
                ErrorMessage = ?,
                DurationMs = ?
            WHERE RunID = ?
            """,
            end_time,
            status,
            source_row_count,
            target_row_count,
            error_message,
            duration_ms,
            run_id,
        )
        conn.commit()
    finally:
        conn.close()


def insert_table_row_count(
    run_id: str,
    pipeline_name: str,
    table_name: str,
    count_stage: str,
    row_count: int,
    malformed_row_count: int,
    captured_at: datetime,
    repaired_row_count: int = 0,
    source_file: str | None = None,
    source_folder_path: str | None = None,
) -> None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        _ensure_table_row_count_log_exists(cursor)
        cursor.execute(
            """
            INSERT INTO audit.TableRowCountLog
                (
                    RunID,
                    PipelineName,
                    TableName,
                    SourceFile,
                    SourceFolderPath,
                    CountStage,
                    ProperRowCount,
                    MalformedRowCount,
                    RepairedRowCount,
                    CapturedAt
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            run_id,
            pipeline_name,
            table_name,
            source_file,
            source_folder_path,
            count_stage,
            row_count,
            malformed_row_count,
            repaired_row_count,
            captured_at,
        )
        conn.commit()
    finally:
        conn.close()


def _get_prior_defined_sql_types(
    cursor,
    table_name: str,
    count_stage: str,
) -> dict[str, str]:
    exact_match_types = _get_prior_defined_sql_types_for_table(cursor, table_name, count_stage)
    if exact_match_types:
        return exact_match_types

    fallback_table_name = _strip_trailing_numeric_table_suffix(table_name)
    if fallback_table_name == table_name:
        return {}

    return _get_prior_defined_sql_types_for_table(cursor, fallback_table_name, count_stage)


def _get_prior_defined_sql_types_for_table(
    cursor,
    table_name: str,
    count_stage: str,
) -> dict[str, str]:
    cursor.execute(
        """
        WITH ranked_profiles AS (
            SELECT
                ColumnName,
                DefinedSqlType,
                ROW_NUMBER() OVER (
                    PARTITION BY ColumnName
                    ORDER BY AutoId DESC
                ) AS row_number
            FROM audit.ColumnProfileLog
            WHERE TableName = ?
              AND CountStage = ?
        )
        SELECT ColumnName, DefinedSqlType
        FROM ranked_profiles
        WHERE row_number = 1
        """,
        table_name,
        count_stage,
    )
    return {row[0]: row[1] for row in cursor.fetchall()}


def _strip_trailing_numeric_table_suffix(table_name: str) -> str:
    schema_name, object_name = table_name.split(".", maxsplit=1)
    object_parts = object_name.split("_")
    if len(object_parts) < 2:
        return table_name

    trailing_part = object_parts[-1]
    preceding_part = object_parts[-2]
    if not trailing_part.isdigit() or preceding_part.isdigit():
        return table_name

    base_name = "_".join(object_parts[:-1])
    return f"{schema_name}.{base_name}"


def insert_column_profile_rows(
    run_id: str,
    pipeline_name: str,
    table_name: str,
    count_stage: str,
    column_length_profile: dict[str, dict[str, int | str | None]],
    captured_at: datetime,
    source_file: str | None = None,
    source_folder_path: str | None = None,
) -> None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        _ensure_column_profile_log_exists(cursor)
        prior_defined_sql_types = _get_prior_defined_sql_types(cursor, table_name, count_stage)
        rows = []
        for column_name, profile in column_length_profile.items():
            minimum_non_null_value = _truncate_text(_safe_profile_text(profile["minimum_non_null_value"]))
            maximum_non_null_value = _truncate_text(_safe_profile_text(profile["maximum_non_null_value"]))
            min_max_mismatch_flag = _compute_min_max_value_type_mismatch_flag(
                minimum_non_null_value,
                maximum_non_null_value,
            )
            rows.append(
                (
                    run_id,
                    pipeline_name,
                    table_name,
                    source_file,
                    source_folder_path,
                    count_stage,
                    profile["ordinal_position"],
                    column_name,
                    minimum_non_null_value,
                    maximum_non_null_value,
                    profile["max_string_length"],
                    profile["blank_row_count"],
                    profile["non_null_row_count"],
                    profile["nullable_flag"],
                    profile["nullability"],
                    _recommend_sql_type(
                        column_name=column_name,
                        minimum_non_null_value=minimum_non_null_value,
                        maximum_non_null_value=maximum_non_null_value,
                        max_string_length=profile["max_string_length"],
                        min_max_mismatch_flag=min_max_mismatch_flag,
                    ),
                    prior_defined_sql_types.get(column_name, profile["defined_sql_type"]),
                    min_max_mismatch_flag,
                    captured_at,
                )
            )

        if not rows:
            return

        cursor.executemany(
            """
            INSERT INTO audit.ColumnProfileLog
                (
                    RunID,
                    PipelineName,
                    TableName,
                    SourceFile,
                    SourceFolderPath,
                    CountStage,
                    OrdinalPosition,
                    ColumnName,
                    MinimumNonNullValue,
                    MaximumNonNullValue,
                    MaxStringLength,
                    BlankRowCount,
                    NonNullRowCount,
                    NullableFlag,
                    Nullability,
                    RecommendedSqlType,
                    DefinedSqlType,
                    MinMaxValueTypeMismatchFlag,
                    CapturedAt
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def insert_load_execution_log(
    table_name: str,
    load_method: str,
    load_status: str,
    chunk_row_count: int,
    started_at: datetime,
    finished_at: datetime,
    duration_ms: int,
    run_id: str | None = None,
    pipeline_name: str | None = None,
    source_file: str | None = None,
    server_name: str | None = None,
    executable_path: str | None = None,
    exit_code: int | None = None,
    detail_message: str | None = None,
    bcp_error_row_count: int | None = None,
    bcp_error_summary: str | None = None,
    stdout_text: str | None = None,
    stderr_text: str | None = None,
) -> None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        _ensure_load_execution_log_exists(cursor)
        cursor.execute(
            """
            INSERT INTO audit.LoadExecutionLog
                (
                    RunID,
                    PipelineName,
                    TableName,
                    SourceFile,
                    LoadMethod,
                    LoadStatus,
                    ChunkRowCount,
                    StartedAt,
                    FinishedAt,
                    DurationMs,
                    ServerName,
                    ExecutablePath,
                    ExitCode,
                    DetailMessage,
                    BcpErrorRowCount,
                    BcpErrorSummary,
                    StdOut,
                    StdErr
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            run_id,
            pipeline_name,
            table_name,
            source_file,
            load_method,
            load_status,
            chunk_row_count,
            started_at,
            finished_at,
            duration_ms,
            server_name,
            executable_path,
            exit_code,
            _truncate_text(detail_message),
            bcp_error_row_count,
            _truncate_text(bcp_error_summary),
            _truncate_text(stdout_text),
            _truncate_text(stderr_text),
        )
        conn.commit()
    finally:
        conn.close()


def _truncate_text(value: str | None, max_length: int = 4000) -> str | None:
    if value is None:
        return None
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


def _safe_profile_text(value: int | str | None) -> str | None:
    if value is None:
        return None
    return str(value)


def _compute_min_max_value_type_mismatch_flag(
    minimum_non_null_value: str | None,
    maximum_non_null_value: str | None,
) -> int:
    minimum_value_type = _infer_profile_value_type(minimum_non_null_value)
    maximum_value_type = _infer_profile_value_type(maximum_non_null_value)

    if minimum_value_type is None or maximum_value_type is None:
        return 0

    return 1 if minimum_value_type != maximum_value_type else 0


DATE_NAME_TOKENS = {"DATE", "DT", "DOB", "BIRTHDATE"}
AMOUNT_NAME_TOKENS = {"AMOUNT", "AMT", "TOTAL", "PRICE", "COST", "BALANCE", "CHARGE"}
RATE_NAME_TOKENS = {"RATE", "PERCENT", "PCT", "PERCENTAGE", "RATIO", "DISC"}
QUANTITY_NAME_TOKENS = {
    "QTY",
    "QUANTITY",
    "COUNT",
    "COUNTS",
    "DAYS",
    "DAY",
    "HOURS",
    "HOUR",
    "UNITS",
    "UNIT",
    "SEQ",
    "SEQUENCE",
    "LINE",
    "LINES",
}
IDENTIFIER_NAME_TOKENS = {"ID", "CODE", "TYPE", "INVOICE", "ORDER", "BUYER", "VENDOR", "COMPANY", "PO", "ITEM", "LOCATION", "GROUP", "CLASS", "STATUS", "REF", "OBJ", "TAG", "NUMBER"}
COLUMN_SEMANTIC_OVERRIDES = {
    "CASHLEDGERTRANSACTION": "quantity",
    "CREDITRECEIVED": "amount",
    "ORIGINALPURCHASEORDER": "identifier",
    "RETURNVALUE": "amount",
}


def _recommend_sql_type(
    column_name: str,
    minimum_non_null_value: str | None,
    maximum_non_null_value: str | None,
    max_string_length: int | None,
    min_max_mismatch_flag: int,
) -> str:
    varchar_type = _varchar_type(max_string_length)
    semantic_kind = _classify_column_semantics(column_name)
    minimum_value_type = _infer_profile_value_type(minimum_non_null_value)
    maximum_value_type = _infer_profile_value_type(maximum_non_null_value)

    if semantic_kind == "identifier":
        if minimum_value_type == "date" and maximum_value_type == "date":
            return "DATE"
        return varchar_type

    if min_max_mismatch_flag == 1:
        return varchar_type

    if semantic_kind == "date":
        return "DATE" if minimum_value_type == "date" and maximum_value_type == "date" else varchar_type

    if semantic_kind == "amount":
        return "MONEY" if minimum_value_type == "numeric" and maximum_value_type == "numeric" else varchar_type

    if semantic_kind == "rate":
        return (
            "DECIMAL(17, 4)"
            if _is_rate_like_numeric_value(minimum_non_null_value)
            or _is_rate_like_numeric_value(maximum_non_null_value)
            else varchar_type
        )

    if semantic_kind == "quantity":
        if _is_integer_like_value(minimum_non_null_value) and _is_integer_like_value(maximum_non_null_value):
            return "INT"
        if minimum_value_type == "numeric" and maximum_value_type == "numeric":
            return "DECIMAL(17, 4)"
        return varchar_type

    if minimum_value_type == "date" and maximum_value_type == "date":
        return "DATE"

    return varchar_type


def _classify_column_semantics(column_name: str) -> str:
    override = COLUMN_SEMANTIC_OVERRIDES.get(_canonical_column_name(column_name))
    if override is not None:
        return override

    tokens = _tokenize_column_name(column_name)

    if tokens & DATE_NAME_TOKENS:
        return "date"
    if tokens & RATE_NAME_TOKENS:
        return "rate"
    if tokens & {"DECIMAL", "DECIMALS"}:
        return "quantity"
    if tokens & AMOUNT_NAME_TOKENS:
        return "amount"
    if tokens & QUANTITY_NAME_TOKENS:
        return "quantity"
    if tokens & IDENTIFIER_NAME_TOKENS:
        return "identifier"

    upper_name = column_name.upper()
    if "DATE" in upper_name or upper_name.endswith("_DT") or upper_name.endswith("DT"):
        return "date"
    if "RATE" in upper_name or "PERCENT" in upper_name or upper_name.endswith("_R"):
        return "rate"
    if "AMOUNT" in upper_name or upper_name.endswith("_AMT") or "TOTAL" in upper_name:
        return "amount"
    if upper_name.endswith("_COUNT") or upper_name.endswith("COUNT") or upper_name.endswith("_QTY"):
        return "quantity"

    return "generic"


def _tokenize_column_name(column_name: str) -> set[str]:
    camel_split = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", column_name)
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", camel_split)
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", camel_split)
    return {token.upper() for token in normalized.split("_") if token}


def _canonical_column_name(column_name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", column_name.upper())


def _is_integer_like_value(value: str | None) -> bool:
    normalized_value = normalize_numeric_candidate(value)
    if normalized_value is None:
        return False
    if re.fullmatch(r"[+-]?\d+", normalized_value):
        return True
    if re.fullmatch(r"[+-]?\d+\.0+", normalized_value):
        return True
    return False


def _is_rate_like_numeric_value(value: str | None) -> bool:
    normalized_value = normalize_numeric_candidate(value)
    if normalized_value is None:
        return False
    if "." not in normalized_value:
        return False
    return len(normalized_value.split(".", maxsplit=1)[1]) > 2


def _varchar_type(max_string_length: int | None) -> str:
    if max_string_length is None:
        return "VARCHAR(100)"
    if max_string_length <= 100:
        return "VARCHAR(100)"
    if max_string_length <= 255:
        return "VARCHAR(255)"
    if max_string_length <= 1000:
        return "VARCHAR(1000)"
    return "VARCHAR(4000)"


def _infer_profile_value_type(value: str | None) -> str | None:
    if value is None:
        return None

    normalized_value = value.strip()
    if normalized_value == "":
        return None

    if _is_datetime_like_value(normalized_value):
        return "date"

    if _is_numeric_like_value(normalized_value):
        return "numeric"

    return "text"


def _is_numeric_like_value(value: str) -> bool:
    return normalize_numeric_candidate(value) is not None


def _is_datetime_like_value(value: str) -> bool:
    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?)?",
        value,
    ):
        return True

    if re.fullmatch(
        r"\d{2}-[A-Za-z]{3}-\d{2}(?:\s+\d{2}\.\d{2}\.\d{2}\.\d{1,9}\s+(?:AM|PM))?",
        value,
        re.IGNORECASE,
    ):
        return True

    datetime_formats = (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%m/%d/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
    )

    for datetime_format in datetime_formats:
        try:
            datetime.strptime(value, datetime_format)
            return True
        except ValueError:
            continue

    return False
