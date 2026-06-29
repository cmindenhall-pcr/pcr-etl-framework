import os
import time
from dotenv import load_dotenv

from src.logger import get_logger

logger = get_logger()


def get_connection_settings() -> dict[str, str | None]:
    load_dotenv(dotenv_path=".env", override=False)
    return {
        "driver": os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server"),
        "server": os.getenv("SQL_SERVER"),
        "port": os.getenv("SQL_PORT"),
        "database": os.getenv("SQL_DATABASE"),
        "username": os.getenv("SQL_USERNAME"),
        "password": os.getenv("SQL_PASSWORD"),
    }


def get_connection(autocommit: bool = False):
    try:
        import pyodbc
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            "Database dependency missing: install 'pyodbc' in the active Python "
            "environment before running pipeline commands that write to or audit in SQL Server."
        ) from e

    settings = get_connection_settings()
    driver = settings["driver"]
    server = settings["server"]
    port = settings["port"]
    database = settings["database"]
    username = settings["username"]
    password = settings["password"]
    retry_attempts = int(os.getenv("SQL_CONNECT_RETRY_ATTEMPTS", "60"))
    retry_delay_seconds = float(os.getenv("SQL_CONNECT_RETRY_DELAY_SECONDS", "3"))
    retry_started = None
    server_endpoint = f"{server},{port}" if server and port else server

    # ✅ Use SQL auth if username/password provided, else Windows auth
    if username and password:
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={server_endpoint};"
            f"DATABASE={database};"
            f"UID={username};"
            f"PWD={password};"
            "Encrypt=no;"
            "TrustServerCertificate=yes;"
            "Connection Timeout=5;"
        )
    else:
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={server_endpoint};"
            f"DATABASE={database};"
            "Trusted_Connection=yes;"
            "Encrypt=no;"
            "TrustServerCertificate=yes;"
            "Connection Timeout=5;"
        )

    for attempt in range(1, retry_attempts + 1):
        try:
            connection = pyodbc.connect(conn_str, autocommit=autocommit)
            if retry_started is not None:
                logger.info(
                    "SQL Server became ready after %.1f seconds; continuing.",
                    time.monotonic() - retry_started,
                )
            return connection
        except pyodbc.Error as exc:
            if not _is_transient_connection_error(exc) or attempt == retry_attempts:
                raise

            if retry_started is None:
                retry_started = time.monotonic()
                logger.info(
                    "SQL Server is not ready yet; waiting up to %.0f seconds for startup.",
                    retry_attempts * retry_delay_seconds,
                )
            time.sleep(retry_delay_seconds)

    raise RuntimeError("SQL Server connection retry loop exited unexpectedly.")


def _is_transient_connection_error(exc: Exception) -> bool:
    message = str(exc).lower()
    transient_markers = (
        "08001",
        "client unable to establish connection",
        "server is not found or not accessible",
        "login timeout expired",
        "ssl provider",
        "encryption not supported on the client",
    )
    return any(marker in message for marker in transient_markers)
