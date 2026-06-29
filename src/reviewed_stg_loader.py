import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.audit_logger import insert_load_execution_log
from src.date_normalization import sql_date_normalization_expression
from src.db_connection import get_connection
from src.identifier_normalization import (
    is_identifier_like_column,
    sql_identifier_normalization_expression,
)
from src.numeric_normalization import sql_numeric_normalization_expression
from src.stg_loader import STAGING_IDENTITY_COLUMN

METADATA_COLUMNS = {"SourceVariant", "SourceTable", "LoadDate", "SFileName"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build reviewed stg tables from hrm tables using anomaly review decisions."
    )
    parser.add_argument(
        "--review-csv",
        required=True,
        help="harmonization_anomaly_review CSV with accepted decisions.",
    )
    parser.add_argument(
        "--entity",
        action="append",
        help="Entity to load. Repeatable. Default: all entities in the review CSV/hrm schema.",
    )
    parser.add_argument(
        "--drop-hrm",
        action="store_true",
        help="Drop hrm tables after stg tables are built successfully.",
    )
    args = parser.parse_args()

    result = load_reviewed_staging_tables(
        review_csv_path=Path(args.review_csv),
        entities=set(args.entity or []),
        drop_hrm=args.drop_hrm,
    )
    print(json.dumps(result, indent=2))


def load_reviewed_staging_tables(
    review_csv_path: Path,
    entities: set[str] | None = None,
    drop_hrm: bool = False,
) -> dict[str, object]:
    review = _load_review(review_csv_path)
    requested_entities = {entity.upper() for entity in entities or set()}
    hrm_entities = _get_hrm_entities()
    if requested_entities:
        hrm_entities = [entity for entity in hrm_entities if entity.upper() in requested_entities]

    results = []
    built_entities = []
    for entity_name in hrm_entities:
        entity_review = review.get(entity_name, _empty_entity_review())
        result = load_reviewed_staging_table(
            entity_name=entity_name,
            entity_review=entity_review,
        )
        results.append(result)
        built_entities.append(entity_name)

    if drop_hrm and built_entities:
        _drop_hrm_tables(built_entities)

    return {
        "table_count": len(results),
        "drop_hrm": drop_hrm,
        "tables": results,
    }


def load_reviewed_staging_table(
    entity_name: str,
    entity_review: dict[str, object],
    run_id: str | None = None,
) -> dict[str, object]:
    hrm_table = f"hrm.{entity_name}"
    stg_table = f"stg.{entity_name}"
    hrm_columns = _get_table_columns(hrm_table)
    if not hrm_columns:
        raise ValueError(f"No columns found for {hrm_table}.")

    type_map = _fetch_entity_profile_types(entity_name)
    hrm_type_map = _get_table_column_type_map(hrm_table)
    output_columns = build_reviewed_output_columns(
        hrm_columns=hrm_columns,
        drop_columns=set(entity_review["drop_columns"]),
        rename_pairs=dict(entity_review["rename_pairs"]),
    )
    column_specs = build_column_specs(
        output_columns=output_columns,
        rename_pairs=dict(entity_review["rename_pairs"]),
        type_map=type_map,
        hrm_type_map=hrm_type_map,
    )
    create_sql = build_create_stg_sql(stg_table, column_specs)
    insert_sql = build_insert_stg_sql(
        stg_table=stg_table,
        hrm_table=hrm_table,
        column_specs=column_specs,
        rename_pairs=dict(entity_review["rename_pairs"]),
    )

    started_at = datetime.now()
    conn = get_connection()
    try:
        cursor = conn.cursor()
        _ensure_schema_exists(cursor, "stg")
        cursor.execute(create_sql)
        cursor.execute(insert_sql)
        conn.commit()
        row_count = _get_table_row_count(cursor, stg_table)
        finished_at = datetime.now()
        insert_load_execution_log(
            run_id=run_id,
            pipeline_name=entity_name,
            table_name=stg_table,
            source_file=hrm_table,
            load_method="HRM_REVIEW_TO_STG",
            load_status="SUCCESS",
            chunk_row_count=row_count,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            detail_message=(
                f"Built {stg_table} from {hrm_table}; "
                f"dropped {len(entity_review['drop_columns'])} columns; "
                f"coalesced {len(entity_review['rename_pairs'])} rename pairs."
            ),
        )
        return {
            "entity_name": entity_name,
            "source_table": hrm_table,
            "staging_table": stg_table,
            "row_count": row_count,
            "column_count": len(column_specs) + 1,
            "dropped_columns": sorted(entity_review["drop_columns"]),
            "coalesced_pairs": dict(entity_review["rename_pairs"]),
        }
    except Exception as exc:
        conn.rollback()
        finished_at = datetime.now()
        insert_load_execution_log(
            run_id=run_id,
            pipeline_name=entity_name,
            table_name=stg_table,
            source_file=hrm_table,
            load_method="HRM_REVIEW_TO_STG",
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


def build_reviewed_output_columns(
    hrm_columns: list[str],
    drop_columns: set[str],
    rename_pairs: dict[str, str],
) -> list[str]:
    output_columns = []
    seen = set()
    source_rename_columns = set(rename_pairs)
    for column_name in hrm_columns:
        if column_name == STAGING_IDENTITY_COLUMN:
            continue
        if column_name in drop_columns:
            continue
        if column_name in source_rename_columns:
            continue
        if column_name in seen:
            continue
        seen.add(column_name)
        output_columns.append(column_name)
    return output_columns


def build_column_specs(
    output_columns: list[str],
    rename_pairs: dict[str, str],
    type_map: dict[str, list[str]],
    hrm_type_map: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    specs = []
    hrm_type_map = hrm_type_map or {}
    sources_by_target: dict[str, list[str]] = defaultdict(list)
    for source_column, target_column in rename_pairs.items():
        sources_by_target[target_column].append(source_column)

    for column_name in output_columns:
        if column_name == "LoadDate":
            sql_type = "DATETIME"
        elif column_name in METADATA_COLUMNS:
            sql_type = "VARCHAR(4000)"
        elif _is_date_like_type(hrm_type_map.get(column_name, "")):
            sql_type = hrm_type_map[column_name]
        else:
            source_columns = [column_name, *sources_by_target.get(column_name, [])]
            sql_type = _choose_sql_type(
                [
                    sql_type
                    for source_column in source_columns
                    for sql_type in type_map.get(source_column, [])
                ]
            )
        specs.append(
            {
                "column_name": column_name,
                "sql_type": sql_type,
                "nullability": _stg_nullability(column_name, sql_type),
            }
        )
    return specs


def build_create_stg_sql(stg_table: str, column_specs: list[dict[str, str]]) -> str:
    schema_name, object_name = stg_table.split(".", maxsplit=1)
    definitions = []
    for spec in column_specs:
        definitions.append(
            f"    [{spec['column_name']}] {spec['sql_type']} {spec['nullability']}"
        )
    definitions.append(f"    [{STAGING_IDENTITY_COLUMN}] INT IDENTITY(1,1) NOT NULL")
    definition_sql = ",\n".join(definitions)
    return f"""
IF OBJECT_ID('{schema_name}.{object_name}', 'U') IS NOT NULL
    DROP TABLE {schema_name}.{object_name};

CREATE TABLE {schema_name}.{object_name} (
{definition_sql}
);
"""


def build_insert_stg_sql(
    stg_table: str,
    hrm_table: str,
    column_specs: list[dict[str, str]],
    rename_pairs: dict[str, str],
) -> str:
    source_by_target: dict[str, list[str]] = defaultdict(list)
    for source_column, target_column in rename_pairs.items():
        source_by_target[target_column].append(source_column)

    select_expressions = []
    for spec in column_specs:
        column_name = spec["column_name"]
        coalesce_sources = [column_name, *source_by_target.get(column_name, [])]
        if len(coalesce_sources) > 1 and column_name not in METADATA_COLUMNS:
            source_expression = "COALESCE(" + ", ".join(
                f"NULLIF(LTRIM(RTRIM([{source_column}])), '')"
                for source_column in coalesce_sources
            ) + ")"
        else:
            source_expression = f"[{column_name}]"
        expression = _typed_select_expression(
            source_expression=source_expression,
            column_name=column_name,
            sql_type=spec["sql_type"],
        )
        select_expressions.append(f"    {expression}")
    select_sql = ",\n".join(select_expressions)
    output_columns = [spec["column_name"] for spec in column_specs]

    return f"""
INSERT INTO {stg_table} ({", ".join(f"[{column}]" for column in output_columns)})
SELECT
{select_sql}
FROM {hrm_table};
"""


def _load_review(review_csv_path: Path) -> dict[str, dict[str, object]]:
    review_by_entity: dict[str, dict[str, object]] = defaultdict(_empty_entity_review)
    with review_csv_path.open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            entity_name = row["entity_name"].upper()
            if row["anomaly_type"] == "unnamed_source_column_with_values":
                review_by_entity[entity_name]["drop_columns"].add(row["hrm_column_name"])
            elif row["anomaly_type"] == "possible_rename_pair":
                review_by_entity[entity_name]["rename_pairs"][
                    row["hrm_column_name"]
                ] = row["candidate_column_name"]
    return dict(review_by_entity)


def _empty_entity_review() -> dict[str, object]:
    return {
        "drop_columns": set(),
        "rename_pairs": {},
    }


def _get_hrm_entities() -> list[str]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = 'hrm'
              AND TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_NAME
            """
        )
        return [str(row[0]) for row in cursor.fetchall()]
    finally:
        conn.close()


def _get_table_columns(table_name: str) -> list[str]:
    schema_name, object_name = table_name.split(".", maxsplit=1)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COLUMN_NAME
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ?
              AND TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
            """,
            schema_name,
            object_name,
        )
        return [str(row[0]) for row in cursor.fetchall()]
    finally:
        conn.close()


def _get_table_column_type_map(table_name: str) -> dict[str, str]:
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
                NUMERIC_SCALE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ?
              AND TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
            """,
            schema_name,
            object_name,
        )
        return {
            str(row[0]): _format_sql_type(
                data_type=str(row[1]),
                character_maximum_length=row[2],
                numeric_precision=row[3],
                numeric_scale=row[4],
            )
            for row in cursor.fetchall()
        }
    finally:
        conn.close()


def _format_sql_type(
    data_type: str,
    character_maximum_length: int | None,
    numeric_precision: int | None,
    numeric_scale: int | None,
) -> str:
    upper_type = data_type.upper()
    if upper_type in {"VARCHAR", "NVARCHAR", "CHAR", "NCHAR"}:
        if character_maximum_length == -1:
            return f"{upper_type}(MAX)"
        return f"{upper_type}({character_maximum_length})"
    if upper_type in {"DECIMAL", "NUMERIC"}:
        return f"{upper_type}({numeric_precision},{numeric_scale})"
    if upper_type in {"DATETIME2", "DATETIMEOFFSET", "TIME"} and numeric_scale is not None:
        return f"{upper_type}({numeric_scale})"
    return upper_type


def _fetch_entity_profile_types(entity_name: str) -> dict[str, list[str]]:
    table_like = f"raw.{entity_name}_V%"
    sql = """
    WITH ranked AS (
        SELECT
            TableName,
            ColumnName,
            RecommendedSqlType,
            DefinedSqlType,
            ROW_NUMBER() OVER (
                PARTITION BY TableName, ColumnName
                ORDER BY CapturedAt DESC, AutoId DESC
            ) AS rn
        FROM audit.ColumnProfileLog
        WHERE CountStage = 'PRELOAD_CSV'
          AND TableName LIKE ?
    )
    SELECT
        ColumnName,
        RecommendedSqlType,
        DefinedSqlType
    FROM ranked
    WHERE rn = 1
    """
    type_map: dict[str, list[str]] = defaultdict(list)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, table_like)
        for row in cursor.fetchall():
            column_name = str(row[0])
            sql_type = str(row[1] or row[2] or "VARCHAR(4000)").upper()
            if sql_type not in type_map[column_name]:
                type_map[column_name].append(sql_type)
    finally:
        conn.close()
    return dict(type_map)


def _choose_sql_type(sql_types: list[str]) -> str:
    normalized_types = [sql_type.upper() for sql_type in sql_types if sql_type]
    if not normalized_types:
        return "VARCHAR(4000)"
    if any(_is_varchar_type(sql_type) for sql_type in normalized_types):
        non_varchar_types = [
            sql_type for sql_type in normalized_types if not _is_varchar_type(sql_type)
        ]
        if not non_varchar_types:
            return _widest_varchar(normalized_types)
        return _choose_sql_type(non_varchar_types)
    if any(_is_date_like_type(sql_type) for sql_type in normalized_types):
        return "DATE"
    if any(sql_type == "MONEY" for sql_type in normalized_types):
        return "MONEY"
    decimal_types = [sql_type for sql_type in normalized_types if sql_type.startswith(("DECIMAL", "NUMERIC"))]
    if decimal_types:
        return decimal_types[0]
    if any(_is_integer_type(sql_type) for sql_type in normalized_types):
        return "INT"
    return normalized_types[0]


def _widest_varchar(sql_types: list[str]) -> str:
    if any("(MAX)" in sql_type or "(4000)" in sql_type for sql_type in sql_types):
        return "VARCHAR(4000)"
    lengths = []
    for sql_type in sql_types:
        match = re.search(r"\((\d+)\)", sql_type)
        if match:
            lengths.append(int(match.group(1)))
    return f"VARCHAR({max(lengths) if lengths else 4000})"


def _stg_nullability(column_name: str, sql_type: str) -> str:
    if column_name in METADATA_COLUMNS:
        return "NOT NULL"
    if _is_date_like_type(sql_type):
        return "NULL"
    return "NOT NULL"


def _typed_select_expression(source_expression: str, column_name: str, sql_type: str) -> str:
    if column_name in METADATA_COLUMNS:
        return f"{source_expression} AS [{column_name}]"
    normalized = (
        source_expression
        if source_expression.startswith("COALESCE(")
        else f"NULLIF(LTRIM(RTRIM({source_expression})), '')"
    )
    if _is_date_like_type(sql_type):
        date_value = sql_date_normalization_expression(normalized, sql_type)
        return f"{date_value} AS [{column_name}]"
    if _is_numeric_type(sql_type):
        numeric_value = sql_numeric_normalization_expression(normalized)
        return (
            f"ISNULL(TRY_CAST({numeric_value} AS {sql_type}), "
            f"CAST(0 AS {sql_type})) AS [{column_name}]"
        )
    if _is_varchar_type(sql_type):
        if is_identifier_like_column(column_name):
            identifier_value = sql_identifier_normalization_expression(source_expression)
            return f"CAST(ISNULL({identifier_value}, '') AS {sql_type}) AS [{column_name}]"
        return f"CAST(ISNULL({normalized}, '') AS {sql_type}) AS [{column_name}]"
    return f"TRY_CAST({normalized} AS {sql_type}) AS [{column_name}]"


def _is_date_like_type(sql_type: str) -> bool:
    return sql_type.upper().startswith(("DATE", "DATETIME", "SMALLDATETIME"))


def _is_integer_type(sql_type: str) -> bool:
    return sql_type.upper() in {"INT", "BIGINT", "SMALLINT", "TINYINT"}


def _is_numeric_type(sql_type: str) -> bool:
    return sql_type.upper().startswith(
        ("DECIMAL", "NUMERIC", "INT", "BIGINT", "SMALLINT", "TINYINT", "MONEY")
    )


def _is_varchar_type(sql_type: str) -> bool:
    return sql_type.upper().startswith(("VARCHAR", "NVARCHAR", "CHAR", "NCHAR"))


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


def _drop_hrm_tables(entity_names: list[str]) -> None:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        for entity_name in sorted(entity_names):
            cursor.execute(
                f"IF OBJECT_ID('hrm.{entity_name}', 'U') IS NOT NULL DROP TABLE hrm.[{entity_name}];"
            )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
