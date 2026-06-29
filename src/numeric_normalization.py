import re


_NUMERIC_PATTERN = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)")


def normalize_numeric_candidate(value: str | None) -> str | None:
    """Return a castable numeric token for accounting/currency-like values."""
    if value is None:
        return None

    normalized = (
        value.strip()
        .replace("\u00a0", "")
        .replace("\t", "")
        .replace(" ", "")
        .replace(",", "")
        .replace("$", "")
    )
    if normalized == "":
        return None

    if normalized.startswith("(") and normalized.endswith(")") and len(normalized) > 2:
        normalized = f"-{normalized[1:-1]}"

    if _NUMERIC_PATTERN.fullmatch(normalized):
        return normalized

    return None


def sql_numeric_normalization_expression(source_expression: str) -> str:
    """Build a SQL expression that normalizes numeric formatting only for typed numeric casts."""
    stripped = (
        f"REPLACE(REPLACE(REPLACE(REPLACE(REPLACE({source_expression}, "
        "CHAR(160), ''), CHAR(9), ''), ' ', ''), ',', ''), '$', '')"
    )
    return (
        "CASE "
        f"WHEN {stripped} LIKE '(%)' "
        f"THEN '-' + SUBSTRING({stripped}, 2, LEN({stripped}) - 2) "
        f"ELSE {stripped} "
        "END"
    )
