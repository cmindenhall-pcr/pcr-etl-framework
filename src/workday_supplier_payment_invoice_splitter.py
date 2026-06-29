import re
from decimal import Decimal, InvalidOperation

from src.db_connection import get_connection
from src.logger import get_logger
from src.numeric_normalization import normalize_numeric_candidate

logger = get_logger()

SOURCE_TABLE = "zen.SupplierPaymentDetails"
BRIDGE_TABLE = "zen.zenSupplierPaymentDetailInvoices_Bridge"
FLATTENED_TABLE = "zen.zenSupplierPaymentDetailInvoices"
PARSE_PATTERN_NAME = "workday_supplier_invoice_amount_v1"
INVOICE_PATTERN = re.compile(
    r"(?P<amount>\(?-?\$?\d[\d,]*(?:\.\d+)?\)?)\s+-\s+Supplier Invoice:\s+(?P<invoice>[A-Za-z0-9_-]+)"
)


def parse_supplier_invoice_tokens(invoices_paid_text: str | None) -> list[dict[str, object]]:
    if not invoices_paid_text:
        return []

    parsed_rows = []
    for ordinal, match in enumerate(INVOICE_PATTERN.finditer(invoices_paid_text), start=1):
        parsed_rows.append(
            {
                "invoice_paid_ordinal": ordinal,
                "invoice_paid_amount": _parse_money(match.group("amount")),
                "invoice_number": match.group("invoice"),
            }
        )

    return parsed_rows


def rebuild_supplier_payment_detail_invoices(batch_size: int = 1000) -> int:
    conn = get_connection()
    try:
        cursor = conn.cursor()
        _ensure_source_table_exists(cursor)
        _recreate_target_table(cursor)
        conn.commit()

        cursor.execute(
            """
            SELECT
                AutoId,
                Transaction_Number,
                Invoices_Paid,
                LoadDate,
                SFileName
            FROM zen.SupplierPaymentDetails
            WHERE NULLIF(LTRIM(RTRIM(Invoices_Paid)), '') IS NOT NULL
            ORDER BY AutoId
            """
        )
        source_rows = cursor.fetchall()

        insert_rows = []
        inserted_count = 0
        for source_row in source_rows:
            payment_detail_auto_id = source_row[0]
            transaction_number = source_row[1]
            source_text = source_row[2]
            load_date = source_row[3]
            source_file_name = source_row[4]
            parsed_tokens = parse_supplier_invoice_tokens(source_text)

            for parsed_token in parsed_tokens:
                insert_rows.append(
                    (
                        payment_detail_auto_id,
                        transaction_number,
                        parsed_token["invoice_number"],
                        _money_parameter(parsed_token["invoice_paid_amount"]),
                        parsed_token["invoice_paid_ordinal"],
                        len(source_text) if source_text is not None else None,
                        PARSE_PATTERN_NAME,
                        load_date,
                        source_file_name,
                    )
                )

            if len(insert_rows) >= batch_size:
                inserted_count += _insert_child_rows(cursor, insert_rows)
                conn.commit()
                insert_rows = []

        if insert_rows:
            inserted_count += _insert_child_rows(cursor, insert_rows)
            conn.commit()

        flattened_count = _rebuild_flattened_table(cursor)
        conn.commit()

        logger.info("Rebuilt %s with %s rows.", BRIDGE_TABLE, inserted_count)
        logger.info("Rebuilt %s with %s rows.", FLATTENED_TABLE, flattened_count)
        return inserted_count
    except Exception:
        conn.rollback()
        logger.exception("Failed rebuilding %s from %s.", BRIDGE_TABLE, SOURCE_TABLE)
        raise
    finally:
        conn.close()


def _parse_money(value: str) -> Decimal | None:
    normalized_value = normalize_numeric_candidate(value)
    if normalized_value is None:
        return None

    try:
        return Decimal(normalized_value)
    except InvalidOperation:
        return None


def _money_parameter(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _ensure_source_table_exists(cursor) -> None:
    cursor.execute(
        """
        IF OBJECT_ID('zen.SupplierPaymentDetails', 'U') IS NULL
        BEGIN
            THROW 51000, 'Missing source table zen.SupplierPaymentDetails.', 1;
        END
        """
    )


def _recreate_target_table(cursor) -> None:
    cursor.execute(
        """
        IF OBJECT_ID('zen.zenSupplierPaymentDetailInvoices', 'U') IS NOT NULL
            DROP TABLE zen.zenSupplierPaymentDetailInvoices;

        IF OBJECT_ID('zen.zenSupplierPaymentDetailInvoices_Bridge', 'U') IS NOT NULL
            DROP TABLE zen.zenSupplierPaymentDetailInvoices_Bridge;

        IF OBJECT_ID('zen.SupplierPaymentDetailInvoices', 'U') IS NOT NULL
            DROP TABLE zen.SupplierPaymentDetailInvoices;

        CREATE TABLE zen.zenSupplierPaymentDetailInvoices_Bridge (
            PaymentDetailAutoId INT NOT NULL,
            Transaction_Number VARCHAR(100) NOT NULL,
            Invoice_Number VARCHAR(100) NOT NULL,
            Invoice_Paid_Amount MONEY NULL,
            Invoice_Paid_Ordinal INT NOT NULL,
            Source_Invoices_Paid_Length INT NULL,
            ParsePattern VARCHAR(100) NOT NULL,
            LoadDate DATETIME NULL,
            SFileName VARCHAR(4000) NULL,
            AutoId INT IDENTITY(1,1) NOT NULL PRIMARY KEY
        );
        """
    )


def _insert_child_rows(cursor, rows: list[tuple[object, ...]]) -> int:
    cursor.fast_executemany = True
    cursor.executemany(
        """
        INSERT INTO zen.zenSupplierPaymentDetailInvoices_Bridge (
            PaymentDetailAutoId,
            Transaction_Number,
            Invoice_Number,
            Invoice_Paid_Amount,
            Invoice_Paid_Ordinal,
            Source_Invoices_Paid_Length,
            ParsePattern,
            LoadDate,
            SFileName
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    return len(rows)


def _rebuild_flattened_table(cursor) -> int:
    cursor.execute(
        """
        IF OBJECT_ID('zen.zenSupplierPaymentDetailInvoices', 'U') IS NOT NULL
            DROP TABLE zen.zenSupplierPaymentDetailInvoices;

        SELECT
            A.Invoice_Number,
            A.Invoice_Paid_Amount,
            B.Payment_Date,
            B.Payment_Status,
            B.Transaction_Number,
            B.Supplier,
            B.Supplier_ID,
            B.Payment_Type,
            B.Payment_Amount,
            B.Currency,
            B.LoadDate,
            B.SFileName,
            B.AutoId
        INTO zen.zenSupplierPaymentDetailInvoices
        FROM zen.zenSupplierPaymentDetailInvoices_Bridge A
        JOIN zen.SupplierPaymentDetails B
          ON A.PaymentDetailAutoId = B.AutoId;
        """
    )
    cursor.execute("SELECT COUNT(*) FROM zen.zenSupplierPaymentDetailInvoices")
    row = cursor.fetchone()
    return int(row[0]) if row else 0


if __name__ == "__main__":
    rebuild_supplier_payment_detail_invoices()
