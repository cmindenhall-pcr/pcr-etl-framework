from datetime import datetime

from src.pipeline_summary import _append_timestamp_to_filename, _build_summary_rows


def test_build_summary_rows_merges_counts_and_durations() -> None:
    run_row = {
        "run_id": "abc12345",
        "pipeline_name": "Porecline",
        "status": "SUCCESS",
        "source_table": "raw.Porecline",
        "target_table": "zen.Porecline",
        "source_row_count": 10,
        "target_row_count": 10,
        "start_time": datetime(2026, 3, 19, 8, 0, 0),
        "end_time": datetime(2026, 3, 19, 8, 0, 9),
        "duration_ms": 9000,
        "error_message": None,
    }
    count_rows = [
        {
            "table_name": "raw.Porecline",
            "source_file": r"C:\data\porecline.csv",
            "count_stage": "PRELOAD_CSV",
            "proper_row_count": 10,
            "malformed_row_count": 2,
            "repaired_row_count": 2,
            "captured_at": datetime(2026, 3, 19, 8, 0, 1),
        },
        {
            "table_name": "raw.Porecline",
            "source_file": r"C:\data\porecline.csv",
            "count_stage": "RAW_LOAD",
            "proper_row_count": 10,
            "malformed_row_count": 2,
            "repaired_row_count": 2,
            "captured_at": datetime(2026, 3, 19, 8, 0, 3),
        },
        {
            "table_name": "stg.Porecline",
            "source_file": "raw.Porecline",
            "count_stage": "STAGING_LOAD",
            "proper_row_count": 10,
            "malformed_row_count": 0,
            "repaired_row_count": 0,
            "captured_at": datetime(2026, 3, 19, 8, 0, 5),
        },
        {
            "table_name": "zen.Porecline",
            "source_file": "stg.Porecline",
            "count_stage": "TARGET_LOAD",
            "proper_row_count": 10,
            "malformed_row_count": 0,
            "repaired_row_count": 0,
            "captured_at": datetime(2026, 3, 19, 8, 0, 8),
        },
    ]
    load_rows = [
        {
            "table_name": "raw.Porecline",
            "source_file": r"C:\data\porecline.csv",
            "load_method": "PRELOAD_PROFILE",
            "load_status": "SUCCESS",
            "chunk_row_count": 10,
            "started_at": datetime(2026, 3, 19, 8, 0, 0),
            "finished_at": datetime(2026, 3, 19, 8, 0, 1),
            "duration_ms": 1000,
            "detail_message": "profile",
        },
        {
            "table_name": "raw.Porecline",
            "source_file": r"C:\data\porecline.csv",
            "load_method": "PYODBC",
            "load_status": "SUCCESS",
            "chunk_row_count": 10,
            "started_at": datetime(2026, 3, 19, 8, 0, 2),
            "finished_at": datetime(2026, 3, 19, 8, 0, 4),
            "duration_ms": 2000,
            "detail_message": "raw load",
        },
        {
            "table_name": "stg.Porecline",
            "source_file": "raw.Porecline",
            "load_method": "RAW_TO_STG",
            "load_status": "SUCCESS",
            "chunk_row_count": 10,
            "started_at": datetime(2026, 3, 19, 8, 0, 4),
            "finished_at": datetime(2026, 3, 19, 8, 0, 6),
            "duration_ms": 2000,
            "detail_message": "staging",
        },
        {
            "table_name": "zen.Porecline",
            "source_file": "stg.Porecline",
            "load_method": "STG_TO_ZEN",
            "load_status": "SUCCESS",
            "chunk_row_count": 10,
            "started_at": datetime(2026, 3, 19, 8, 0, 6),
            "finished_at": datetime(2026, 3, 19, 8, 0, 8),
            "duration_ms": 2000,
            "detail_message": "target",
        },
    ]

    rows = _build_summary_rows(run_row, count_rows, load_rows)

    assert [row["step_name"] for row in rows] == [
        "PRELOAD_CSV",
        "RAW_LOAD",
        "STAGING_LOAD",
        "TARGET_LOAD",
        "PIPELINE_TOTAL",
    ]
    assert rows[0]["repaired_row_count"] == 2
    assert rows[1]["duration_ms"] == 2000
    assert rows[3]["proper_row_count"] == 10
    assert rows[4]["duration_ms"] == 9000


def test_build_summary_rows_keeps_run_id_per_row() -> None:
    run_row = {
        "run_id": "xyz98765",
        "pipeline_name": "Mainvdtl",
        "status": "SUCCESS",
        "source_table": "raw.Mainvdtl",
        "target_table": "zen.Mainvdtl",
        "source_row_count": 20,
        "target_row_count": 20,
        "start_time": datetime(2026, 3, 19, 9, 0, 0),
        "end_time": datetime(2026, 3, 19, 9, 0, 5),
        "duration_ms": 5000,
        "error_message": None,
    }

    rows = _build_summary_rows(run_row, [], [])

    assert rows == [
        {
            "run_id": "xyz98765",
            "pipeline_name": "Mainvdtl",
            "pipeline_status": "SUCCESS",
            "step_name": "PIPELINE_TOTAL",
            "step_status": "SUCCESS",
            "table_name": "zen.Mainvdtl",
            "source_name": "raw.Mainvdtl",
            "proper_row_count": 20,
            "malformed_row_count": 0,
            "repaired_row_count": 0,
            "duration_ms": 5000,
            "started_at": "2026-03-19 09:00:00",
            "finished_at": "2026-03-19 09:00:05",
            "captured_at": None,
            "load_methods": None,
            "notes": None,
        }
    ]


def test_append_timestamp_to_filename_keeps_base_name() -> None:
    name = _append_timestamp_to_filename("pipeline_runs_2026-03-19_summary.csv")

    assert name.startswith("pipeline_runs_2026-03-19_summary_")
    assert name.endswith(".csv")
