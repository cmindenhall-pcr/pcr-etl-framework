import argparse
import csv
import json
import re
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from src.db_connection import get_connection
from src.project_paths import LOGS_DIR


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a user-facing anomaly review file for harmonized entity variants."
    )
    parser.add_argument(
        "--harmonization-report",
        required=True,
        help="JSON report produced by src.variant_harmonization_report.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(LOGS_DIR / "harmonization_review"),
        help="Folder where review files will be written.",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=5,
        help="Maximum non-null sample values to include per anomaly.",
    )
    args = parser.parse_args()

    result = write_anomaly_review(
        harmonization_report_path=Path(args.harmonization_report),
        output_dir=Path(args.output_dir),
        sample_size=args.sample_size,
    )
    print(json.dumps(result, indent=2))


def write_anomaly_review(
    harmonization_report_path: Path,
    output_dir: Path,
    sample_size: int = 5,
) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    report = json.loads(harmonization_report_path.read_text(encoding="utf-8"))
    anomalies = build_anomaly_rows(report, sample_size=sample_size)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"harmonization_anomaly_review_{timestamp}.csv"
    md_path = output_dir / f"harmonization_anomaly_review_{timestamp}.md"
    _write_csv(csv_path, anomalies)
    _write_markdown(md_path, anomalies)

    return {
        "anomaly_count": len(anomalies),
        "written_files": [str(csv_path), str(md_path)],
    }


def build_anomaly_rows(report: dict[str, object], sample_size: int = 5) -> list[dict[str, object]]:
    hrm_columns_by_entity = _fetch_hrm_columns(list(report.keys()))
    rows = []
    for entity_name, entity_report in sorted(report.items()):
        hrm_columns = hrm_columns_by_entity.get(entity_name, set())
        variant_names = [variant["pipeline_name"] for variant in entity_report["variants"]]
        rows.extend(
            _build_variant_only_rows(
                entity_name=entity_name,
                entity_report=entity_report,
                variant_names=variant_names,
                hrm_columns=hrm_columns,
                sample_size=sample_size,
            )
        )
        rows.extend(
            _build_rename_candidate_rows(
                entity_name=entity_name,
                entity_report=entity_report,
                variant_names=variant_names,
                hrm_columns=hrm_columns,
                sample_size=sample_size,
            )
        )
    return rows


def _build_variant_only_rows(
    entity_name: str,
    entity_report: dict[str, object],
    variant_names: list[str],
    hrm_columns: set[str],
    sample_size: int,
) -> list[dict[str, object]]:
    rows = []
    for column_name in sorted(_variant_only_column_names(entity_report)):
        if column_name not in hrm_columns:
            continue
        present_variants = _present_variants(entity_report, column_name)
        missing_variants = [variant for variant in variant_names if variant not in present_variants]
        non_null_count = _column_non_null_count(entity_report, column_name)
        samples = _fetch_samples(entity_name, column_name, sample_size)
        rows.append(
            {
                "entity_name": entity_name,
                "anomaly_type": (
                    "unnamed_source_column_with_values"
                    if _is_generated_blank_header_column(column_name)
                    else "named_variant_only_column"
                ),
                "hrm_column_name": column_name,
                "candidate_column_name": "",
                "present_in_variants": ";".join(present_variants),
                "missing_from_variants": ";".join(missing_variants),
                "non_null_row_count": non_null_count,
                "sample_values": _format_samples(samples),
                "source_trace": _format_source_trace(entity_report, column_name),
                "recommended_review_action": _recommended_action(column_name),
                "review_decision": "",
                "review_notes": "",
            }
        )
    return rows


def _build_rename_candidate_rows(
    entity_name: str,
    entity_report: dict[str, object],
    variant_names: list[str],
    hrm_columns: set[str],
    sample_size: int,
) -> list[dict[str, object]]:
    named_columns = sorted(
        column
        for column in _variant_only_column_names(entity_report)
        if column in hrm_columns and not _is_generated_blank_header_column(column)
    )
    rows = []
    seen_pairs = set()
    for left_index, left_column in enumerate(named_columns):
        for right_column in named_columns[left_index + 1 :]:
            pair_key = tuple(sorted([left_column, right_column]))
            if pair_key in seen_pairs:
                continue
            if not _looks_like_rename_candidate(left_column, right_column):
                continue
            seen_pairs.add(pair_key)
            left_present = _present_variants(entity_report, left_column)
            right_present = _present_variants(entity_report, right_column)
            if set(left_present) & set(right_present):
                continue
            rows.append(
                {
                    "entity_name": entity_name,
                    "anomaly_type": "possible_rename_pair",
                    "hrm_column_name": left_column,
                    "candidate_column_name": right_column,
                    "present_in_variants": f"{left_column}:{';'.join(left_present)} | {right_column}:{';'.join(right_present)}",
                    "missing_from_variants": "",
                    "non_null_row_count": (
                        f"{left_column}:{_column_non_null_count(entity_report, left_column)} | "
                        f"{right_column}:{_column_non_null_count(entity_report, right_column)}"
                    ),
                    "sample_values": (
                        f"{left_column} => {_format_samples(_fetch_samples(entity_name, left_column, sample_size))} | "
                        f"{right_column} => {_format_samples(_fetch_samples(entity_name, right_column, sample_size))}"
                    ),
                    "source_trace": (
                        f"{left_column}: {_format_source_trace(entity_report, left_column)} | "
                        f"{right_column}: {_format_source_trace(entity_report, right_column)}"
                    ),
                    "recommended_review_action": "Decide whether these columns should coalesce into one stg field.",
                    "review_decision": "",
                    "review_notes": "",
                }
            )
    return rows


def _variant_only_column_names(entity_report: dict[str, object]) -> set[str]:
    return {
        column_name
        for columns in entity_report["variant_only_columns"].values()
        for column_name in columns
    }


def _present_variants(entity_report: dict[str, object], column_name: str) -> list[str]:
    variants = []
    for variant in entity_report["variants"]:
        if any(column["column_name"] == column_name for column in variant["columns"]):
            variants.append(variant["pipeline_name"])
    return variants


def _column_non_null_count(entity_report: dict[str, object], column_name: str) -> int:
    total = 0
    for variant in entity_report["variants"]:
        for column in variant["columns"]:
            if column["column_name"] == column_name:
                total += int(column["non_null_row_count"] or 0)
    return total


def _format_source_trace(entity_report: dict[str, object], column_name: str) -> str:
    parts = []
    for variant in entity_report["variants"]:
        for column in variant["columns"]:
            if column["column_name"] != column_name:
                continue
            parts.append(
                f"{variant['pipeline_name']}|{variant['table_name']}|ordinal={column['ordinal_position']}"
            )
    return "; ".join(parts)


def _fetch_hrm_columns(entity_names: list[str]) -> dict[str, set[str]]:
    if not entity_names:
        return {}
    placeholders = ", ".join("?" for _ in entity_names)
    sql = f"""
    SELECT
        TABLE_NAME,
        COLUMN_NAME
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'hrm'
      AND TABLE_NAME IN ({placeholders})
    """
    columns_by_entity: dict[str, set[str]] = defaultdict(set)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, entity_names)
        for row in cursor.fetchall():
            columns_by_entity[str(row[0])].add(str(row[1]))
    finally:
        conn.close()
    return columns_by_entity


def _fetch_samples(entity_name: str, column_name: str, sample_size: int) -> list[tuple[str, str, str]]:
    sql = f"""
    SELECT TOP ({int(sample_size)})
        [SourceVariant],
        [SFileName],
        CAST([{_escape_bracket_name(column_name)}] AS VARCHAR(4000)) AS [SampleValue]
    FROM hrm.[{_escape_bracket_name(entity_name)}]
    WHERE [{_escape_bracket_name(column_name)}] IS NOT NULL
      AND LTRIM(RTRIM(CAST([{_escape_bracket_name(column_name)}] AS VARCHAR(4000)))) <> ''
    ORDER BY [SourceVariant], [SFileName], [AutoId]
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql)
        return [(str(row[0]), str(row[1]), str(row[2])) for row in cursor.fetchall()]
    finally:
        conn.close()


def _format_samples(samples: list[tuple[str, str, str]]) -> str:
    return " || ".join(
        f"{variant}|{Path(source_file).name}|{value}"
        for variant, source_file, value in samples
    )


def _recommended_action(column_name: str) -> str:
    if _is_generated_blank_header_column(column_name):
        return "Identify source meaning from sample values; rename or approve drop before stg."
    return "Decide whether to keep as independent field or map/coalesce with a related field in stg."


def _looks_like_rename_candidate(left_column: str, right_column: str) -> bool:
    left = _normalize_for_similarity(left_column)
    right = _normalize_for_similarity(right_column)
    if not left or not right:
        return False
    if left in right or right in left:
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.72


def _normalize_for_similarity(column_name: str) -> str:
    normalized = column_name.lower()
    replacements = {
        "quantity": "qty",
        "discount": "disc",
        "reference": "ref",
        "created_by": "created",
        "amount_in_lc": "amount_lc",
        "exempt": "expt",
        "del_note": "dn",
        "delivery_note": "dn",
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _is_generated_blank_header_column(column_name: str) -> bool:
    return bool(re.fullmatch(r"Column_\d+", column_name))


def _escape_bracket_name(name: str) -> str:
    return name.replace("]", "]]")


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "entity_name",
        "anomaly_type",
        "hrm_column_name",
        "candidate_column_name",
        "present_in_variants",
        "missing_from_variants",
        "non_null_row_count",
        "sample_values",
        "source_trace",
        "recommended_review_action",
        "review_decision",
        "review_notes",
    ]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, rows: list[dict[str, object]]) -> None:
    grouped_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped_rows[str(row["entity_name"])].append(row)

    lines = ["# Harmonization Anomaly Review", ""]
    for entity_name, entity_rows in sorted(grouped_rows.items()):
        lines.extend([f"## {entity_name}", ""])
        for row in entity_rows:
            candidate = (
                f" <-> {row['candidate_column_name']}"
                if row["candidate_column_name"]
                else ""
            )
            lines.append(
                f"- {row['anomaly_type']}: {row['hrm_column_name']}{candidate}"
            )
            lines.append(f"  - Present: {row['present_in_variants']}")
            if row["missing_from_variants"]:
                lines.append(f"  - Missing: {row['missing_from_variants']}")
            lines.append(f"  - Non-null: {row['non_null_row_count']}")
            if row["sample_values"]:
                lines.append(f"  - Samples: {row['sample_values']}")
            lines.append(f"  - Review: {row['recommended_review_action']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
