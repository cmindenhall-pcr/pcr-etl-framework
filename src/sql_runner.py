from pathlib import Path

from src.db_connection import get_connection
from src.logger import get_logger

logger = get_logger()


def run_sql_file(file_path: str | Path, autocommit: bool = True, run_id: str | None = None) -> None:
    """
    Execute a .sql file against the configured SQL Server database.
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"SQL file not found: {file_path}")

    sql_text = file_path.read_text(encoding="utf-8").strip()

    if not sql_text:
        raise ValueError(f"SQL file is empty: {file_path}")

    prefix = f"[run_id={run_id}] " if run_id else ""
    logger.info(f"{prefix}Running SQL file: {file_path}")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql_text)

        if autocommit:
            conn.commit()

        logger.info(f"{prefix}Successfully executed SQL file: {file_path}")

    except Exception as e:
        conn.rollback()
        logger.exception(f"{prefix}Failed executing SQL file: {file_path}")
        raise RuntimeError(f"Error executing SQL file {file_path}: {e}") from e

    finally:
        conn.close()


def run_sql_text(sql_text: str, autocommit: bool = True, run_id: str | None = None) -> None:
    """
    Execute a raw SQL string against the configured SQL Server database.
    """
    if not sql_text or not sql_text.strip():
        raise ValueError("SQL text is empty.")

    prefix = f"[run_id={run_id}] " if run_id else ""
    logger.info(f"{prefix}Running SQL text block")

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql_text)

        if autocommit:
            conn.commit()

        logger.info(f"{prefix}Successfully executed SQL text block")

    except Exception as e:
        conn.rollback()
        logger.exception(f"{prefix}Failed executing SQL text block")
        raise RuntimeError(f"Error executing SQL text: {e}") from e

    finally:
        conn.close()
