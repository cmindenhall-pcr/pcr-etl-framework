from src.audit_logger import _recommend_sql_type


def test_recommend_sql_type_uses_decimal_for_fractional_quantities() -> None:
    assert (
        _recommend_sql_type(
            column_name="DISTR_QTY",
            minimum_non_null_value="-.0800",
            maximum_non_null_value="9992.0000",
            max_string_length=10,
            min_max_mismatch_flag=0,
        )
        == "DECIMAL(17, 4)"
    )


def test_recommend_sql_type_keeps_integer_quantities_as_int() -> None:
    assert (
        _recommend_sql_type(
            column_name="QTY",
            minimum_non_null_value="0",
            maximum_non_null_value="9992",
            max_string_length=4,
            min_max_mismatch_flag=0,
        )
        == "INT"
    )


def test_recommend_sql_type_accepts_fractional_second_timestamps_as_dates() -> None:
    assert (
        _recommend_sql_type(
            column_name="FLX_CREATE_DATE",
            minimum_non_null_value="2020-01-01 01:30:44.590000000",
            maximum_non_null_value="2024-05-02 14:50:56.040000000",
            max_string_length=29,
            min_max_mismatch_flag=0,
        )
        == "DATE"
    )


def test_recommend_sql_type_accepts_peoplesoft_mon_dates_as_dates() -> None:
    assert (
        _recommend_sql_type(
            column_name="CASH_CLEARED_DT",
            minimum_non_null_value="01-APR-22",
            maximum_non_null_value="31-OCT-24",
            max_string_length=9,
            min_max_mismatch_flag=0,
        )
        == "DATE"
    )


def test_recommend_sql_type_lets_date_values_override_identifier_name() -> None:
    assert (
        _recommend_sql_type(
            column_name="COMMENT_ID",
            minimum_non_null_value="01-APR-22 01.04.17.000000000 PM",
            maximum_non_null_value="31-OCT-23 12.58.20.000551000 PM",
            max_string_length=35,
            min_max_mismatch_flag=0,
        )
        == "DATE"
    )
    assert (
        _recommend_sql_type(
            column_name="LAST_UPDATE_DTTM",
            minimum_non_null_value="01-APR-22 01.31.06.924300000 PM",
            maximum_non_null_value="31-OCT-24 11.08.28.886188000 AM",
            max_string_length=35,
            min_max_mismatch_flag=0,
        )
        == "DATE"
    )


def test_recommend_sql_type_keeps_paid_vendor_decimal_artifacts_as_identifier() -> None:
    assert (
        _recommend_sql_type(
            column_name="PaidVendor",
            minimum_non_null_value="5701.000000000000000",
            maximum_non_null_value="5858.000000000000000",
            max_string_length=20,
            min_max_mismatch_flag=0,
        )
        == "VARCHAR(100)"
    )


def test_recommend_sql_type_treats_number_of_decimals_as_integer_count() -> None:
    assert (
        _recommend_sql_type(
            column_name="BaseCurrencyNumberOfDecimals",
            minimum_non_null_value="2.000000000000000",
            maximum_non_null_value="2.000000000000000",
            max_string_length=17,
            min_max_mismatch_flag=0,
        )
        == "INT"
    )


def test_recommend_sql_type_keeps_amount_decimal_artifacts_as_money() -> None:
    assert (
        _recommend_sql_type(
            column_name="BasePaymentAmount",
            minimum_non_null_value="0.000000000000000",
            maximum_non_null_value="0.000000000000000",
            max_string_length=17,
            min_max_mismatch_flag=0,
        )
        == "MONEY"
    )


def test_recommend_sql_type_treats_line_and_sequence_decimal_artifacts_as_int() -> None:
    assert (
        _recommend_sql_type(
            column_name="NumberOfLines",
            minimum_non_null_value="0.000000000000000",
            maximum_non_null_value="98.000000000000000",
            max_string_length=18,
            min_max_mismatch_flag=0,
        )
        == "INT"
    )
    assert (
        _recommend_sql_type(
            column_name="CancelSequence",
            minimum_non_null_value="0.000000000000000",
            maximum_non_null_value="9999.000000000000000",
            max_string_length=20,
            min_max_mismatch_flag=0,
        )
        == "INT"
    )
    assert (
        _recommend_sql_type(
            column_name="PaymentSequence",
            minimum_non_null_value="1.000000000000000",
            maximum_non_null_value="2.000000000000000",
            max_string_length=17,
            min_max_mismatch_flag=0,
        )
        == "INT"
    )


def test_recommend_sql_type_applies_exact_name_corrections() -> None:
    assert (
        _recommend_sql_type(
            column_name="CashLedgerTransaction",
            minimum_non_null_value="0.000000000000000",
            maximum_non_null_value="9999.000000000000000",
            max_string_length=20,
            min_max_mismatch_flag=0,
        )
        == "INT"
    )
    assert (
        _recommend_sql_type(
            column_name="OriginalPurchaseOrder",
            minimum_non_null_value="0.000000000000000",
            maximum_non_null_value="48366.000000000000000",
            max_string_length=21,
            min_max_mismatch_flag=0,
        )
        == "VARCHAR(100)"
    )
    assert (
        _recommend_sql_type(
            column_name="CreditReceived",
            minimum_non_null_value="0.000000000000000",
            maximum_non_null_value="998.460000000000000",
            max_string_length=21,
            min_max_mismatch_flag=0,
        )
        == "MONEY"
    )
    assert (
        _recommend_sql_type(
            column_name="ReturnValue",
            minimum_non_null_value="-70.000000000000000",
            maximum_non_null_value="9984.600000000000000",
            max_string_length=22,
            min_max_mismatch_flag=0,
        )
        == "MONEY"
    )
