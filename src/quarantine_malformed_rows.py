import argparse
import csv
import json
import io
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from src.generate_multifile_raw_pipeline_config import detect_encoding
from src.project_paths import LOGS_DIR
from src.csv_utils import (
    ensure_max_csv_field_size,
    trim_trailing_blank_header_columns,
    trim_trailing_blank_row_values,
)

ensure_max_csv_field_size()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write malformed delimited rows with surrounding context to quarantine CSV files."
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Pipeline config JSON containing preload_profile sections.",
    )
    parser.add_argument(
        "--database",
        required=True,
        help="Database/client name used in the quarantine output path.",
    )
    parser.add_argument(
        "--context-rows",
        type=int,
        default=5,
        help="Number of rows before and after each malformed row. Default: 5.",
    )
    parser.add_argument(
        "--output-root",
        default=str(LOGS_DIR / "quarantine"),
        help="Root folder for quarantine files. Default: logs/quarantine.",
    )
    args = parser.parse_args()

    result = write_quarantine_files(
        config_path=Path(args.config),
        database_name=args.database,
        context_rows=args.context_rows,
        output_root=Path(args.output_root),
    )
    print(json.dumps(result, indent=2))


def write_quarantine_files(
    config_path: Path,
    database_name: str,
    context_rows: int = 5,
    output_root: Path | None = None,
) -> dict[str, object]:
    if context_rows < 0:
        raise ValueError("context_rows must be zero or greater.")

    output_root = output_root or (LOGS_DIR / "quarantine")
    output_dir = output_root / database_name
    output_dir.mkdir(parents=True, exist_ok=True)

    config = json.loads(config_path.read_text(encoding="utf-8"))
    generated_at_utc = datetime.now(timezone.utc).isoformat()
    written_files = []
    total_malformed_rows = 0

    for pipeline_name, pipeline in config.items():
        profile = pipeline.get("preload_profile")
        if not profile:
            continue

        output_path = output_dir / f"{pipeline_name}_malformed_context.csv"
        malformed_count = _write_pipeline_quarantine(
            pipeline_name=pipeline_name,
            profile=profile,
            output_path=output_path,
            context_rows=context_rows,
            generated_at_utc=generated_at_utc,
        )
        if malformed_count == 0:
            if output_path.exists():
                output_path.unlink()
            continue

        total_malformed_rows += malformed_count
        written_files.append(
            {
                "pipeline_name": pipeline_name,
                "malformed_row_count": malformed_count,
                "output_path": str(output_path),
            }
        )

    return {
        "database": database_name,
        "config_path": str(config_path),
        "context_rows": context_rows,
        "total_malformed_rows": total_malformed_rows,
        "written_files": written_files,
    }


QUARANTINE_COLUMNS = [
    "generated_at_utc",
    "pipeline_name",
    "source_file",
    "malformed_group_id",
    "row_role",
    "relative_offset",
    "is_malformed",
    "physical_row_number",
    "expected_column_count",
    "actual_column_count",
    "raw_line_text",
]


def _write_pipeline_quarantine(
    pipeline_name: str,
    profile: dict,
    output_path: Path,
    context_rows: int,
    generated_at_utc: str,
) -> int:
    malformed_count = 0
    delimiter = profile["delimiter"]
    source_files = [Path(path) for path in profile["source_files"]]
    header_row_number = int(profile.get("header_row_number", 1))

    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=QUARANTINE_COLUMNS)
        writer.writeheader()

        for source_file in source_files:
            malformed_count += _write_source_file_quarantine(
                writer=writer,
                pipeline_name=pipeline_name,
                source_file=source_file,
                delimiter=delimiter,
                header_row_number=header_row_number,
                context_rows=context_rows,
                generated_at_utc=generated_at_utc,
            )

    return malformed_count


def _write_source_file_quarantine(
    writer: csv.DictWriter,
    pipeline_name: str,
    source_file: Path,
    delimiter: str,
    header_row_number: int,
    context_rows: int,
    generated_at_utc: str,
) -> int:
    if header_row_number < 1:
        raise ValueError("header_row_number must be at least 1.")

    encoding = detect_encoding(source_file)
    previous_rows: deque[dict[str, object]] = deque(maxlen=context_rows)
    active_groups: list[dict[str, int | str]] = []
    malformed_count = 0

    with source_file.open(encoding=encoding, errors="replace", newline="") as file:
        reader = csv.reader(file, delimiter=delimiter)
        header_row = None
        for row_number, row in enumerate(reader, start=1):
            if row_number == header_row_number:
                header_row = row
                break

        if header_row is None:
            return 0

        expected_column_count = len(trim_trailing_blank_header_columns(header_row))

        for row in reader:
            physical_row_number = reader.line_num
            trimmed_row = trim_trailing_blank_row_values(row, expected_column_count)
            actual_column_count = len(trimmed_row)
            row_text = _serialize_csv_row(row, delimiter)
            row_is_blank = all((value or "").strip() == "" for value in row)
            is_malformed = (not row_is_blank) and actual_column_count != expected_column_count
            row_info = {
                "physical_row_number": physical_row_number,
                "actual_column_count": actual_column_count,
                "raw_line_text": row_text,
                "is_malformed": int(is_malformed),
            }

            _write_active_group_after_rows(
                writer=writer,
                active_groups=active_groups,
                row_info=row_info,
                pipeline_name=pipeline_name,
                source_file=source_file,
                expected_column_count=expected_column_count,
                context_rows=context_rows,
                generated_at_utc=generated_at_utc,
            )

            if is_malformed:
                malformed_count += 1
                malformed_group_id = (
                    f"{source_file.stem}:row:{physical_row_number}"
                )
                for previous_row in previous_rows:
                    _write_quarantine_row(
                        writer=writer,
                        generated_at_utc=generated_at_utc,
                        pipeline_name=pipeline_name,
                        source_file=source_file,
                        malformed_group_id=malformed_group_id,
                        row_role="before",
                        relative_offset=int(previous_row["physical_row_number"])
                        - physical_row_number,
                        is_malformed=int(previous_row["is_malformed"]),
                        physical_row_number=int(previous_row["physical_row_number"]),
                        expected_column_count=expected_column_count,
                        actual_column_count=int(previous_row["actual_column_count"]),
                        raw_line_text=str(previous_row["raw_line_text"]),
                    )
                _write_quarantine_row(
                    writer=writer,
                    generated_at_utc=generated_at_utc,
                    pipeline_name=pipeline_name,
                    source_file=source_file,
                    malformed_group_id=malformed_group_id,
                    row_role="malformed",
                    relative_offset=0,
                    is_malformed=1,
                    physical_row_number=physical_row_number,
                    expected_column_count=expected_column_count,
                    actual_column_count=actual_column_count,
                    raw_line_text=row_text,
                )
                active_groups.append(
                    {
                        "malformed_group_id": malformed_group_id,
                        "malformed_row_number": physical_row_number,
                        "remaining_after_rows": context_rows,
                    }
                )

            previous_rows.append(row_info)

    return malformed_count


def _write_active_group_after_rows(
    writer: csv.DictWriter,
    active_groups: list[dict[str, int | str]],
    row_info: dict[str, object],
    pipeline_name: str,
    source_file: Path,
    expected_column_count: int,
    context_rows: int,
    generated_at_utc: str,
) -> None:
    for group in list(active_groups):
        remaining_after_rows = int(group["remaining_after_rows"])
        if remaining_after_rows <= 0:
            active_groups.remove(group)
            continue

        relative_offset = int(row_info["physical_row_number"]) - int(
            group["malformed_row_number"]
        )
        if relative_offset <= 0:
            continue
        if relative_offset > context_rows:
            active_groups.remove(group)
            continue

        _write_quarantine_row(
            writer=writer,
            generated_at_utc=generated_at_utc,
            pipeline_name=pipeline_name,
            source_file=source_file,
            malformed_group_id=str(group["malformed_group_id"]),
            row_role="after",
            relative_offset=relative_offset,
            is_malformed=int(row_info["is_malformed"]),
            physical_row_number=int(row_info["physical_row_number"]),
            expected_column_count=expected_column_count,
            actual_column_count=int(row_info["actual_column_count"]),
            raw_line_text=str(row_info["raw_line_text"]),
        )

        group["remaining_after_rows"] = remaining_after_rows - 1
        if int(group["remaining_after_rows"]) <= 0:
            active_groups.remove(group)


def _write_quarantine_row(
    writer: csv.DictWriter,
    generated_at_utc: str,
    pipeline_name: str,
    source_file: Path,
    malformed_group_id: str,
    row_role: str,
    relative_offset: int,
    is_malformed: int,
    physical_row_number: int,
    expected_column_count: int,
    actual_column_count: int,
    raw_line_text: str,
) -> None:
    writer.writerow(
        {
            "generated_at_utc": generated_at_utc,
            "pipeline_name": pipeline_name,
            "source_file": str(source_file),
            "malformed_group_id": malformed_group_id,
            "row_role": row_role,
            "relative_offset": relative_offset,
            "is_malformed": is_malformed,
            "physical_row_number": physical_row_number,
            "expected_column_count": expected_column_count,
            "actual_column_count": actual_column_count,
            "raw_line_text": raw_line_text,
        }
    )


def _serialize_csv_row(row: list[str], delimiter: str) -> str:
    output = io.StringIO()
    writer = csv.writer(output, delimiter=delimiter, lineterminator="")
    writer.writerow(row)
    return output.getvalue()


if __name__ == "__main__":
    main()
