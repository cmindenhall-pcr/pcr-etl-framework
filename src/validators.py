from src.db_connection import get_connection
from src.logger import get_logger

logger = get_logger()


def get_row_count(table_name: str) -> int:
    """
    Return row count for a given table.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        result = cursor.fetchone()
        return result[0]
    finally:
        conn.close()


def get_row_counts(source_table: str, target_table: str, run_id: str | None = None) -> tuple[int, int]:
    """
    Return source and target row counts and log them.
    """
    source_count = get_row_count(source_table)
    target_count = get_row_count(target_table)

    prefix = f"[run_id={run_id}] " if run_id else ""
    logger.info(f"{prefix}Source ({source_table}): {source_count}")
    logger.info(f"{prefix}Target ({target_table}): {target_count}")

    return source_count, target_count


def validate_row_match(source_table: str, target_table: str, run_id: str | None = None) -> bool:
    """
    Compare row counts between source and target tables.
    """
    source_count, target_count = get_row_counts(source_table, target_table, run_id=run_id)
    return source_count == target_count
