import argparse
import csv
import hashlib
import json
import re
from pathlib import Path

from src.csv_utils import build_canonical_column_names

ENTITY_PATTERN = re.compile(
    r"^(?P<entity>.+?)_(?P<from>\d{8})_(?P<to>\d{8})(?:_(?P<part>\d+))?\.csv$",
    re.IGNORECASE,
)
ENCODING_CANDIDATES = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
SNIFFER_DELIMITERS = [",", "|", "\t", ";"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate raw-only multi-file pipeline config from a folder tree of CSV extracts."
    )
    parser.add_argument("folder", help="Root folder containing inbound CSV files")
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=[".csv"],
        help="File extensions to include. Default: .csv",
    )
    parser.add_argument(
        "--delimiter",
        default=",",
        help="Delimited file separator, or 'auto' to sniff per entity. Default: comma.",
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
        "--split-schema-variants",
        action="store_true",
        help="Split files with different headers into separate pipelines.",
    )
    parser.add_argument(
        "--canonicalize-headers",
        action="store_true",
        help="Generate unique SQL-safe column names for blank, duplicate, or punctuated headers.",
    )
    parser.add_argument(
        "--no-header",
        action="store_true",
        help="Treat files as headerless and generate Column_001-style source columns.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Include files in subfolders. Default: only files directly in the folder.",
    )
    parser.add_argument(
        "--include-staging",
        action="store_true",
        help="Include staging_table entries using stg.<TitleCasedPipelineName>.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path to write the generated pipeline config JSON.",
    )
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")

    config = build_pipeline_config(
        folder=folder,
        extensions=args.extensions,
        delimiter=args.delimiter,
        strip_prefix_regex=args.strip_prefix_regex,
        strip_suffix_regex=args.strip_suffix_regex,
        split_schema_variants=args.split_schema_variants,
        canonicalize_headers=args.canonicalize_headers,
        has_header=not args.no_header,
        recursive=args.recursive,
        include_staging=args.include_staging,
    )
    output_path = Path(args.output)
    output_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(json.dumps({"output_path": str(output_path), "pipeline_count": len(config)}, indent=2))


def build_pipeline_config(
    folder: Path,
    extensions: list[str] | None = None,
    delimiter: str = ",",
    strip_prefix_regex: str | None = None,
    strip_suffix_regex: str | None = None,
    split_schema_variants: bool = False,
    canonicalize_headers: bool = False,
    has_header: bool = True,
    recursive: bool = True,
    include_staging: bool = False,
) -> dict[str, dict]:
    normalized_extensions = _normalize_extensions(extensions or [".csv"])
    candidates = folder.rglob("*") if recursive else folder.glob("*")
    source_files = sorted(
        path
        for path in candidates
        if path.is_file() and path.suffix.lower() in normalized_extensions
    )
    grouped_files: dict[tuple[str, str | None], list[Path]] = {}

    for file_path in source_files:
        entity_name = extract_entity_name(
            file_path.name,
            strip_prefix_regex=strip_prefix_regex,
            strip_suffix_regex=strip_suffix_regex,
        )
        variant_key = None
        if split_schema_variants:
            encoding = detect_encoding(file_path)
            entity_delimiter = detect_delimiter(file_path, encoding) if delimiter == "auto" else delimiter
            variant_key = _hash_header(
                read_source_columns(
                    file_path,
                    encoding,
                    entity_delimiter,
                    canonicalize_headers=False,
                    has_header=has_header,
                )
            )
        group_key = (entity_name, variant_key)
        grouped_files.setdefault(group_key, []).append(file_path)

    config: dict[str, dict] = {}
    variant_numbers = _build_variant_numbers(grouped_files)
    for group_key, files in sorted(grouped_files.items(), key=lambda item: (item[0][0], item[0][1] or "")):
        entity_name, variant_key = group_key
        pipeline_name = entity_name
        if split_schema_variants and variant_key is not None:
            pipeline_name = f"{entity_name}_V{variant_numbers[group_key]:02d}"
        sample_file = sorted(files)[0]
        encoding = detect_encoding(sample_file)
        entity_delimiter = detect_delimiter(sample_file, encoding) if delimiter == "auto" else delimiter
        columns = read_source_columns(
            sample_file,
            encoding,
            entity_delimiter,
            canonicalize_headers=canonicalize_headers,
            has_header=has_header,
        )
        config[pipeline_name] = {
            "source_table": f"raw.{pipeline_name}",
            "preload_profile": {
                "source_files": [str(path) for path in sorted(files)],
                "column_mappings": {column: column for column in columns},
                "max_malformed_rows": 0,
                "has_header": has_header,
                "delimiter": entity_delimiter,
            },
        }
        if canonicalize_headers and has_header:
            config[pipeline_name]["preload_profile"]["canonicalize_headers"] = True
        if split_schema_variants:
            config[pipeline_name]["source_entity"] = entity_name
            config[pipeline_name]["schema_variant_key"] = variant_key
        if include_staging:
            config[pipeline_name]["staging_table"] = f"stg.{_to_staging_table_name(pipeline_name)}"

    return config


def extract_entity_name(
    file_name: str,
    strip_prefix_regex: str | None = None,
    strip_suffix_regex: str | None = None,
) -> str:
    match = ENTITY_PATTERN.match(file_name)
    entity_name = match.group("entity") if match else Path(file_name).stem
    if strip_prefix_regex:
        entity_name = re.sub(strip_prefix_regex, "", entity_name, flags=re.IGNORECASE)
    if strip_suffix_regex:
        entity_name = re.sub(strip_suffix_regex, "", entity_name, flags=re.IGNORECASE)
    return entity_name.strip()


def _normalize_extensions(extensions: list[str]) -> set[str]:
    return {
        extension.lower() if extension.startswith(".") else f".{extension.lower()}"
        for extension in extensions
    }


def _to_staging_table_name(entity_name: str) -> str:
    return "".join(part.capitalize() for part in entity_name.split("_"))


def read_source_columns(
    file_path: Path,
    encoding: str,
    delimiter: str,
    canonicalize_headers: bool = False,
    has_header: bool = True,
) -> list[str]:
    with open(file_path, newline="", encoding=encoding) as f:
        reader = csv.reader(f, delimiter=delimiter)
        try:
            first_row = next(reader)
        except StopIteration as exc:
            raise ValueError(f"Sample CSV file is empty: {file_path}") from exc

    if has_header:
        columns = [column.strip() for column in first_row]
        if canonicalize_headers:
            columns = build_canonical_column_names(columns)
    else:
        columns = [f"Column_{position:03d}" for position in range(1, len(first_row) + 1)]
    if not columns:
        raise ValueError(f"Sample CSV file has no usable columns: {file_path}")
    return columns


def _hash_header(header: list[str]) -> str:
    payload = json.dumps([column.strip() for column in header], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _build_variant_numbers(
    grouped_files: dict[tuple[str, str | None], list[Path]]
) -> dict[tuple[str, str | None], int]:
    counters: dict[str, int] = {}
    variant_numbers: dict[tuple[str, str | None], int] = {}
    for group_key in sorted(grouped_files, key=lambda item: (item[0], item[1] or "")):
        entity_name, variant_key = group_key
        if variant_key is None:
            variant_numbers[group_key] = 1
            continue
        counters[entity_name] = counters.get(entity_name, 0) + 1
        variant_numbers[group_key] = counters[entity_name]
    return variant_numbers


def detect_delimiter(file_path: Path, encoding: str, sample_bytes: int = 16384) -> str:
    with file_path.open("rb") as file:
        raw_sample = file.read(sample_bytes)
    sample_text = raw_sample.decode(encoding, errors="replace")
    if not sample_text.strip():
        return ","

    try:
        return csv.Sniffer().sniff(sample_text, delimiters="".join(SNIFFER_DELIMITERS)).delimiter
    except csv.Error:
        first_line = sample_text.splitlines()[0] if sample_text.splitlines() else ""
        delimiter_counts = {
            delimiter: first_line.count(delimiter)
            for delimiter in SNIFFER_DELIMITERS
        }
        return max(delimiter_counts, key=delimiter_counts.get)


def detect_encoding(file_path: Path, sample_bytes: int = 16384) -> str:
    with file_path.open("rb") as file:
        raw_sample = file.read(sample_bytes)
    if raw_sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"

    for encoding in ENCODING_CANDIDATES:
        try:
            raw_sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue

    return "latin-1"


if __name__ == "__main__":
    main()
