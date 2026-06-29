from datetime import datetime

from src.audit_logger import insert_load_execution_log
from src.db_connection import get_connection
from src.logger import get_logger

logger = get_logger()
ZEN_IDENTITY_COLUMN = "AutoId"


def append_staging_to_zen(
    staging_table: str,
    zen_table: str,
    pipeline_name: str | None = None,
    run_id: str | None = None,
) -> int:
    prefix = f"[run_id={run_id}] " if run_id else ""
    schema_rows = _get_table_schema(staging_table)
    schema_rows = _exclude_layer_identity_columns(schema_rows)

    if not schema_rows:
        raise RuntimeError(
            f"Zen load failed for {zen_table}: no staging columns found for {staging_table}."
        )

    column_definitions = ",\n".join(
        f"    [{row['column_name']}] {row['sql_type']} {row['nullability']}"
        for row in schema_rows
    )
    column_list = ", ".join(f"[{row['column_name']}]" for row in schema_rows)
    schema_name, object_name = zen_table.split(".", maxsplit=1)

    conn = get_connection()
    started_at = datetime.now()
    try:
        cursor = conn.cursor()
        _ensure_schema_exists(cursor, schema_name)
        logger.info(f"{prefix}Appending {staging_table} into {zen_table}")

        target_before = _get_table_row_count(cursor, zen_table, allow_missing=True)
        if _table_exists(cursor, schema_name, object_name):
            _ensure_zen_autoid_identity(cursor, schema_name, object_name, zen_table)

        cursor.execute(
            f"""
            IF OBJECT_ID('{schema_name}.{object_name}', 'U') IS NULL
            BEGIN
                CREATE TABLE {schema_name}.{object_name} (
{column_definitions},
                    [{ZEN_IDENTITY_COLUMN}] INT IDENTITY(1,1) NOT NULL PRIMARY KEY
                );
            END

            INSERT INTO {schema_name}.{object_name} ({column_list})
            SELECT {column_list}
            FROM {staging_table};
            """
        )
        conn.commit()

        finished_at = datetime.now()
        target_after = _get_table_row_count(cursor, zen_table)
        inserted_row_count = target_after - target_before

        insert_load_execution_log(
            run_id=run_id,
            pipeline_name=pipeline_name,
            table_name=zen_table,
            source_file=staging_table,
            load_method="STG_TO_ZEN",
            load_status="SUCCESS",
            chunk_row_count=inserted_row_count,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            detail_message=f"Appended {inserted_row_count} rows from {staging_table} into {zen_table}.",
        )
        logger.info(f"{prefix}Appended {inserted_row_count} rows into {zen_table}")
        return inserted_row_count
    except Exception as e:
        conn.rollback()
        finished_at = datetime.now()
        insert_load_execution_log(
            run_id=run_id,
            pipeline_name=pipeline_name,
            table_name=zen_table,
            source_file=staging_table,
            load_method="STG_TO_ZEN",
            load_status="FAILED",
            chunk_row_count=0,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            detail_message=str(e),
        )
        logger.exception(f"{prefix}Failed appending {staging_table} into {zen_table}")
        raise RuntimeError(
            f"Zen load failed for {zen_table} from {staging_table}: {e}"
        ) from e
    finally:
        conn.close()


def _get_table_schema(table_name: str) -> list[dict[str, str]]:
    schema_name, object_name = table_name.split(".", maxsplit=1)
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
                    "sql_type": _build_sql_type(
                        data_type=row[1],
                        character_maximum_length=row[2],
                        numeric_precision=row[3],
                        numeric_scale=row[4],
                    ),
                    "nullability": "NULL" if row[5] == "YES" else "NOT NULL",
                }
            )
        return rows
    finally:
        conn.close()


def _exclude_layer_identity_columns(
    schema_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    return [
        row
        for row in schema_rows
        if row["column_name"].lower() != ZEN_IDENTITY_COLUMN.lower()
    ]


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


def _get_table_row_count(cursor, table_name: str, allow_missing: bool = False) -> int:
    schema_name, object_name = table_name.split(".", maxsplit=1)
    if allow_missing and not _table_exists(cursor, schema_name, object_name):
        return 0
    cursor.execute(f"SELECT COUNT(*) FROM {schema_name}.{object_name}")
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _table_exists(cursor, schema_name: str, object_name: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = ?
          AND TABLE_NAME = ?
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


def _ensure_zen_autoid_identity(
    cursor,
    schema_name: str,
    object_name: str,
    zen_table: str,
) -> None:
    cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ?
          AND TABLE_NAME = ?
        ORDER BY ORDINAL_POSITION DESC
        """,
        schema_name,
        object_name,
    )
    row = cursor.fetchone()
    rightmost_column = str(row[0]) if row and row[0] is not None else ""
    if rightmost_column != ZEN_IDENTITY_COLUMN:
        raise RuntimeError(
            f"Zen load failed for {zen_table}: existing [{ZEN_IDENTITY_COLUMN}] column is not the rightmost column. "
            "Drop and recreate the zen table before reloading."
        )

    cursor.execute(
        """
        SELECT COLUMNPROPERTY(
            OBJECT_ID(QUOTENAME(?) + '.' + QUOTENAME(?)),
            ?,
            'IsIdentity'
        )
        """,
        schema_name,
        object_name,
        ZEN_IDENTITY_COLUMN,
    )
    row = cursor.fetchone()
    is_identity = int(row[0]) if row and row[0] is not None else 0
    if is_identity != 1:
        raise RuntimeError(
            f"Zen load failed for {zen_table}: existing [{ZEN_IDENTITY_COLUMN}] column is missing or not IDENTITY. "
            "Drop and recreate the zen table before reloading."
        )
