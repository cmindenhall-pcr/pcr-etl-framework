from datetime import datetime

from src.audit_logger import insert_load_execution_log
from src.csv_utils import sanitize_column_name
from src.date_normalization import sql_date_normalization_expression
from src.db_connection import get_connection
from src.identifier_normalization import (
    is_identifier_like_column,
    sql_identifier_normalization_expression,
)
from src.logger import get_logger
from src.numeric_normalization import sql_numeric_normalization_expression

logger = get_logger()
RAW_METADATA_COLUMNS = {"LoadDate", "SFileName"}
STAGING_IDENTITY_COLUMN = "AutoId"


def load_raw_to_staging(
    source_table: str,
    staging_table: str,
    pipeline_name: str | None = None,
    run_id: str | None = None,
) -> None:
    prefix = f"[run_id={run_id}] " if run_id else ""
    raw_rows = _get_raw_table_columns(source_table)
    profile_rows = _get_latest_column_profiles(source_table)
    if profile_rows:
        all_rows = _merge_profile_rows_with_raw_metadata(profile_rows, raw_rows)
    else:
        all_rows = raw_rows

    if not all_rows:
        raise RuntimeError(
            f"Staging load failed for {staging_table}: no column definitions found for {source_table}."
        )

    staging_rows = _build_staging_rows(all_rows, staging_table)
    data_column_definitions = [
        f"    [{row['staging_column_name']}] {row['defined_sql_type']} {row['nullability']}"
        for row in staging_rows
    ]
    column_definitions = ",\n".join(
        [
            *data_column_definitions,
            f"    [{STAGING_IDENTITY_COLUMN}] INT IDENTITY(1,1) NOT NULL",
        ]
    )
    insert_columns = ", ".join(f"[{row['staging_column_name']}]" for row in staging_rows)
    select_columns = ",\n".join(
        f"    {_build_select_expression(row['source_column_name'], row['defined_sql_type'])} AS [{row['staging_column_name']}]"
        for row in staging_rows
    )

    schema_name, object_name = staging_table.split(".", maxsplit=1)

    conn = get_connection()
    started_at = datetime.now()
    try:
        cursor = conn.cursor()
        _ensure_schema_exists(cursor, schema_name)
        logger.info(f"{prefix}Rebuilding staging table {staging_table} from {source_table}")

        cursor.execute(
            f"""
            IF OBJECT_ID('{schema_name}.{object_name}', 'U') IS NOT NULL
                DROP TABLE {schema_name}.{object_name};

            CREATE TABLE {schema_name}.{object_name} (
{column_definitions}
            );
            """
        )
        cursor.execute(
            f"""
            INSERT INTO {schema_name}.{object_name} ({insert_columns})
            SELECT
{select_columns}
            FROM {source_table};
            """
        )
        conn.commit()
        finished_at = datetime.now()
        row_count = _get_table_row_count(cursor, staging_table)
        insert_load_execution_log(
            run_id=run_id,
            pipeline_name=pipeline_name,
            table_name=staging_table,
            source_file=source_table,
            load_method="RAW_TO_STG",
            load_status="SUCCESS",
            chunk_row_count=row_count,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            detail_message=f"Rebuilt {staging_table} from {source_table}.",
        )
        logger.info(f"{prefix}Loaded staging table {staging_table} from {source_table}")
    except Exception as e:
        conn.rollback()
        finished_at = datetime.now()
        insert_load_execution_log(
            run_id=run_id,
            pipeline_name=pipeline_name,
            table_name=staging_table,
            source_file=source_table,
            load_method="RAW_TO_STG",
            load_status="FAILED",
            chunk_row_count=0,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            detail_message=str(e),
        )
        logger.exception(f"{prefix}Failed loading staging table {staging_table}")
        raise RuntimeError(
            f"Staging load failed for {staging_table} from {source_table}: {e}"
        ) from e
    finally:
        conn.close()


def _build_staging_rows(
    source_rows: list[dict[str, str]],
    staging_table: str,
) -> list[dict[str, str]]:
    staging_rows = []
    seen_staging_names: dict[str, str] = {}

    for row in source_rows:
        source_column_name = row["column_name"]
        staging_column_name = _normalize_staging_column_name(source_column_name)
        if staging_column_name.lower() == STAGING_IDENTITY_COLUMN.lower():
            raise RuntimeError(
                f"Staging load failed for {staging_table}: source column "
                f"{source_column_name!r} conflicts with reserved staging identity "
                f"column {STAGING_IDENTITY_COLUMN!r}."
            )

        existing_source_column = seen_staging_names.get(staging_column_name.lower())

        if existing_source_column is not None:
            raise RuntimeError(
                f"Staging load failed for {staging_table}: columns "
                f"{existing_source_column!r} and {source_column_name!r} both normalize "
                f"to staging column {staging_column_name!r}."
            )

        seen_staging_names[staging_column_name.lower()] = source_column_name
        staging_rows.append(
            {
                **row,
                "source_column_name": source_column_name,
                "staging_column_name": staging_column_name,
            }
        )

    return staging_rows


def _normalize_staging_column_name(column_name: str) -> str:
    if column_name in RAW_METADATA_COLUMNS:
        return column_name

    return sanitize_column_name(column_name)


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


def _get_latest_column_profiles(source_table: str) -> list[dict[str, str]]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            WITH latest_run AS (
                SELECT TOP 1 RunID
                FROM audit.ColumnProfileLog
                WHERE TableName = ?
                  AND CountStage = 'PRELOAD_CSV'
                ORDER BY AutoId DESC
            )
            SELECT
                c.ColumnName,
                COALESCE(NULLIF(c.RecommendedSqlType, ''), c.DefinedSqlType) AS StagingSqlType,
                c.Nullability
            FROM audit.ColumnProfileLog c
            INNER JOIN latest_run lr
                ON c.RunID = lr.RunID
            WHERE c.TableName = ?
              AND c.CountStage = 'PRELOAD_CSV'
            ORDER BY
                CASE WHEN c.OrdinalPosition IS NULL THEN 1 ELSE 0 END,
                c.OrdinalPosition,
                c.AutoId
            """,
            source_table,
            source_table,
        )
        return [
            {
                "column_name": row[0],
                "defined_sql_type": row[1],
                "nullability": _normalize_profile_nullability(
                    defined_sql_type=row[1],
                    profile_nullability=row[2],
                ),
            }
            for row in cursor.fetchall()
        ]
    finally:
        conn.close()


def _normalize_profile_nullability(
    defined_sql_type: str,
    profile_nullability: str,
) -> str:
    # Date-like fields are loaded with TRY_CAST, so invalid source tokens can
    # legitimately become NULL during raw -> stg conversion even when the
    # profile log recorded the source text column as non-blank.
    if _is_date_like_type(defined_sql_type.upper()):
        return "NULL"

    return profile_nullability


def _merge_profile_rows_with_raw_metadata(
    profile_rows: list[dict[str, str]],
    raw_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    raw_rows_by_name = {row["column_name"]: row for row in raw_rows}
    profile_rows = [
        _preserve_raw_max_text_type(profile_row, raw_rows_by_name.get(profile_row["column_name"]))
        for profile_row in profile_rows
    ]
    raw_metadata_by_name = {
        row["column_name"]: row
        for row in raw_rows
        if row["column_name"] in RAW_METADATA_COLUMNS
    }
    merged_rows = list(profile_rows)

    for metadata_column in ("LoadDate", "SFileName"):
        metadata_row = raw_metadata_by_name.get(metadata_column)
        if metadata_row is not None:
            merged_rows.append(metadata_row)

    return merged_rows


def _preserve_raw_max_text_type(
    profile_row: dict[str, str],
    raw_row: dict[str, str] | None,
) -> dict[str, str]:
    if raw_row is None:
        return profile_row

    raw_sql_type = raw_row["defined_sql_type"].upper()
    profile_sql_type = profile_row["defined_sql_type"].upper()
    if raw_sql_type in {"VARCHAR(MAX)", "NVARCHAR(MAX)"} and _is_varchar_type(profile_sql_type):
        return {
            **profile_row,
            "defined_sql_type": raw_row["defined_sql_type"],
        }

    return profile_row


def _get_raw_table_columns(source_table: str) -> list[dict[str, str]]:
    schema_name, object_name = source_table.split(".", maxsplit=1)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                COLUMN_NAME,
                DATA_TYPE,
                CHARACTER_MAXIMUM_LENGTH,
                NUMERIC_PRECISION,
                NUMERIC_SCALE,
                IS_NULLABLE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ?
              AND TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
            """,
            schema_name,
            object_name,
        )
        rows = []
        for row in cursor.fetchall():
            rows.append(
                {
                    "column_name": row[0],
                    "defined_sql_type": _build_sql_type(
                        data_type=row[1],
                        character_maximum_length=row[2],
                        numeric_precision=row[3],
                        numeric_scale=row[4],
                    ),
                    "nullability": (
                        _get_metadata_nullability(row[5])
                        if row[0] in RAW_METADATA_COLUMNS
                        else _get_nullability_for_type(
                            _build_sql_type(
                                data_type=row[1],
                                character_maximum_length=row[2],
                                numeric_precision=row[3],
                                numeric_scale=row[4],
                            )
                        )
                    ),
                }
            )
        return rows
    finally:
        conn.close()


def _get_metadata_nullability(is_nullable: str) -> str:
    return "NULL" if is_nullable == "YES" else "NOT NULL"


def _get_nullability_for_type(defined_sql_type: str) -> str:
    upper_type = defined_sql_type.upper()

    if _is_date_like_type(upper_type):
        return "NULL"

    if _is_numeric_type(upper_type) or _is_varchar_type(upper_type):
        return "NOT NULL"

    return "NULL"


def _get_table_row_count(cursor, table_name: str) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _build_select_expression(column_name: str, defined_sql_type: str) -> str:
    quoted_column = f"[{column_name}]"
    if column_name in RAW_METADATA_COLUMNS:
        return quoted_column

    normalized_value = f"NULLIF(LTRIM(RTRIM({quoted_column})), '')"
    upper_type = defined_sql_type.upper()

    if _is_date_like_type(upper_type):
        return sql_date_normalization_expression(normalized_value, defined_sql_type)

    if _is_numeric_type(upper_type):
        numeric_value = sql_numeric_normalization_expression(normalized_value)
        return (
            f"ISNULL("
            f"TRY_CAST({numeric_value} AS {defined_sql_type}), "
            f"CAST(0 AS {defined_sql_type})"
            f")"
        )

    if _is_varchar_type(upper_type):
        if is_identifier_like_column(column_name):
            identifier_value = sql_identifier_normalization_expression(quoted_column)
            return f"CAST(ISNULL({identifier_value}, '') AS {defined_sql_type})"
        return f"CAST(ISNULL({normalized_value}, '') AS {defined_sql_type})"

    return f"TRY_CAST({normalized_value} AS {defined_sql_type})"


def _is_date_like_type(defined_sql_type: str) -> bool:
    return defined_sql_type.startswith(("DATE", "DATETIME", "SMALLDATETIME"))


def _is_numeric_type(defined_sql_type: str) -> bool:
    return defined_sql_type.startswith(
        ("DECIMAL", "NUMERIC", "INT", "BIGINT", "SMALLINT", "TINYINT", "MONEY")
    )


def _is_varchar_type(defined_sql_type: str) -> bool:
    return defined_sql_type.startswith(("VARCHAR", "NVARCHAR", "CHAR", "NCHAR"))


def _build_sql_type(
    data_type: str,
    character_maximum_length: int | None,
    numeric_precision: int | None,
    numeric_scale: int | None,
) -> str:
    upper_type = data_type.upper()

    if upper_type in {"VARCHAR", "NVARCHAR", "CHAR", "NCHAR", "VARBINARY", "BINARY"}:
        if character_maximum_length == -1:
            return f"{upper_type}(MAX)"
        return f"{upper_type}({character_maximum_length})"

    if upper_type in {"DECIMAL", "NUMERIC"}:
        return f"{upper_type}({numeric_precision},{numeric_scale})"

    if upper_type in {"DATETIME2", "DATETIMEOFFSET", "TIME"} and numeric_scale is not None:
        return f"{upper_type}({numeric_scale})"

    return upper_type
