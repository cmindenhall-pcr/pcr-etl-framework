import re


IDENTIFIER_NAME_TOKENS = {
    "ID",
    "CODE",
    "TYPE",
    "INVOICE",
    "ORDER",
    "BUYER",
    "VENDOR",
    "COMPANY",
    "PO",
    "ITEM",
    "LOCATION",
    "GROUP",
    "CLASS",
    "STATUS",
    "REF",
    "OBJ",
    "TAG",
    "NUMBER",
}
IDENTIFIER_NAME_FRAGMENTS = {
    "VENDOR",
    "SUPPLIER",
    "PAYVENDOR",
    "PAY_VENDOR",
}


def is_identifier_like_column(column_name: str) -> bool:
    upper_name = column_name.upper()
    tokens = _tokenize_column_name(column_name)
    return bool(tokens & IDENTIFIER_NAME_TOKENS) or any(
        fragment in upper_name for fragment in IDENTIFIER_NAME_FRAGMENTS
    )


def sql_identifier_normalization_expression(source_expression: str) -> str:
    """Strip source numeric formatting from identifier strings such as 9558.0000."""
    trimmed = f"NULLIF(LTRIM(RTRIM({source_expression})), '')"
    fractional_part = f"SUBSTRING({trimmed}, CHARINDEX('.', {trimmed}) + 1, LEN({trimmed}))"
    whole_part = f"LEFT({trimmed}, CHARINDEX('.', {trimmed}) - 1)"
    return (
        "CASE "
        f"WHEN {trimmed} LIKE '[0-9]%.%' "
        f"AND {fractional_part} NOT LIKE '%[^0]%' "
        f"THEN {whole_part} "
        f"ELSE {trimmed} "
        "END"
    )


def _tokenize_column_name(column_name: str) -> set[str]:
    camel_split = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", column_name)
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", camel_split)
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", camel_split)
    return {token.upper() for token in normalized.split("_") if token}
