import argparse
import sys
import uuid
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime

from src.project_paths import SQL_DIR
from src.sql_runner import run_sql_file
from src.validators import get_row_count, validate_row_match, get_row_counts
from src.config_loader import load_pipeline_config
from src.logger import get_logger
from src.audit_logger import (
    insert_column_profile_rows,
    insert_load_execution_log,
    insert_pipeline_run_start,
    insert_table_row_count,
    truncate_audit_tables,
    update_pipeline_run_end,
)
from src.preload_profiler import profile_csv_file
from src.csv_loader import load_csv_to_table
from src.stg_loader import load_raw_to_staging
from src.pipeline_summary import write_pipeline_run_summary_csv, write_pipeline_runs_summary_csv
from src.zen_loader import append_staging_to_zen

logger = get_logger()
MAX_PARALLEL_PIPELINES_CAP = 4


def _print_batch_progress(
    completed_pipelines: int,
    total_pipelines: int,
    pipeline_name: str,
    mode_label: str,
    batch_start_time: datetime,
    pipeline_start_time: datetime | None = None,
) -> None:
    bar_width = 24
    ratio = completed_pipelines / total_pipelines if total_pipelines else 0
    filled_width = int(ratio * bar_width)
    bar = "#" * filled_width + "-" * (bar_width - filled_width)
    percent = int(ratio * 100)
    batch_elapsed_seconds = max(int((datetime.now() - batch_start_time).total_seconds()), 0)
    batch_elapsed_label = _format_duration(batch_elapsed_seconds)
    batch_eta_label = "--:--"
    current_elapsed_label = "--:--"

    if completed_pipelines > 0:
        average_seconds_per_pipeline = batch_elapsed_seconds / completed_pipelines
        remaining_pipelines = max(total_pipelines - completed_pipelines, 0)
        eta_seconds = int(average_seconds_per_pipeline * remaining_pipelines)
        batch_eta_label = _format_duration(eta_seconds)

    if pipeline_start_time is not None:
        current_elapsed_seconds = max(
            int((datetime.now() - pipeline_start_time).total_seconds()), 0
        )
        current_elapsed_label = _format_duration(current_elapsed_seconds)

    print(
        f"[{bar}] {percent:>3}% ({completed_pipelines}/{total_pipelines}) "
        f"{mode_label} | {pipeline_name} | batch elapsed {batch_elapsed_label} | "
        f"batch eta {batch_eta_label} | current {current_elapsed_label}",
        flush=True,
    )


def _format_duration(total_seconds: int) -> str:
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours > 0:
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    return f"{minutes:02}:{seconds:02}"


def _write_summary_csv(run_id: str) -> None:
    try:
        summary_path = write_pipeline_run_summary_csv(run_id)
        logger.info(f"[run_id={run_id}] Wrote pipeline summary CSV: {summary_path}")
    except Exception:
        logger.exception(f"[run_id={run_id}] Failed writing pipeline summary CSV")


def _write_batch_summary_csv(
    run_ids: list[str],
    batch_start_time: datetime,
    profile_only: bool,
    raw_only: bool,
) -> None:
    try:
        summary_path = write_pipeline_runs_summary_csv(
            run_ids=run_ids,
            base_name=(
                f"pipeline_runs_{batch_start_time.date().isoformat()}_profile_summary"
                if profile_only
                else f"pipeline_runs_{batch_start_time.date().isoformat()}_raw_summary"
                if raw_only
                else f"pipeline_runs_{batch_start_time.date().isoformat()}_summary"
            ),
        )
        logger.info(f"Wrote batch summary CSV: {summary_path}")
    except Exception:
        logger.exception("Failed writing batch summary CSV")


def _run_sql_step_with_audit(
    file_path,
    table_name: str,
    source_name: str | None,
    load_method: str,
    pipeline_name: str,
    run_id: str,
) -> int:
    started_at = datetime.now()
    try:
        run_sql_file(file_path, run_id=run_id)
        row_count = get_row_count(table_name)
        finished_at = datetime.now()
        insert_load_execution_log(
            run_id=run_id,
            pipeline_name=pipeline_name,
            table_name=table_name,
            source_file=source_name,
            load_method=load_method,
            load_status="SUCCESS",
            chunk_row_count=row_count,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            detail_message=f"Executed SQL file {file_path.name}.",
        )
        return row_count
    except Exception as exc:
        finished_at = datetime.now()
        insert_load_execution_log(
            run_id=run_id,
            pipeline_name=pipeline_name,
            table_name=table_name,
            source_file=source_name,
            load_method=load_method,
            load_status="FAILED",
            chunk_row_count=0,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=int((finished_at - started_at).total_seconds() * 1000),
            detail_message=str(exc),
        )
        raise


def run_pipeline(
    pipeline_name: str,
    profile_only: bool = False,
    raw_only: bool = False,
    staging_only: bool = False,
    zen_only: bool = False,
    write_individual_summary: bool = True,
    reset_audit_for_profile: bool = True,
) -> str:
    run_id = str(uuid.uuid4())[:8]
    start_time = datetime.now()

    source_count = None
    target_count = None

    try:
        if profile_only and reset_audit_for_profile:
            logger.info("Truncating audit tables before pre-load evaluation.")
            truncate_audit_tables()

        config = load_pipeline_config()

        if pipeline_name not in config:
            raise ValueError(f"Pipeline '{pipeline_name}' not found in config.")

        pipeline = config[pipeline_name]
        source_table = pipeline["source_table"]
        staging_table = pipeline.get("staging_table")
        target_table = pipeline.get("target_table")
        staging_sql_file = pipeline.get("staging_sql_file")
        target_sql_name = pipeline.get("target_sql_file")

        if not raw_only:
            insert_pipeline_run_start(
                run_id=run_id,
                pipeline_name=pipeline_name,
                start_time=start_time,
                source_table=source_table,
                target_table=target_table,
            )

        logger.info(f"[run_id={run_id}] Starting pipeline: {pipeline_name}")

        if staging_only and not staging_table:
            raise ValueError(
                f"Pipeline '{pipeline_name}' does not define a staging_table for --staging-only."
            )
        if zen_only and (not staging_table or not target_table):
            raise ValueError(
                f"Pipeline '{pipeline_name}' must define both staging_table and target_table for --zen-only."
            )

        # Run inbound CSV pre-load checks before any staging SQL executes
        if "preload_profile" in pipeline and not staging_only and not zen_only:
            logger.info(f"[run_id={run_id}] Running pre-load profile for {pipeline_name}")
            profile_started_at = datetime.now()
            profile_result = profile_csv_file(
                pipeline["preload_profile"],
                run_id=run_id,
                enforce_malformed_threshold=not profile_only,
            )
            profile_finished_at = datetime.now()
            insert_load_execution_log(
                run_id=run_id,
                pipeline_name=pipeline_name,
                table_name=source_table,
                source_file=profile_result["source_file"],
                load_method="PRELOAD_PROFILE",
                load_status="SUCCESS",
                chunk_row_count=profile_result["row_count"],
                started_at=profile_started_at,
                finished_at=profile_finished_at,
                duration_ms=int((profile_finished_at - profile_started_at).total_seconds() * 1000),
                detail_message=(
                    f"Profiled CSV with {profile_result['repaired_row_count']} repaired "
                    f"rows and {profile_result['malformed_row_count']} malformed rows."
                ),
            )
            logger.info(
                f"[run_id={run_id}] Pre-load profile complete: "
                f"rows={profile_result['row_count']}, "
                f"malformed_rows={profile_result['malformed_row_count']}, "
                f"repaired_rows={profile_result['repaired_row_count']}"
            )
            if profile_result["malformed_threshold_exceeded"]:
                logger.warning(
                    f"[run_id={run_id}] Pre-load profile exceeded malformed row threshold "
                    f"for {pipeline_name}: malformed_rows="
                    f"{profile_result['malformed_row_count']}, allowed="
                    f"{profile_result['max_malformed_rows']}"
                )
            insert_table_row_count(
                run_id=run_id,
                pipeline_name=pipeline_name,
                table_name=source_table,
                source_file=profile_result["source_file"],
                source_folder_path=profile_result["source_folder_path"],
                count_stage="PRELOAD_CSV",
                row_count=profile_result["row_count"],
                malformed_row_count=profile_result["malformed_row_count"],
                repaired_row_count=profile_result["repaired_row_count"],
                captured_at=datetime.now(),
            )
            insert_column_profile_rows(
                run_id=run_id,
                pipeline_name=pipeline_name,
                table_name=source_table,
                source_file=profile_result["source_file"],
                source_folder_path=profile_result["source_folder_path"],
                count_stage="PRELOAD_CSV",
                column_length_profile=profile_result["column_length_profile"],
                captured_at=datetime.now(),
            )
            logger.info(
                f"[run_id={run_id}] Saved pre-load CSV counts for {source_table}: "
                f"proper_rows={profile_result['row_count']}, "
                f"malformed_rows={profile_result['malformed_row_count']}, "
                f"repaired_rows={profile_result['repaired_row_count']}"
            )
            if profile_only:
                profile_status = (
                    "PROFILED_WARN"
                    if profile_result["malformed_threshold_exceeded"]
                    else "PROFILED"
                )
                update_pipeline_run_end(
                    run_id=run_id,
                    end_time=datetime.now(),
                    status=profile_status,
                    source_row_count=None,
                    target_row_count=None,
                    error_message=(
                        "Malformed row threshold exceeded during profile-only run."
                        if profile_result["malformed_threshold_exceeded"]
                        else None
                    ),
                )
                logger.info(
                    f"[run_id={run_id}] Profile-only mode complete for pipeline: "
                    f"{pipeline_name}"
                )
                if write_individual_summary:
                    _write_summary_csv(run_id)
                return run_id
            logger.info(f"[run_id={run_id}] Loading inbound CSV into {source_table}")
            raw_load_result = load_csv_to_table(
                table_name=source_table,
                profile_config=pipeline["preload_profile"],
                run_id=run_id,
                normalize_loaded_columns=not raw_only,
                allow_metadata_backfill=not raw_only,
            )
            insert_table_row_count(
                run_id=run_id,
                pipeline_name=pipeline_name,
                table_name=source_table,
                source_file=raw_load_result["source_file"],
                source_folder_path=raw_load_result["source_folder_path"],
                count_stage="RAW_LOAD",
                row_count=raw_load_result["inserted_row_count"],
                malformed_row_count=raw_load_result["malformed_row_count"],
                repaired_row_count=raw_load_result["repaired_row_count"],
                captured_at=datetime.now(),
            )

            if raw_only:
                logger.info(
                    f"[run_id={run_id}] Raw-only mode complete for pipeline: {pipeline_name}"
                )
                return run_id

        if zen_only:
            logger.info(f"[run_id={run_id}] Appending zen table for {pipeline_name}")
            inserted_row_count = append_staging_to_zen(
                staging_table=staging_table,
                zen_table=target_table,
                pipeline_name=pipeline_name,
                run_id=run_id,
            )
            logger.info(
                f"[run_id={run_id}] Appended {inserted_row_count} rows from "
                f"{staging_table} into {target_table}"
            )
            source_count = get_row_count(staging_table)
            target_count = get_row_count(target_table)
            insert_table_row_count(
                run_id=run_id,
                pipeline_name=pipeline_name,
                table_name=target_table,
                source_file=staging_table,
                count_stage="TARGET_LOAD",
                row_count=inserted_row_count,
                malformed_row_count=0,
                repaired_row_count=0,
                captured_at=datetime.now(),
            )
            update_pipeline_run_end(
                run_id=run_id,
                end_time=datetime.now(),
                status="SUCCESS",
                source_row_count=source_count,
                target_row_count=target_count,
                error_message=None,
            )
            logger.info(
                f"[run_id={run_id}] Staging-to-zen append completed successfully for pipeline: "
                f"{pipeline_name}"
            )
            if write_individual_summary:
                _write_summary_csv(run_id)
            return run_id

        if staging_table and not staging_sql_file:
            logger.info(f"[run_id={run_id}] Loading staging table for {pipeline_name}")
            load_raw_to_staging(
                source_table=source_table,
                staging_table=staging_table,
                pipeline_name=pipeline_name,
                run_id=run_id,
            )

            logger.info(f"[run_id={run_id}] Validating {source_table} -> {staging_table} row counts")
            if not validate_row_match(source_table, staging_table, run_id=run_id):
                source_count, target_count = get_row_counts(source_table, staging_table, run_id=run_id)
                raise ValueError(f"Validation failed for {pipeline_name}")

            source_count, target_count = get_row_counts(source_table, staging_table, run_id=run_id)
            insert_table_row_count(
                run_id=run_id,
                pipeline_name=pipeline_name,
                table_name=staging_table,
                source_file=source_table,
                count_stage="STAGING_LOAD",
                row_count=target_count,
                malformed_row_count=0,
                repaired_row_count=0,
                captured_at=datetime.now(),
            )

            if target_table and not staging_only:
                logger.info(f"[run_id={run_id}] Appending zen table for {pipeline_name}")
                inserted_row_count = append_staging_to_zen(
                    staging_table=staging_table,
                    zen_table=target_table,
                    pipeline_name=pipeline_name,
                    run_id=run_id,
                )
                logger.info(
                    f"[run_id={run_id}] Appended {inserted_row_count} rows from "
                    f"{staging_table} into {target_table}"
                )
                target_count = get_row_count(target_table)
                insert_table_row_count(
                    run_id=run_id,
                    pipeline_name=pipeline_name,
                    table_name=target_table,
                    source_file=staging_table,
                    count_stage="TARGET_LOAD",
                    row_count=inserted_row_count,
                    malformed_row_count=0,
                    repaired_row_count=0,
                    captured_at=datetime.now(),
                )

            update_pipeline_run_end(
                run_id=run_id,
                end_time=datetime.now(),
                status="SUCCESS",
                source_row_count=source_count,
                target_row_count=target_count,
                error_message=None,
            )
            logger.info(
                f"[run_id={run_id}] Raw-to-staging load completed successfully for pipeline: "
                f"{pipeline_name}"
            )
            if write_individual_summary:
                _write_summary_csv(run_id)
            return run_id

        if not staging_sql_file:
            source_count = get_row_count(source_table)
            insert_table_row_count(
                run_id=run_id,
                pipeline_name=pipeline_name,
                table_name=source_table,
                count_stage="RAW_LOAD",
                row_count=source_count,
                malformed_row_count=0,
                repaired_row_count=0,
                captured_at=datetime.now(),
            )
            update_pipeline_run_end(
                run_id=run_id,
                end_time=datetime.now(),
                status="SUCCESS",
                source_row_count=source_count,
                target_row_count=None,
                error_message=None,
            )
            logger.info(
                f"[run_id={run_id}] Raw load completed successfully for pipeline: {pipeline_name}"
            )
            if write_individual_summary:
                _write_summary_csv(run_id)
            return run_id

        stg_file = SQL_DIR / staging_sql_file
        target_sql_file = SQL_DIR / target_sql_name if target_sql_name else None

        logger.info(f"[run_id={run_id}] Loading staging table for {pipeline_name}")
        _run_sql_step_with_audit(
            file_path=stg_file,
            table_name=staging_table,
            source_name=source_table,
            load_method="SQL_TO_STG",
            pipeline_name=pipeline_name,
            run_id=run_id,
        )

        logger.info(f"[run_id={run_id}] Validating {source_table} -> {staging_table} row counts")
        if not validate_row_match(source_table, staging_table, run_id=run_id):
            source_count, target_count = get_row_counts(source_table, staging_table, run_id=run_id)
            raise ValueError(f"Validation failed for {pipeline_name}")

        source_count, target_count = get_row_counts(source_table, staging_table, run_id=run_id)
        insert_table_row_count(
            run_id=run_id,
            pipeline_name=pipeline_name,
            table_name=staging_table,
            source_file=source_table,
            count_stage="STAGING_LOAD",
            row_count=target_count,
            malformed_row_count=0,
            repaired_row_count=0,
            captured_at=datetime.now(),
        )

        if target_sql_file is not None:
            logger.info(f"[run_id={run_id}] Loading target table for {pipeline_name}")
            target_count = _run_sql_step_with_audit(
                file_path=target_sql_file,
                table_name=target_table,
                source_name=staging_table,
                load_method="SQL_TO_TARGET",
                pipeline_name=pipeline_name,
                run_id=run_id,
            )
            insert_table_row_count(
                run_id=run_id,
                pipeline_name=pipeline_name,
                table_name=target_table,
                source_file=staging_table,
                count_stage="TARGET_LOAD",
                row_count=target_count,
                malformed_row_count=0,
                repaired_row_count=0,
                captured_at=datetime.now(),
            )

        update_pipeline_run_end(
            run_id=run_id,
            end_time=datetime.now(),
            status="SUCCESS",
            source_row_count=source_count,
            target_row_count=target_count,
            error_message=None,
        )

        logger.info(f"[run_id={run_id}] Pipeline completed successfully: {pipeline_name}")
        if write_individual_summary:
            _write_summary_csv(run_id)
        return run_id

    except Exception as e:
        if not raw_only:
            update_pipeline_run_end(
                run_id=run_id,
                end_time=datetime.now(),
                status="FAILED",
                source_row_count=source_count,
                target_row_count=target_count,
                error_message=str(e),
            )
        logger.exception(f"[run_id={run_id}] Pipeline failed: {pipeline_name}")
        if write_individual_summary and not raw_only:
            _write_summary_csv(run_id)
        raise


def run_all_pipelines(
    profile_only: bool = False,
    raw_only: bool = False,
    staging_only: bool = False,
    zen_only: bool = False,
    max_parallel_pipelines: int = 1,
) -> None:
    config = load_pipeline_config()
    pipeline_names = list(config.keys())
    if profile_only:
        logger.info("Truncating audit tables before batch pre-load evaluation.")
        truncate_audit_tables()
    _run_pipeline_batch(
        pipeline_names=pipeline_names,
        profile_only=profile_only,
        raw_only=raw_only,
        staging_only=staging_only,
        zen_only=zen_only,
        max_parallel_pipelines=max_parallel_pipelines,
    )


def _run_pipeline_batch(
    pipeline_names: list[str],
    profile_only: bool,
    raw_only: bool,
    staging_only: bool,
    zen_only: bool,
    max_parallel_pipelines: int,
) -> None:
    total_pipelines = len(pipeline_names)
    failed_pipelines: list[str] = []
    completed_run_ids: list[str] = []
    mode_label = (
        "profile-only"
        if profile_only
        else "raw-only"
        if raw_only
        else "staging-only"
        if staging_only
        else "zen-only"
        if zen_only
        else "full-load"
    )
    batch_start_time = datetime.now()
    parallelism = _normalize_parallelism(max_parallel_pipelines, total_pipelines)

    if parallelism == 1:
        for index, pipeline_name in enumerate(pipeline_names, start=1):
            pipeline_start_time = datetime.now()
            _print_batch_progress(
                index - 1,
                total_pipelines,
                pipeline_name,
                mode_label,
                batch_start_time,
                pipeline_start_time,
            )
            logger.info(
                f"Batch progress: {index} of {total_pipelines} pipelines "
                f"({mode_label}) - {pipeline_name}"
            )
            try:
                run_id = run_pipeline(
                    pipeline_name,
                    profile_only=profile_only,
                    raw_only=raw_only,
                    staging_only=staging_only,
                    zen_only=zen_only,
                    write_individual_summary=False,
                    reset_audit_for_profile=False,
                )
                completed_run_ids.append(run_id)
                _print_batch_progress(
                    index,
                    total_pipelines,
                    pipeline_name,
                    mode_label,
                    batch_start_time,
                    pipeline_start_time,
                )
            except Exception:
                failed_pipelines.append(pipeline_name)
                _print_batch_progress(
                    index,
                    total_pipelines,
                    pipeline_name,
                    mode_label,
                    batch_start_time,
                    pipeline_start_time,
                )
                logger.exception(f"Batch pipeline failed: {pipeline_name}")
                if not profile_only:
                    raise
    else:
        logger.info(
            f"Running batch with up to {parallelism} pipelines in parallel "
            f"({mode_label})."
        )
        next_index = 0
        completed_pipelines = 0
        active_futures: dict[Future[str], tuple[str, datetime, int]] = {}

        with ThreadPoolExecutor(max_workers=parallelism) as executor:
            while next_index < len(pipeline_names) and len(active_futures) < parallelism:
                next_index = _submit_next_pipeline(
                    executor=executor,
                    pipeline_names=pipeline_names,
                    profile_only=profile_only,
                    raw_only=raw_only,
                    staging_only=staging_only,
                    zen_only=zen_only,
                    batch_start_time=batch_start_time,
                    mode_label=mode_label,
                    total_pipelines=total_pipelines,
                    next_index=next_index,
                    completed_pipelines=completed_pipelines,
                    active_futures=active_futures,
                )

            while active_futures:
                done_futures, _ = wait(active_futures.keys(), return_when=FIRST_COMPLETED)
                for future in done_futures:
                    pipeline_name, pipeline_start_time, pipeline_order = active_futures.pop(future)
                    completed_pipelines += 1
                    try:
                        run_id = future.result()
                        completed_run_ids.append(run_id)
                        _print_batch_progress(
                            completed_pipelines,
                            total_pipelines,
                            pipeline_name,
                            mode_label,
                            batch_start_time,
                            pipeline_start_time,
                        )
                    except Exception:
                        failed_pipelines.append(pipeline_name)
                        _print_batch_progress(
                            completed_pipelines,
                            total_pipelines,
                            pipeline_name,
                            mode_label,
                            batch_start_time,
                            pipeline_start_time,
                        )
                        logger.exception(
                            f"Batch pipeline failed: {pipeline_name} "
                            f"(parallel order {pipeline_order} of {total_pipelines})"
                        )
                        if not profile_only:
                            for pending_future in active_futures:
                                pending_future.cancel()
                            raise

                    while next_index < len(pipeline_names) and len(active_futures) < parallelism:
                        next_index = _submit_next_pipeline(
                            executor=executor,
                            pipeline_names=pipeline_names,
                            profile_only=profile_only,
                            raw_only=raw_only,
                            staging_only=staging_only,
                            zen_only=zen_only,
                            batch_start_time=batch_start_time,
                            mode_label=mode_label,
                            total_pipelines=total_pipelines,
                            next_index=next_index,
                            completed_pipelines=completed_pipelines,
                            active_futures=active_futures,
                        )

    if completed_run_ids and not raw_only:
        _write_batch_summary_csv(
            run_ids=completed_run_ids,
            batch_start_time=batch_start_time,
            profile_only=profile_only,
            raw_only=raw_only,
        )

    if failed_pipelines:
        raise RuntimeError(
            "Batch completed with failures for pipelines: "
            + ", ".join(failed_pipelines)
        )


def _submit_next_pipeline(
    executor: ThreadPoolExecutor,
    pipeline_names: list[str],
    profile_only: bool,
    raw_only: bool,
    staging_only: bool,
    zen_only: bool,
    batch_start_time: datetime,
    mode_label: str,
    total_pipelines: int,
    next_index: int,
    completed_pipelines: int,
    active_futures: dict[Future[str], tuple[str, datetime, int]],
) -> int:
    pipeline_name = pipeline_names[next_index]
    pipeline_order = next_index + 1
    pipeline_start_time = datetime.now()
    _print_batch_progress(
        completed_pipelines,
        total_pipelines,
        pipeline_name,
        mode_label,
        batch_start_time,
        pipeline_start_time,
    )
    logger.info(
        f"Batch progress: {pipeline_order} of {total_pipelines} pipelines "
        f"({mode_label}) - {pipeline_name}"
    )
    future = executor.submit(
        run_pipeline,
        pipeline_name,
        profile_only,
        raw_only,
        staging_only,
        zen_only,
        False,
        False,
    )
    active_futures[future] = (pipeline_name, pipeline_start_time, pipeline_order)
    return next_index + 1


def _normalize_parallelism(requested_parallelism: int, total_pipelines: int) -> int:
    if total_pipelines <= 1:
        return 1
    if requested_parallelism < 1:
        raise ValueError("max_parallel_pipelines must be at least 1.")
    return min(requested_parallelism, MAX_PARALLEL_PIPELINES_CAP, total_pipelines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one or more configured ETL pipelines."
    )
    parser.add_argument(
        "pipeline_name",
        help="Pipeline name to run, or --all to run every configured pipeline.",
    )
    parser.add_argument(
        "--profile-only",
        action="store_true",
        help="Run preload profiling and auditing without loading raw/staging/target tables.",
    )
    parser.add_argument(
        "--raw-only",
        action="store_true",
        help="Run preload profiling, load raw tables, and stop before staging/target tables.",
    )
    parser.add_argument(
        "--staging-only",
        action="store_true",
        help="Skip pre-load/raw ingestion and rebuild staging from existing raw tables.",
    )
    parser.add_argument(
        "--zen-only",
        action="store_true",
        help="Skip pre-load/raw/staging and append zen from existing staging tables.",
    )
    parser.add_argument(
        "--max-parallel-pipelines",
        type=int,
        default=1,
        help=(
            "Maximum number of pipelines to run in parallel when using --all. "
            "Default is 1. Values above 4 are capped."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        enabled_modes = [args.profile_only, args.raw_only, args.staging_only, args.zen_only]
        if sum(1 for mode in enabled_modes if mode) > 1:
            raise ValueError(
                "--profile-only, --raw-only, --staging-only, and --zen-only are mutually exclusive."
            )
        if args.pipeline_name == "--all":
            run_all_pipelines(
                profile_only=args.profile_only,
                raw_only=args.raw_only,
                staging_only=args.staging_only,
                zen_only=args.zen_only,
                max_parallel_pipelines=args.max_parallel_pipelines,
            )
        else:
            if args.max_parallel_pipelines != 1:
                raise ValueError(
                    "--max-parallel-pipelines is only supported when pipeline_name is --all."
                )
            run_pipeline(
                args.pipeline_name,
                profile_only=args.profile_only,
                raw_only=args.raw_only,
                staging_only=args.staging_only,
                zen_only=args.zen_only,
            )
    except Exception as e:
        raise SystemExit(str(e))
