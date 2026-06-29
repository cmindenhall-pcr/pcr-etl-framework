def sql_date_normalization_expression(source_expression: str, sql_type: str) -> str:
    """Cast date-like source values and null known source-system sentinel dates."""
    cast_expression = f"TRY_CAST({source_expression} AS {sql_type})"
    return (
        "CASE "
        f"WHEN TRY_CAST({source_expression} AS DATE) = CAST('19000101' AS DATE) "
        f"THEN NULL "
        f"ELSE {cast_expression} "
        "END"
    )
