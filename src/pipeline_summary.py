import csv
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from src.db_connection import get_connection
from src.project_paths import LOGS_DIR

STEP_ORDER = {
    "PRELOAD_CSV": 1,
    "RAW_LOAD": 2,
    "STAGING_LOAD": 3,
    "TARGET_LOAD": 4,
    "PIPELINE_TOTAL": 5,
}

LOAD_METHOD_TO_STEP = {
    "PRELOAD_PROFILE": "PRELOAD_CSV",
    "BCP": "RAW_LOAD",
    "PYODBC": "RAW_LOAD",
    "RAW_TO_STG": "STAGING_LOAD",
    "SQL_TO_STG": "STAGING_LOAD",
    "STG_TO_ZEN": "TARGET_LOAD",
    "SQL_TO_TARGET": "TARGET_LOAD",
}


def write_pipeline_run_summary_csv(run_id: str) -> Path:
    run_row, count_rows, load_rows = _fetch_run_audit_data(run_id)
    summary_rows = _build_summary_rows(run_row, count_rows, load_rows)
    return _write_summary_rows_to_csv(
        summary_rows=summary_rows,
        output_name=f"{run_row['pipeline_name'] if run_row else 'unknown_pipeline'}_{run_id}_summary.csv",
        append_timestamp=False,
    )


def write_pipeline_day_summary_csv(summary_date: date) -> Path:
    run_ids = _fetch_run_ids_for_date(summary_date)
    return write_pipeline_runs_summary_csv(
        run_ids=run_ids,
        base_name=f"pipeline_runs_{summary_date.isoformat()}_summary",
    )


def write_pipeline_runs_summary_csv(run_ids: list[str], base_name: str) -> Path:
    summary_rows: list[dict[str, object]] = []

    for run_id in run_ids:
        run_row, count_rows, load_rows = _fetch_run_audit_data(run_id)
        summary_rows.extend(_build_summary_rows(run_row, count_rows, load_rows))

    return _write_summary_rows_to_csv(
        summary_rows=summary_rows,
        output_name=f"{base_name}.csv",
    )


def _write_summary_rows_to_csv(
    summary_rows: list[dict[str, object]],
    output_name: str,
    append_timestamp: bool = True,
) -> Path:
    output_dir = LOGS_DIR / "pipeline_summaries"
    output_dir.mkdir(parents=True, exist_ok=True)
    final_name = output_name
    if append_timestamp:
        final_name = _append_timestamp_to_filename(output_name)
    output_path = output_dir / final_name

    with open(output_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "run_id",
                "pipeline_name",
                "pipeline_status",
                "step_name",
                "step_status",
                "table_name",
                "source_name",
                "proper_row_count",
                "malformed_row_count",
                "repaired_row_count",
                "duration_ms",
                "started_at",
                "finished_at",
                "captured_at",
                "load_methods",
                "notes",
            ],
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    return output_path


def _append_timestamp_to_filename(output_name: str) -> str:
    path = Path(output_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{path.stem}_{timestamp}{path.suffix}"


def _fetch_run_ids_for_date(summary_date: date) -> list[str]:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT RunID
            FROM audit.PipelineRunLog
            WHERE CAST(StartTime AS DATE) = ?
            ORDER BY StartTime, RunID
            """,
            summary_date,
        )
        return [str(row[0]) for row in cursor.fetchall()]
    finally:
        conn.close()


def _fetch_run_audit_data(
    run_id: str,
) -> tuple[dict[str, object] | None, list[dict[str, object]], list[dict[str, object]]]:
    conn = get_connection()
    try:
        try:
            import pyodbc
        except ModuleNotFoundError:
            pyodbc = None
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                RunID,
                PipelineName,
                Status,
                SourceTable,
                TargetTable,
                SourceRowCount,
                TargetRowCount,
                StartTime,
                EndTime,
                DurationMs,
                ErrorMessage
            FROM audit.PipelineRunLog
            WHERE RunID = ?
            """,
            run_id,
        )
        pipeline_row = cursor.fetchone()
        run_row = None
        if pipeline_row:
            run_row = {
                "run_id": pipeline_row[0],
                "pipeline_name": pipeline_row[1],
                "status": pipeline_row[2],
                "source_table": pipeline_row[3],
                "target_table": pipeline_row[4],
                "source_row_count": pipeline_row[5],
                "target_row_count": pipeline_row[6],
                "start_time": pipeline_row[7],
                "end_time": pipeline_row[8],
                "duration_ms": pipeline_row[9],
                "error_message": pipeline_row[10],
            }

        count_query = """
            SELECT
                TableName,
                SourceFile,
                CountStage,
                ProperRowCount,
                MalformedRowCount,
                RepairedRowCount,
                CapturedAt
            FROM audit.TableRowCountLog
            WHERE RunID = ?
            ORDER BY CapturedAt, TableRowCountLogID
            """
        try:
            cursor.execute(count_query, run_id)
        except Exception as exc:
            if pyodbc is None or not isinstance(exc, pyodbc.ProgrammingError):
                raise
            if "RepairedRowCount" not in str(exc):
                raise
            cursor.execute(
                """
                SELECT
                    TableName,
                    SourceFile,
                    CountStage,
                    ProperRowCount,
                    MalformedRowCount,
                    0 AS RepairedRowCount,
                    CapturedAt
                FROM audit.TableRowCountLog
                WHERE RunID = ?
                ORDER BY CapturedAt, TableRowCountLogID
                """,
                run_id,
            )
        count_rows = [
            {
                "table_name": row[0],
                "source_file": row[1],
                "count_stage": row[2],
                "proper_row_count": row[3],
                "malformed_row_count": row[4],
                "repaired_row_count": row[5],
                "captured_at": row[6],
            }
            for row in cursor.fetchall()
        ]

        cursor.execute(
            """
            SELECT
                TableName,
                SourceFile,
                LoadMethod,
                LoadStatus,
                ChunkRowCount,
                StartedAt,
                FinishedAt,
                DurationMs,
                DetailMessage
            FROM audit.LoadExecutionLog
            WHERE RunID = ?
            ORDER BY StartedAt, LoadExecutionLogID
            """,
            run_id,
        )
        load_rows = [
            {
                "table_name": row[0],
                "source_file": row[1],
                "load_method": row[2],
                "load_status": row[3],
                "chunk_row_count": row[4],
                "started_at": row[5],
                "finished_at": row[6],
                "duration_ms": row[7],
                "detail_message": row[8],
            }
            for row in cursor.fetchall()
        ]
        return run_row, count_rows, load_rows
    finally:
        conn.close()


def _build_summary_rows(
    run_row: dict[str, object] | None,
    count_rows: list[dict[str, object]],
    load_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    pipeline_name = run_row["pipeline_name"] if run_row else None
    pipeline_status = run_row["status"] if run_row else None
    step_rows: dict[str, dict[str, object]] = {}

    for count_row in count_rows:
        step_name = str(count_row["count_stage"])
        step_entry = step_rows.setdefault(
            step_name,
            _build_empty_step_row(pipeline_name, pipeline_status, step_name),
        )
        step_entry["table_name"] = count_row["table_name"]
        step_entry["source_name"] = count_row["source_file"]
        step_entry["proper_row_count"] = count_row["proper_row_count"]
        step_entry["malformed_row_count"] = count_row["malformed_row_count"]
        step_entry["repaired_row_count"] = count_row["repaired_row_count"]
        step_entry["captured_at"] = _to_text(count_row["captured_at"])

    grouped_load_rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    for load_row in load_rows:
        step_name = LOAD_METHOD_TO_STEP.get(str(load_row["load_method"]))
        if step_name is None:
            continue
        grouped_load_rows[step_name].append(load_row)

    for step_name, rows in grouped_load_rows.items():
        step_entry = step_rows.setdefault(
            step_name,
            _build_empty_step_row(pipeline_name, pipeline_status, step_name),
        )
        step_entry["table_name"] = step_entry["table_name"] or rows[-1]["table_name"]
        step_entry["source_name"] = step_entry["source_name"] or rows[-1]["source_file"]
        step_entry["step_status"] = _aggregate_step_status(rows)
        step_entry["duration_ms"] = sum(int(row["duration_ms"] or 0) for row in rows)
        step_entry["started_at"] = _to_text(min(row["started_at"] for row in rows if row["started_at"]))
        step_entry["finished_at"] = _to_text(
            max(row["finished_at"] for row in rows if row["finished_at"])
        )
        step_entry["load_methods"] = ",".join(
            sorted({str(row["load_method"]) for row in rows if row["load_method"]})
        )
        if not step_entry["proper_row_count"]:
            step_entry["proper_row_count"] = sum(int(row["chunk_row_count"] or 0) for row in rows)
        notes = [str(row["detail_message"]) for row in rows if row["detail_message"]]
        step_entry["notes"] = " | ".join(notes[:3])

    pipeline_total = _build_empty_step_row(pipeline_name, pipeline_status, "PIPELINE_TOTAL")
    pipeline_total["step_status"] = pipeline_status
    pipeline_total["table_name"] = run_row["target_table"] if run_row else None
    pipeline_total["source_name"] = run_row["source_table"] if run_row else None
    pipeline_total["proper_row_count"] = (
        run_row["target_row_count"]
        if run_row and run_row["target_row_count"] is not None
        else (run_row["source_row_count"] if run_row else None)
    )
    pipeline_total["duration_ms"] = run_row["duration_ms"] if run_row else None
    pipeline_total["started_at"] = _to_text(run_row["start_time"] if run_row else None)
    pipeline_total["finished_at"] = _to_text(run_row["end_time"] if run_row else None)
    pipeline_total["notes"] = run_row["error_message"] if run_row else None
    step_rows["PIPELINE_TOTAL"] = pipeline_total

    return [
        {
            "run_id": run_row["run_id"] if run_row else None,
            **step_rows[step_name],
        }
        for step_name in sorted(step_rows, key=lambda item: STEP_ORDER.get(item, 99))
    ]


def _build_empty_step_row(
    pipeline_name: str | None,
    pipeline_status: str | None,
    step_name: str,
) -> dict[str, object]:
    return {
        "pipeline_name": pipeline_name,
        "pipeline_status": pipeline_status,
        "step_name": step_name,
        "step_status": None,
        "table_name": None,
        "source_name": None,
        "proper_row_count": None,
        "malformed_row_count": 0,
        "repaired_row_count": 0,
        "duration_ms": None,
        "started_at": None,
        "finished_at": None,
        "captured_at": None,
        "load_methods": None,
        "notes": None,
    }


def _aggregate_step_status(rows: list[dict[str, object]]) -> str:
    statuses = [str(row["load_status"]) for row in rows if row["load_status"]]
    if any(status == "FAILED" for status in statuses):
        return "FAILED"
    if any(status == "SUCCESS" for status in statuses):
        return "SUCCESS"
    return statuses[-1] if statuses else ""


def _to_text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)
