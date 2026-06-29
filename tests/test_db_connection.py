import sys
from types import SimpleNamespace

import pytest

from src.db_connection import _is_transient_connection_error, get_connection


class FakePyodbcError(Exception):
    pass


def test_is_transient_connection_error_detects_startup_issue() -> None:
    exc = FakePyodbcError("08001 Client unable to establish connection")
    assert _is_transient_connection_error(exc) is True


def test_get_connection_retries_transient_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"count": 0}
    sentinel = object()

    def fake_connect(conn_str: str, autocommit: bool = False):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise FakePyodbcError("08001 Client unable to establish connection")
        assert "Connection Timeout=5;" in conn_str
        assert autocommit is True
        return sentinel

    fake_pyodbc = SimpleNamespace(Error=FakePyodbcError, connect=fake_connect)
    monkeypatch.setitem(sys.modules, "pyodbc", fake_pyodbc)
    monkeypatch.setenv("SQL_SERVER", "localhost")
    monkeypatch.setenv("SQL_DATABASE", "ClientDatabase")
    monkeypatch.setenv("SQL_USERNAME", "sa")
    monkeypatch.setenv("SQL_PASSWORD", "placeholder_password")
    monkeypatch.setenv("SQL_CONNECT_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("SQL_CONNECT_RETRY_DELAY_SECONDS", "0")
    monkeypatch.setattr("src.db_connection.time.sleep", lambda _: None)

    assert get_connection(autocommit=True) is sentinel
    assert attempts["count"] == 3


def test_get_connection_does_not_retry_non_transient_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = {"count": 0}

    def fake_connect(conn_str: str, autocommit: bool = False):
        attempts["count"] += 1
        raise FakePyodbcError("28000 Login failed for user")

    fake_pyodbc = SimpleNamespace(Error=FakePyodbcError, connect=fake_connect)
    monkeypatch.setitem(sys.modules, "pyodbc", fake_pyodbc)
    monkeypatch.setenv("SQL_SERVER", "localhost")
    monkeypatch.setenv("SQL_DATABASE", "ClientDatabase")
    monkeypatch.setenv("SQL_USERNAME", "sa")
    monkeypatch.setenv("SQL_PASSWORD", "placeholder_password")
    monkeypatch.setenv("SQL_CONNECT_RETRY_ATTEMPTS", "4")
    monkeypatch.setenv("SQL_CONNECT_RETRY_DELAY_SECONDS", "0")

    with pytest.raises(FakePyodbcError):
        get_connection()

    assert attempts["count"] == 1
