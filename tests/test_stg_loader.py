from __future__ import annotations

from types import SimpleNamespace

from src import stg_loader


class FakeCursor:
    def __init__(self, profile_rows, raw_rows, row_count: int = 0) -> None:
        self.profile_rows = profile_rows
        self.raw_rows = raw_rows
        self.row_count = row_count
        self.executed_sql: list[str] = []
        self.executed_params: list[tuple[object, ...]] = []
        self._fetchall_result = []
        self._fetchone_result = None

    def execute(self, sql: str, *params) -> None:
        self.executed_sql.append(sql)
        self.executed_params.append(params)

        if "FROM sys.schemas" in sql:
            self._fetchone_result = (1,)
            self._fetchall_result = []
            return

        if "FROM audit.ColumnProfileLog c" in sql:
            self._fetchall_result = self.profile_rows
            self._fetchone_result = None
            return

        if "FROM INFORMATION_SCHEMA.COLUMNS" in sql:
            self._fetchall_result = self.raw_rows
            self._fetchone_result = None
            return

        if "SELECT COUNT(*) FROM stg.ClaimReport140" in sql:
            self._fetchone_result = (self.row_count,)
            self._fetchall_result = []
            return

        self._fetchone_result = None
        self._fetchall_result = []

    def fetchall(self):
        return self._fetchall_result

    def fetchone(self):
        return self._fetchone_result


class FakeConnection:
    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor
        self.commit_calls = 0
        self.rollback_calls = 0
        self.closed = False

    def cursor(self) -> FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1

    def close(self) -> None:
        self.closed = True


def test_load_raw_to_staging_prefers_audited_profile_types(
    monkeypatch,
) -> None:
    profile_rows = [
        ("strClient", "VARCHAR(4000)", "NOT NULL"),
        ("PO-NUMBER", "VARCHAR(4000)", "NOT NULL"),
        ("dClaimAmount", "MONEY", "NOT NULL"),
        ("dtClaimInvoiceDate", "DATE", "NULL"),
    ]
    raw_rows = [
        ("strClient", "varchar", 4000, None, None, "YES"),
        ("PO-NUMBER", "varchar", 4000, None, None, "YES"),
        ("dClaimAmount", "varchar", 4000, None, None, "YES"),
        ("dtClaimInvoiceDate", "varchar", 4000, None, None, "YES"),
        ("LoadDate", "datetime", None, None, None, "NO"),
        ("SFileName", "varchar", 255, None, None, "NO"),
    ]
    cursor = FakeCursor(profile_rows=profile_rows, raw_rows=raw_rows, row_count=27)
    connection = FakeConnection(cursor)

    monkeypatch.setattr(stg_loader, "get_connection", lambda: connection)
    monkeypatch.setattr(stg_loader, "insert_load_execution_log", lambda **_: None)

    stg_loader.load_raw_to_staging(
        source_table="raw.ClaimReport140",
        staging_table="stg.ClaimReport140",
        pipeline_name="ClaimReport140",
        run_id="test1234",
    )

    combined_sql = "\n".join(cursor.executed_sql)
    assert "CREATE TABLE stg.ClaimReport140" in combined_sql
    assert "[PO_NUMBER] VARCHAR(4000) NOT NULL" in combined_sql
    assert "[PO-NUMBER] VARCHAR(4000) NOT NULL" not in combined_sql
    assert "[dClaimAmount] MONEY NOT NULL" in combined_sql
    assert "[dtClaimInvoiceDate] DATE NULL" in combined_sql
    assert "[LoadDate] DATETIME NOT NULL" in combined_sql
    assert "[SFileName] VARCHAR(255) NOT NULL" in combined_sql
    assert "[AutoId] INT IDENTITY(1,1) NOT NULL" in combined_sql
    assert "WHEN NULLIF(LTRIM(RTRIM([PO-NUMBER])), '') LIKE '[0-9]%.%'" in combined_sql
    assert "TRY_CAST(CASE WHEN REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(NULLIF(LTRIM(RTRIM([dClaimAmount])), '')" in combined_sql
    assert "), ',', ''), '$', '') LIKE '(%)' THEN '-'" in combined_sql
    assert "WHEN TRY_CAST(NULLIF(LTRIM(RTRIM([dtClaimInvoiceDate])), '') AS DATE) = CAST('19000101' AS DATE) THEN NULL" in combined_sql
    assert "ELSE TRY_CAST(NULLIF(LTRIM(RTRIM([dtClaimInvoiceDate])), '') AS DATE)" in combined_sql
    assert "[LoadDate] AS [LoadDate]" in combined_sql
    assert "[SFileName] AS [SFileName]" in combined_sql
    assert "INSERT INTO stg.ClaimReport140 ([strClient], [PO_NUMBER], [dClaimAmount], [dtClaimInvoiceDate], [LoadDate], [SFileName])" in combined_sql
    assert "INSERT INTO stg.ClaimReport140 ([strClient], [PO_NUMBER], [dClaimAmount], [dtClaimInvoiceDate], [LoadDate], [SFileName], [AutoId])" not in combined_sql
    assert "INSERT INTO stg.ClaimReport140" in combined_sql
    assert "SELECT INTO stg.ClaimReport140" not in combined_sql


def test_load_raw_to_staging_falls_back_to_raw_schema_when_profile_missing(
    monkeypatch,
) -> None:
    cursor = FakeCursor(
        profile_rows=[],
        raw_rows=[
            ("strClient", "varchar", 4000, None, None, "YES"),
            ("LoadDate", "datetime", None, None, None, "NO"),
        ],
        row_count=27,
    )
    connection = FakeConnection(cursor)

    monkeypatch.setattr(stg_loader, "get_connection", lambda: connection)
    monkeypatch.setattr(stg_loader, "insert_load_execution_log", lambda **_: None)

    stg_loader.load_raw_to_staging(
        source_table="raw.ClaimReport140",
        staging_table="stg.ClaimReport140",
    )

    combined_sql = "\n".join(cursor.executed_sql)
    assert "[strClient] VARCHAR(4000) NOT NULL" in combined_sql
    assert "[LoadDate] DATETIME NOT NULL" in combined_sql
    assert "[AutoId] INT IDENTITY(1,1) NOT NULL" in combined_sql
    assert "CAST(ISNULL(NULLIF(LTRIM(RTRIM([strClient])), ''), '') AS VARCHAR(4000))" in combined_sql
    assert "AS [LoadDate]" in combined_sql


def test_load_raw_to_staging_rejects_duplicate_normalized_names(
    monkeypatch,
) -> None:
    cursor = FakeCursor(
        profile_rows=[
            ("PO-NUMBER", "VARCHAR(4000)", "NOT NULL"),
            ("PO_NUMBER", "VARCHAR(4000)", "NOT NULL"),
        ],
        raw_rows=[],
        row_count=0,
    )
    connection = FakeConnection(cursor)

    monkeypatch.setattr(stg_loader, "get_connection", lambda: connection)
    monkeypatch.setattr(stg_loader, "insert_load_execution_log", lambda **_: None)

    try:
        stg_loader.load_raw_to_staging(
            source_table="raw.ClaimReport140",
            staging_table="stg.ClaimReport140",
        )
    except RuntimeError as exc:
        assert "both normalize to staging column 'PO_NUMBER'" in str(exc)
    else:
        raise AssertionError("Expected duplicate normalized staging names to fail.")


def test_load_raw_to_staging_normalizes_spaces_and_removes_dots(
    monkeypatch,
) -> None:
    cursor = FakeCursor(
        profile_rows=[
            ("Pur. Doc.", "VARCHAR(4000)", "NOT NULL"),
            ("Doc Date", "DATE", "NULL"),
        ],
        raw_rows=[],
        row_count=0,
    )
    connection = FakeConnection(cursor)

    monkeypatch.setattr(stg_loader, "get_connection", lambda: connection)
    monkeypatch.setattr(stg_loader, "insert_load_execution_log", lambda **_: None)

    stg_loader.load_raw_to_staging(
        source_table="raw.EKKO",
        staging_table="stg.EKKO",
    )

    combined_sql = "\n".join(cursor.executed_sql)
    assert "[Pur_Doc] VARCHAR(4000) NOT NULL" in combined_sql
    assert "[Doc_Date] DATE NULL" in combined_sql
    assert "AS [Pur_Doc]" in combined_sql
    assert "AS [Doc_Date]" in combined_sql


def test_load_raw_to_staging_rejects_reserved_autoid_source_name(
    monkeypatch,
) -> None:
    cursor = FakeCursor(
        profile_rows=[
            ("AutoId", "VARCHAR(4000)", "NOT NULL"),
        ],
        raw_rows=[],
        row_count=0,
    )
    connection = FakeConnection(cursor)

    monkeypatch.setattr(stg_loader, "get_connection", lambda: connection)
    monkeypatch.setattr(stg_loader, "insert_load_execution_log", lambda **_: None)

    try:
        stg_loader.load_raw_to_staging(
            source_table="raw.ClaimReport140",
            staging_table="stg.ClaimReport140",
        )
    except RuntimeError as exc:
        assert "conflicts with reserved staging identity column 'AutoId'" in str(exc)
    else:
        raise AssertionError("Expected reserved AutoId source name to fail.")


def test_load_raw_to_staging_strips_zero_decimal_identifier_formatting(
    monkeypatch,
) -> None:
    cursor = FakeCursor(
        profile_rows=[
            ("Vendor", "VARCHAR(100)", "NOT NULL"),
            ("PayVendor", "VARCHAR(100)", "NOT NULL"),
            ("BaseCurrencyNumberOfDecimals", "VARCHAR(100)", "NOT NULL"),
            ("TransactionIDNumber", "VARCHAR(100)", "NOT NULL"),
            ("PurchaseOrder", "VARCHAR(100)", "NOT NULL"),
            ("Buyer", "VARCHAR(100)", "NOT NULL"),
            ("Description", "VARCHAR(4000)", "NOT NULL"),
        ],
        raw_rows=[],
        row_count=0,
    )
    connection = FakeConnection(cursor)

    monkeypatch.setattr(stg_loader, "get_connection", lambda: connection)
    monkeypatch.setattr(stg_loader, "insert_load_execution_log", lambda **_: None)

    stg_loader.load_raw_to_staging(
        source_table="raw.Vendor",
        staging_table="stg.Vendor",
    )

    combined_sql = "\n".join(cursor.executed_sql)
    assert "WHEN NULLIF(LTRIM(RTRIM([Vendor])), '') LIKE '[0-9]%.%'" in combined_sql
    assert "WHEN NULLIF(LTRIM(RTRIM([PayVendor])), '') LIKE '[0-9]%.%'" in combined_sql
    assert "WHEN NULLIF(LTRIM(RTRIM([BaseCurrencyNumberOfDecimals])), '') LIKE '[0-9]%.%'" in combined_sql
    assert "WHEN NULLIF(LTRIM(RTRIM([TransactionIDNumber])), '') LIKE '[0-9]%.%'" in combined_sql
    assert "WHEN NULLIF(LTRIM(RTRIM([PurchaseOrder])), '') LIKE '[0-9]%.%'" in combined_sql
    assert "WHEN NULLIF(LTRIM(RTRIM([Buyer])), '') LIKE '[0-9]%.%'" in combined_sql
    assert "SUBSTRING(NULLIF(LTRIM(RTRIM([Vendor])), ''), CHARINDEX('.', NULLIF(LTRIM(RTRIM([Vendor])), '')) + 1" in combined_sql
    assert "THEN LEFT(NULLIF(LTRIM(RTRIM([Vendor])), ''), CHARINDEX('.', NULLIF(LTRIM(RTRIM([Vendor])), '')) - 1)" in combined_sql
    assert "CAST(ISNULL(NULLIF(LTRIM(RTRIM([Description])), ''), '') AS VARCHAR(4000))" in combined_sql


def test_load_raw_to_staging_preserves_raw_varchar_max_when_profile_is_narrower(
    monkeypatch,
) -> None:
    cursor = FakeCursor(
        profile_rows=[
            ("Invoices_Paid", "VARCHAR(4000)", "NOT NULL"),
        ],
        raw_rows=[
            ("Invoices_Paid", "varchar", -1, None, None, "YES"),
        ],
        row_count=0,
    )
    connection = FakeConnection(cursor)

    monkeypatch.setattr(stg_loader, "get_connection", lambda: connection)
    monkeypatch.setattr(stg_loader, "insert_load_execution_log", lambda **_: None)

    stg_loader.load_raw_to_staging(
        source_table="raw.SupplierPaymentDetails",
        staging_table="stg.SupplierPaymentDetails",
    )

    combined_sql = "\n".join(cursor.executed_sql)
    assert "[Invoices_Paid] VARCHAR(MAX) NOT NULL" in combined_sql
    assert "CAST(ISNULL(NULLIF(LTRIM(RTRIM([Invoices_Paid])), ''), '') AS VARCHAR(MAX))" in combined_sql
