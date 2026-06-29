import argparse
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from src.generate_multifile_raw_pipeline_config import detect_delimiter, detect_encoding
from src.csv_utils import build_canonical_column_names
from src.project_paths import LOGS_DIR


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze physical schema variants across delimited source files."
    )
    parser.add_argument("folder", help="Root folder containing source files.")
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=[".csv"],
        help="File extensions to include. Default: .csv",
    )
    parser.add_argument(
        "--delimiter",
        default="auto",
        help="Delimited file separator, or 'auto' to sniff per file. Default: auto.",
    )
    parser.add_argument(
        "--strip-prefix-regex",
        default=None,
        help="Optional regex prefix to remove from file stems before grouping entities.",
    )
    parser.add_argument(
        "--strip-suffix-regex",
        default=None,
        help="Optional regex suffix to remove from file stems before grouping entities.",
    )
    parser.add_argument(
        "--client",
        default=None,
        help="Client/database name used in the default output path.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Include files in subfolders. Default: only files directly in the folder.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write JSON report. Default: logs/schema_variants/<client>_schema_variants_<timestamp>.json",
    )
    args = parser.parse_args()

    report = analyze_schema_variants(
        folder=Path(args.folder),
        extensions=args.extensions,
        delimiter=args.delimiter,
        strip_prefix_regex=args.strip_prefix_regex,
        strip_suffix_regex=args.strip_suffix_regex,
        client_name=args.client,
        recursive=args.recursive,
    )

    output_path = Path(args.output) if args.output else _default_output_path(args.client)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(_summarize_report(report, output_path), indent=2))


def analyze_schema_variants(
    folder: Path,
    extensions: list[str] | None = None,
    delimiter: str = "auto",
    strip_prefix_regex: str | None = None,
    strip_suffix_regex: str | None = None,
    client_name: str | None = None,
    recursive: bool = False,
) -> dict[str, object]:
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")

    candidates = folder.rglob("*") if recursive else folder.glob("*")
    files = sorted(
        path
        for path in candidates
        if path.is_file() and path.suffix.lower() in _normalize_extensions(extensions or [".csv"])
    )
    grouped_files: dict[str, list[Path]] = defaultdict(list)
    for source_file in files:
        entity_name = extract_entity_name(
            source_file.name,
            strip_prefix_regex=strip_prefix_regex,
            strip_suffix_regex=strip_suffix_regex,
        )
        grouped_files[entity_name].append(source_file)

    entities = {}
    for entity_name, entity_files in sorted(grouped_files.items()):
        file_reports = [
            _analyze_file(source_file, delimiter=delimiter)
            for source_file in sorted(entity_files)
        ]
        entities[entity_name] = _summarize_entity(entity_name, file_reports)

    return {
        "client_name": client_name,
        "folder": str(folder),
        "recursive": recursive,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "file_count": len(files),
        "entity_count": len(entities),
        "entities": entities,
    }


def extract_entity_name(
    file_name: str,
    strip_prefix_regex: str | None = None,
    strip_suffix_regex: str | None = None,
) -> str:
    entity_name = Path(file_name).stem.strip()
    if strip_prefix_regex:
        entity_name = re.sub(strip_prefix_regex, "", entity_name, flags=re.IGNORECASE).strip()
    if strip_suffix_regex:
        entity_name = re.sub(strip_suffix_regex, "", entity_name, flags=re.IGNORECASE).strip()
    return _normalize_entity_name(entity_name)


def _analyze_file(source_file: Path, delimiter: str) -> dict[str, object]:
    encoding = detect_encoding(source_file)
    file_delimiter = detect_delimiter(source_file, encoding) if delimiter == "auto" else delimiter
    header = _read_header(source_file, encoding, file_delimiter)
    normalized_header = [column.strip() for column in header]
    blank_header_positions = [
        index + 1 for index, column_name in enumerate(normalized_header) if not column_name
    ]
    duplicate_headers = sorted(
        column_name
        for column_name, count in Counter(normalized_header).items()
        if column_name and count > 1
    )
    canonical_columns = build_canonical_column_names(normalized_header)

    return {
        "source_file": str(source_file),
        "file_name": source_file.name,
        "encoding": encoding,
        "delimiter": file_delimiter,
        "column_count": len(normalized_header),
        "header_hash": _hash_header(normalized_header),
        "blank_header_count": len(blank_header_positions),
        "blank_header_positions": blank_header_positions,
        "duplicate_headers": duplicate_headers,
        "raw_header": normalized_header,
        "canonical_columns": canonical_columns,
    }


def _summarize_entity(entity_name: str, file_reports: list[dict[str, object]]) -> dict[str, object]:
    variants: dict[str, dict[str, object]] = {}
    all_canonical_columns: list[str] = []
    seen_columns = set()

    for file_report in file_reports:
        header_hash = str(file_report["header_hash"])
        if header_hash not in variants:
            variants[header_hash] = {
                "header_hash": header_hash,
                "column_count": file_report["column_count"],
                "canonical_columns": file_report["canonical_columns"],
                "files": [],
            }
        variants[header_hash]["files"].append(file_report["file_name"])

        for column_name in file_report["canonical_columns"]:
            if column_name not in seen_columns:
                seen_columns.add(column_name)
                all_canonical_columns.append(column_name)

    max_column_count = max((int(file_report["column_count"]) for file_report in file_reports), default=0)
    min_column_count = min((int(file_report["column_count"]) for file_report in file_reports), default=0)
    total_blank_headers = sum(int(file_report["blank_header_count"]) for file_report in file_reports)
    duplicate_headers = sorted(
        {
            duplicate_header
            for file_report in file_reports
            for duplicate_header in file_report["duplicate_headers"]
        }
    )
    recommendation = _recommend_entity_strategy(
        variant_count=len(variants),
        min_column_count=min_column_count,
        max_column_count=max_column_count,
        total_blank_headers=total_blank_headers,
        duplicate_headers=duplicate_headers,
    )

    return {
        "entity_name": entity_name,
        "file_count": len(file_reports),
        "variant_count": len(variants),
        "min_column_count": min_column_count,
        "max_column_count": max_column_count,
        "has_column_count_drift": min_column_count != max_column_count,
        "total_blank_header_count": total_blank_headers,
        "duplicate_headers": duplicate_headers,
        "recommendation": recommendation,
        "canonical_union_column_count": len(all_canonical_columns),
        "canonical_union_columns": all_canonical_columns,
        "variants": list(variants.values()),
        "files": file_reports,
    }


def _recommend_entity_strategy(
    variant_count: int,
    min_column_count: int,
    max_column_count: int,
    total_blank_headers: int,
    duplicate_headers: list[str],
) -> str:
    if variant_count == 1 and total_blank_headers == 0 and not duplicate_headers:
        return "single_shape_load"
    if min_column_count == max_column_count:
        return "single_shape_with_header_cleanup"
    return "schema_variant_review_required"


def _read_header(source_file: Path, encoding: str, delimiter: str) -> list[str]:
    with source_file.open(newline="", encoding=encoding, errors="replace") as file:
        reader = csv.reader(file, delimiter=delimiter)
        try:
            return next(reader)
        except StopIteration as exc:
            raise ValueError(f"Source file is empty: {source_file}") from exc


def _hash_header(header: list[str]) -> str:
    payload = json.dumps(header, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _normalize_entity_name(entity_name: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", entity_name.strip())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not normalized:
        raise ValueError("Entity name normalized to empty string.")
    return normalized.upper()


def _normalize_extensions(extensions: list[str]) -> set[str]:
    return {
        extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        for extension in extensions
    }


def _default_output_path(client_name: str | None) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_client = _normalize_entity_name(client_name or "source")
    return LOGS_DIR / "schema_variants" / f"{safe_client}_schema_variants_{timestamp}.json"


def _summarize_report(report: dict[str, object], output_path: Path) -> dict[str, object]:
    entities = report["entities"]
    review_required = [
        entity_name
        for entity_name, entity_report in entities.items()
        if entity_report["recommendation"] == "schema_variant_review_required"
    ]
    return {
        "output_path": str(output_path),
        "file_count": report["file_count"],
        "entity_count": report["entity_count"],
        "schema_variant_review_required_count": len(review_required),
        "schema_variant_review_required": review_required,
    }


if __name__ == "__main__":
    main()
