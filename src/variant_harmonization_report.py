import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from src.config_loader import load_pipeline_config
from src.db_connection import get_connection
from src.project_paths import LOGS_DIR


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate empirical schema harmonization reports for raw schema variants."
    )
    parser.add_argument(
        "--config",
        required=False,
        help="Pipeline config path. If omitted, PIPELINE_CONFIG_PATH/config default is used.",
    )
    parser.add_argument(
        "--entity",
        action="append",
        help="Logical entity to include. Repeat for multiple entities. Default: all variant entities.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(LOGS_DIR / "harmonization"),
        help="Folder where report files will be written.",
    )
    args = parser.parse_args()

    config = load_pipeline_config() if not args.config else _load_config(Path(args.config))
    result = write_harmonization_reports(
        config=config,
        output_dir=Path(args.output_dir),
        entities=set(args.entity or []),
    )
    print(json.dumps(result, indent=2))


def write_harmonization_reports(
    config: dict,
    output_dir: Path,
    entities: set[str] | None = None,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    requested_entities = {entity.upper() for entity in entities or set()}
    variants_by_entity = _variants_by_entity(config)
    if requested_entities:
        variants_by_entity = {
            entity: variants
            for entity, variants in variants_by_entity.items()
            if entity.upper() in requested_entities
        }

    profile_rows = _fetch_latest_profile_rows(
        [variant["table_name"] for variants in variants_by_entity.values() for variant in variants]
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    written_files = []
    entity_reports = {}
    for entity_name, variants in sorted(variants_by_entity.items()):
        entity_report = build_entity_report(entity_name, variants, profile_rows)
        entity_reports[entity_name] = entity_report

        csv_path = output_dir / f"{entity_name}_harmonization_columns_{timestamp}.csv"
        md_path = output_dir / f"{entity_name}_harmonization_summary_{timestamp}.md"
        _write_entity_column_csv(csv_path, entity_report)
        _write_entity_markdown(md_path, entity_report)
        written_files.extend([str(csv_path), str(md_path)])

    json_path = output_dir / f"variant_harmonization_report_{timestamp}.json"
    json_path.write_text(json.dumps(entity_reports, indent=2, default=str), encoding="utf-8")
    written_files.append(str(json_path))

    return {
        "entity_count": len(entity_reports),
        "written_files": written_files,
    }


def build_entity_report(
    entity_name: str,
    variants: list[dict[str, str]],
    profile_rows: dict[str, list[dict[str, object]]],
) -> dict[str, object]:
    variant_reports = []
    columns_by_variant = {}
    all_columns = []
    all_column_set = set()

    for variant in sorted(variants, key=lambda item: item["pipeline_name"]):
        rows = sorted(
            profile_rows.get(variant["table_name"], []),
            key=lambda row: int(row["ordinal_position"]),
        )
        variant_column_names = [str(row["column_name"]) for row in rows]
        columns_by_variant[variant["pipeline_name"]] = set(variant_column_names)
        for column_name in variant_column_names:
            if column_name not in all_column_set:
                all_column_set.add(column_name)
                all_columns.append(column_name)
        variant_reports.append(
            {
                "pipeline_name": variant["pipeline_name"],
                "table_name": variant["table_name"],
                "row_count": _table_row_count(variant["table_name"]),
                "column_count": len(rows),
                "columns": rows,
            }
        )

    common_columns = [
        column_name
        for column_name in all_columns
        if all(column_name in columns for columns in columns_by_variant.values())
    ]
    variant_only_columns = {
        variant_report["pipeline_name"]: [
            column_name
            for column_name in columns_by_variant[variant_report["pipeline_name"]]
            if column_name not in common_columns
        ]
        for variant_report in variant_reports
    }
    ordinal_name_conflicts = _find_ordinal_name_conflicts(variant_reports)

    return {
        "entity_name": entity_name,
        "variant_count": len(variant_reports),
        "total_row_count": sum(int(variant["row_count"]) for variant in variant_reports),
        "common_column_count": len(common_columns),
        "union_column_count": len(all_columns),
        "common_columns": common_columns,
        "variant_only_columns": variant_only_columns,
        "ordinal_name_conflicts": ordinal_name_conflicts,
        "variants": variant_reports,
        "recommendation": _recommend_next_step(
            variant_reports=variant_reports,
            common_columns=common_columns,
            ordinal_name_conflicts=ordinal_name_conflicts,
        ),
    }


def _variants_by_entity(config: dict) -> dict[str, list[dict[str, str]]]:
    variants_by_entity: dict[str, list[dict[str, str]]] = defaultdict(list)
    for pipeline_name, pipeline in config.items():
        entity_name = pipeline.get("source_entity")
        if not entity_name:
            continue
        variants_by_entity[str(entity_name)].append(
            {
                "pipeline_name": pipeline_name,
                "table_name": pipeline["source_table"],
            }
        )
    return variants_by_entity


def _fetch_latest_profile_rows(table_names: list[str]) -> dict[str, list[dict[str, object]]]:
    if not table_names:
        return {}

    placeholders = ", ".join("?" for _ in table_names)
    sql = f"""
    WITH ranked AS (
        SELECT
            TableName,
            PipelineName,
            ColumnName,
            OrdinalPosition,
            MinimumNonNullValue,
            MaximumNonNullValue,
            MaxStringLength,
            BlankRowCount,
            NonNullRowCount,
            RecommendedSqlType,
            DefinedSqlType,
            CapturedAt,
            ROW_NUMBER() OVER (
                PARTITION BY TableName, ColumnName
                ORDER BY CapturedAt DESC, AutoId DESC
            ) AS rn
        FROM audit.ColumnProfileLog
        WHERE CountStage = 'PRELOAD_CSV'
          AND TableName IN ({placeholders})
    )
    SELECT
        TableName,
        PipelineName,
        ColumnName,
        OrdinalPosition,
        MinimumNonNullValue,
        MaximumNonNullValue,
        MaxStringLength,
        BlankRowCount,
        NonNullRowCount,
        RecommendedSqlType,
        DefinedSqlType,
        CapturedAt
    FROM ranked
    WHERE rn = 1
    ORDER BY TableName, OrdinalPosition;
    """
    grouped_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, table_names)
        columns = [column[0] for column in cursor.description]
        for row in cursor.fetchall():
            record = {
                _to_snake_case(column_name): value
                for column_name, value in zip(columns, row)
            }
            grouped_rows[str(record["table_name"])].append(record)
    finally:
        conn.close()
    return grouped_rows


def _table_row_count(table_name: str) -> int:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COALESCE(SUM(row_count), 0)
            FROM sys.dm_db_partition_stats
            WHERE object_id = OBJECT_ID(?) AND index_id IN (0, 1)
            """,
            table_name,
        )
        return int(cursor.fetchone()[0] or 0)
    finally:
        conn.close()


def _find_ordinal_name_conflicts(variant_reports: list[dict[str, object]]) -> list[dict[str, object]]:
    names_by_ordinal: dict[int, set[str]] = defaultdict(set)
    for variant_report in variant_reports:
        for column in variant_report["columns"]:
            names_by_ordinal[int(column["ordinal_position"])].add(str(column["column_name"]))

    conflicts = []
    for ordinal_position, names in sorted(names_by_ordinal.items()):
        if len(names) <= 1:
            continue
        conflicts.append(
            {
                "ordinal_position": ordinal_position,
                "column_names": sorted(names),
            }
        )
    return conflicts


def _recommend_next_step(
    variant_reports: list[dict[str, object]],
    common_columns: list[str],
    ordinal_name_conflicts: list[dict[str, object]],
) -> str:
    union_column_count = len(
        {
            str(column["column_name"])
            for variant_report in variant_reports
            for column in variant_report["columns"]
        }
    )
    if union_column_count == len(common_columns) and not ordinal_name_conflicts:
        return "auto_union_candidate"
    if ordinal_name_conflicts:
        return "review_ordinal_conflicts_before_union"
    return "review_variant_only_columns_before_union"


def _write_entity_column_csv(path: Path, entity_report: dict[str, object]) -> None:
    fieldnames = [
        "entity_name",
        "pipeline_name",
        "table_name",
        "row_count",
        "ordinal_position",
        "column_name",
        "presence",
        "non_null_row_count",
        "blank_row_count",
        "fill_rate",
        "max_string_length",
        "minimum_non_null_value",
        "maximum_non_null_value",
        "recommended_sql_type",
        "defined_sql_type",
    ]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        common_columns = set(entity_report["common_columns"])
        for variant in entity_report["variants"]:
            row_count = int(variant["row_count"])
            for column in variant["columns"]:
                non_null_count = int(column["non_null_row_count"] or 0)
                blank_count = int(column["blank_row_count"] or 0)
                denominator = non_null_count + blank_count
                writer.writerow(
                    {
                        "entity_name": entity_report["entity_name"],
                        "pipeline_name": variant["pipeline_name"],
                        "table_name": variant["table_name"],
                        "row_count": row_count,
                        "ordinal_position": column["ordinal_position"],
                        "column_name": column["column_name"],
                        "presence": "common" if column["column_name"] in common_columns else "variant_only",
                        "non_null_row_count": non_null_count,
                        "blank_row_count": blank_count,
                        "fill_rate": round(non_null_count / denominator, 6) if denominator else None,
                        "max_string_length": column["max_string_length"],
                        "minimum_non_null_value": column["minimum_non_null_value"],
                        "maximum_non_null_value": column["maximum_non_null_value"],
                        "recommended_sql_type": column["recommended_sql_type"],
                        "defined_sql_type": column["defined_sql_type"],
                    }
                )


def _write_entity_markdown(path: Path, entity_report: dict[str, object]) -> None:
    lines = [
        f"# {entity_report['entity_name']} Harmonization Summary",
        "",
        f"- Variants: {entity_report['variant_count']}",
        f"- Total raw rows: {entity_report['total_row_count']:,}",
        f"- Common columns: {entity_report['common_column_count']}",
        f"- Union columns: {entity_report['union_column_count']}",
        f"- Recommendation: {entity_report['recommendation']}",
        "",
        "## Variants",
        "",
        "| Pipeline | Raw table | Rows | Columns |",
        "| --- | --- | ---: | ---: |",
    ]
    for variant in entity_report["variants"]:
        lines.append(
            f"| {variant['pipeline_name']} | {variant['table_name']} | "
            f"{int(variant['row_count']):,} | {variant['column_count']} |"
        )

    lines.extend(["", "## Variant-Only Columns", ""])
    for pipeline_name, columns in entity_report["variant_only_columns"].items():
        sample = ", ".join(sorted(columns)[:20])
        suffix = f" (+{len(columns) - 20} more)" if len(columns) > 20 else ""
        lines.append(f"- {pipeline_name}: {len(columns)} columns")
        if sample:
            lines.append(f"  {sample}{suffix}")

    lines.extend(["", "## Ordinal Name Conflicts", ""])
    if not entity_report["ordinal_name_conflicts"]:
        lines.append("None.")
    else:
        for conflict in entity_report["ordinal_name_conflicts"][:50]:
            lines.append(
                f"- Position {conflict['ordinal_position']}: "
                f"{', '.join(conflict['column_names'])}"
            )
        remaining = len(entity_report["ordinal_name_conflicts"]) - 50
        if remaining > 0:
            lines.append(f"- ... {remaining} additional conflicts omitted from summary.")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


def _to_snake_case(value: str) -> str:
    result = []
    for index, character in enumerate(value):
        if character.isupper() and index > 0:
            result.append("_")
        result.append(character.lower())
    return "".join(result)


if __name__ == "__main__":
    main()
