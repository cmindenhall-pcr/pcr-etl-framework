import argparse
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.audit_logger import insert_load_execution_log
from src.config_loader import load_pipeline_config
from src.csv_utils import sanitize_column_name
from src.date_normalization import sql_date_normalization_expression
from src.db_connection import get_connection
from src.stg_loader import STAGING_IDENTITY_COLUMN

HARMONIZED_METADATA_COLUMNS = [
    ("SourceVariant", "VARCHAR(128)", "NOT NULL"),
    ("SourceTable", "VARCHAR(256)", "NOT NULL"),
    ("LoadDate", "DATETIME", "NOT NULL"),
    ("SFileName", "VARCHAR(255)", "NOT NULL"),
]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Load one harmonized staging table from raw schema-variant tables."
    )
    parser.add_argument("--config", help="Pipeline config path. Defaults to normal config loader.")
    parser.add_argument("--entity", required=True, help="Logical entity name, e.g. BSAK.")
    parser.add_argument("--staging-table", help="Target table. Default: hrm.<ENTITY>.")
    args = parser.parse_args()

    config = _load_config(Path(args.config)) if args.config else load_pipeline_config()
    staging_table = args.staging_table or f"hrm.{args.entity.upper()}"
    result = load_harmonized_staging(
        config=config,
        entity_name=args.entity,
        staging_table=staging_table,
    )
    print(json.dumps(result, indent=2))


def load_harmonized_staging(
    config: dict,
    entity_name: str,
    staging_table: str,
    run_id: str | None = None,
) -> dict[str, object]:
    variants = _entity_variants(config, entity_name)
    if not variants:
        raise ValueError(f"No raw schema variants found for entity {entity_name!r}.")

    profile_rows = _fetch_latest_profile_rows([variant["source_table"] for variant in variants])
    union_columns = _build_union_columns(variants, profile_rows)
    if not union_columns:
        raise ValueError(f"No profiled columns found for entity {entity_name!r}.")
    column_types = _build_harmonized_column_types(union_columns, profile_rows)

    schema_name, object_name = staging_table.split(".", maxsplit=1)
    started_at = datetime.now()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        _ensure_schema_exists(cursor, schema_name)
        create_sql = build_create_harmonized_staging_sql(
            staging_table=staging_table,
            union_columns=union_columns,
            column_types=column_types,
        )
        cursor.execute(create_sql)
        inserted_rows = 0
        for variant in variants:
            source_column_map = _build_source_column_map(
                profile_rows[variant["source_table"]],
                variant["source_table"],
            )
            insert_sql = build_insert_variant_sql(
                staging_table=staging_table,
                variant=variant,
                union_columns=union_columns,
                column_types=column_types,
                source_column_map=source_column_map,
            )
            cursor.execute(insert_sql)
            inserted_rows += cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0

        conn.commit()
        row_count = _get_table_row_count(cursor, staging_table)
        finished_at = datetime.now()
        insert_load_execution_log(
            run_id=run_id,
            pipeline_name=entity_name.upper(),
            table_name=staging_table,
            source_file=f"HARMONIZED::{len(variants)} raw variants",
            load_method="RAW_VARIANTS_TO_STG",
            load_status="SUCCESS",
            chunk_row_count=row_count,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            detail_message=f"Rebuilt {staging_table} from {len(variants)} raw variants.",
        )
        return {
            "entity_name": entity_name.upper(),
            "staging_table": staging_table,
            "variant_count": len(variants),
            "column_count_excluding_autoid": len(union_columns) + len(HARMONIZED_METADATA_COLUMNS),
            "row_count": row_count,
        }
    except Exception as exc:
        conn.rollback()
        finished_at = datetime.now()
        insert_load_execution_log(
            run_id=run_id,
            pipeline_name=entity_name.upper(),
            table_name=staging_table,
            source_file=f"HARMONIZED::{len(variants)} raw variants",
            load_method="RAW_VARIANTS_TO_STG",
            load_status="FAILED",
            chunk_row_count=0,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            detail_message=str(exc),
        )
        raise
    finally:
        conn.close()


def build_create_harmonized_staging_sql(
    staging_table: str,
    union_columns: list[str],
    column_types: dict[str, str] | None = None,
) -> str:
    schema_name, object_name = staging_table.split(".", maxsplit=1)
    column_types = column_types or {}
    data_definitions = [
        f"    [{column_name}] {column_types.get(column_name, 'VARCHAR(4000)')} NULL"
        for column_name in union_columns
    ]
    metadata_definitions = [
        f"    [{column_name}] {sql_type} {nullability}"
        for column_name, sql_type, nullability in HARMONIZED_METADATA_COLUMNS
    ]
    column_definitions = ",\n".join(
        [
            *data_definitions,
            *metadata_definitions,
            f"    [{STAGING_IDENTITY_COLUMN}] INT IDENTITY(1,1) NOT NULL",
        ]
    )
    return f"""
IF OBJECT_ID('{schema_name}.{object_name}', 'U') IS NOT NULL
    DROP TABLE {schema_name}.{object_name};

CREATE TABLE {schema_name}.{object_name} (
{column_definitions}
);
"""


def build_insert_variant_sql(
    staging_table: str,
    variant: dict[str, str],
    union_columns: list[str],
    column_types: dict[str, str] | None,
    source_column_map: dict[str, str],
) -> str:
    column_types = column_types or {}
    insert_columns = [
        *union_columns,
        *(column_name for column_name, _, _ in HARMONIZED_METADATA_COLUMNS),
    ]
    select_expressions = []
    for column_name in union_columns:
        source_column_name = source_column_map.get(column_name)
        if source_column_name is not None:
            normalized_source = f"NULLIF(LTRIM(RTRIM([{source_column_name}])), '')"
            if _is_date_like_type(column_types.get(column_name, "")):
                select_expressions.append(
                    f"    {sql_date_normalization_expression(normalized_source, column_types[column_name])} AS [{column_name}]"
                )
                continue
            select_expressions.append(
                f"    CAST({normalized_source} AS VARCHAR(4000)) AS [{column_name}]"
            )
        else:
            select_expressions.append(
                f"    CAST(NULL AS {column_types.get(column_name, 'VARCHAR(4000)')}) AS [{column_name}]"
            )

    select_expressions.extend(
        [
            f"    CAST('{variant['pipeline_name']}' AS VARCHAR(128)) AS [SourceVariant]",
            f"    CAST('{variant['source_table']}' AS VARCHAR(256)) AS [SourceTable]",
            "    [LoadDate] AS [LoadDate]",
            "    [SFileName] AS [SFileName]",
        ]
    )

    return f"""
INSERT INTO {staging_table} ({", ".join(f"[{column}]" for column in insert_columns)})
SELECT
{",\n".join(select_expressions)}
FROM {variant['source_table']};
"""


def _build_harmonized_column_types(
    union_columns: list[str],
    profile_rows: dict[str, list[dict[str, object]]],
) -> dict[str, str]:
    rows_by_harmonized_column: dict[str, list[dict[str, object]]] = defaultdict(list)
    for rows in profile_rows.values():
        for row in rows:
            column_name = _normalize_harmonized_column_name(str(row["column_name"]))
            rows_by_harmonized_column[column_name].append(row)

    column_types = {}
    for column_name in union_columns:
        if _is_oracle_date_column(column_name, rows_by_harmonized_column[column_name]):
            column_types[column_name] = "DATE"
        else:
            column_types[column_name] = "VARCHAR(4000)"
    return column_types


def _is_oracle_date_column(column_name: str, profile_rows: list[dict[str, object]]) -> bool:
    upper_name = column_name.upper()
    if "DATE" not in upper_name:
        return False

    values = [
        str(row.get("minimum_non_null_value") or "")
        for row in profile_rows
        if row.get("minimum_non_null_value")
    ]
    values.extend(
        str(row.get("maximum_non_null_value") or "")
        for row in profile_rows
        if row.get("maximum_non_null_value")
    )
    if not values:
        return True

    return any(_looks_like_oracle_datetime(value) for value in values)


def _looks_like_oracle_datetime(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"\d{4}-\d{2}-\d{2}(?:[T ][0-2]\d:[0-5]\d:[0-5]\d(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})?)?",
            value.strip(),
        )
    )


def _is_date_like_type(sql_type: str) -> bool:
    return sql_type.upper().startswith(("DATE", "DATETIME", "SMALLDATETIME"))


def _entity_variants(config: dict, entity_name: str) -> list[dict[str, str]]:
    normalized_entity_name = entity_name.upper()
    variants = []
    for pipeline_name, pipeline in config.items():
        if str(pipeline.get("source_entity", "")).upper() != normalized_entity_name:
            continue
        variants.append(
            {
                "pipeline_name": pipeline_name,
                "source_table": pipeline["source_table"],
            }
        )
    return sorted(variants, key=lambda variant: variant["pipeline_name"])


def _fetch_latest_profile_rows(table_names: list[str]) -> dict[str, list[dict[str, object]]]:
    placeholders = ", ".join("?" for _ in table_names)
    sql = f"""
    WITH latest_runs AS (
        SELECT TableName, MAX(AutoId) AS MaxAutoId
        FROM audit.ColumnProfileLog
        WHERE CountStage = 'PRELOAD_CSV'
          AND TableName IN ({placeholders})
        GROUP BY TableName, RunID
    ),
    latest_run_per_table AS (
        SELECT TableName, MAX(MaxAutoId) AS MaxAutoId
        FROM latest_runs
        GROUP BY TableName
    ),
    latest_run_id AS (
        SELECT c.TableName, c.RunID
        FROM audit.ColumnProfileLog c
        INNER JOIN latest_run_per_table l
            ON c.TableName = l.TableName
           AND c.AutoId = l.MaxAutoId
    )
    SELECT
        c.TableName,
        c.ColumnName,
        c.OrdinalPosition,
        c.MinimumNonNullValue,
        c.MaximumNonNullValue,
        c.NonNullRowCount
    FROM audit.ColumnProfileLog c
    INNER JOIN latest_run_id lr
        ON c.TableName = lr.TableName
       AND c.RunID = lr.RunID
    WHERE c.CountStage = 'PRELOAD_CSV'
    ORDER BY c.TableName, c.OrdinalPosition, c.AutoId;
    """
    grouped_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, table_names)
        for row in cursor.fetchall():
            grouped_rows[str(row[0])].append(
                {
                    "table_name": row[0],
                    "column_name": row[1],
                    "ordinal_position": row[2],
                    "minimum_non_null_value": row[3],
                    "maximum_non_null_value": row[4],
                    "non_null_row_count": int(row[5] or 0),
                }
            )
    finally:
        conn.close()
    return grouped_rows


def _build_union_columns(
    variants: list[dict[str, str]],
    profile_rows: dict[str, list[dict[str, object]]],
) -> list[str]:
    union_columns = []
    seen_columns = set()
    generated_blank_non_null_counts = _generated_blank_non_null_counts(profile_rows)
    for variant in variants:
        for row in sorted(
            profile_rows[variant["source_table"]],
            key=lambda item: int(item["ordinal_position"]),
        ):
            column_name = _normalize_harmonized_column_name(str(row["column_name"]))
            if column_name in seen_columns:
                continue
            if (
                _is_generated_blank_header_column(column_name)
                and generated_blank_non_null_counts[column_name] == 0
            ):
                continue
            if column_name.lower() == STAGING_IDENTITY_COLUMN.lower():
                raise ValueError(f"Source column {column_name!r} conflicts with reserved AutoId.")
            seen_columns.add(column_name)
            union_columns.append(column_name)
    return union_columns


def _generated_blank_non_null_counts(
    profile_rows: dict[str, list[dict[str, object]]],
) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for rows in profile_rows.values():
        for row in rows:
            column_name = _normalize_harmonized_column_name(str(row["column_name"]))
            if _is_generated_blank_header_column(column_name):
                counts[column_name] += int(row["non_null_row_count"])
    return counts


def _build_source_column_map(
    profile_rows: list[dict[str, object]],
    source_table: str,
) -> dict[str, str]:
    source_column_map = {}
    for row in profile_rows:
        source_column_name = str(row["column_name"])
        harmonized_column_name = _normalize_harmonized_column_name(source_column_name)
        existing_source_column = source_column_map.get(harmonized_column_name)
        if existing_source_column is not None:
            raise ValueError(
                f"Source table {source_table} has columns {existing_source_column!r} "
                f"and {source_column_name!r} that both normalize to "
                f"{harmonized_column_name!r}."
            )
        source_column_map[harmonized_column_name] = source_column_name
    return source_column_map


def _normalize_harmonized_column_name(column_name: str) -> str:
    if column_name in {name for name, _, _ in HARMONIZED_METADATA_COLUMNS}:
        return column_name
    return sanitize_column_name(column_name)


def _is_generated_blank_header_column(column_name: str) -> bool:
    return bool(re.fullmatch(r"Column_\d{3}", column_name))


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


def _get_table_row_count(cursor, table_name: str) -> int:
    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
