from decimal import Decimal

from src.workday_supplier_payment_invoice_splitter import parse_supplier_invoice_tokens


def test_parse_supplier_invoice_tokens_extracts_amounts_in_order() -> None:
    text = (
        "11.75 - Supplier Invoice: SINV10003169  "
        "15.05 - Supplier Invoice: SINV10003156  "
        "27.5 - Supplier Invoice: SINV10003217"
    )

    assert parse_supplier_invoice_tokens(text) == [
        {
            "invoice_paid_ordinal": 1,
            "invoice_paid_amount": Decimal("11.75"),
            "invoice_number": "SINV10003169",
        },
        {
            "invoice_paid_ordinal": 2,
            "invoice_paid_amount": Decimal("15.05"),
            "invoice_number": "SINV10003156",
        },
        {
            "invoice_paid_ordinal": 3,
            "invoice_paid_amount": Decimal("27.5"),
            "invoice_number": "SINV10003217",
        },
    ]


def test_parse_supplier_invoice_tokens_handles_negative_amounts() -> None:
    text = "-1.21 - Supplier Invoice: SINV10013047  (2.50) - Supplier Invoice: SINV10013048"

    assert parse_supplier_invoice_tokens(text) == [
        {
            "invoice_paid_ordinal": 1,
            "invoice_paid_amount": Decimal("-1.21"),
            "invoice_number": "SINV10013047",
        },
        {
            "invoice_paid_ordinal": 2,
            "invoice_paid_amount": Decimal("-2.50"),
            "invoice_number": "SINV10013048",
        },
    ]
