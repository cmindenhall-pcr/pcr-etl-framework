from src.zen_loader import _exclude_layer_identity_columns


def test_exclude_layer_identity_columns_drops_staging_autoid() -> None:
    schema_rows = [
        {"column_name": "Company", "sql_type": "VARCHAR(100)", "nullability": "NOT NULL"},
        {"column_name": "AutoId", "sql_type": "INT", "nullability": "NOT NULL"},
    ]

    assert _exclude_layer_identity_columns(schema_rows) == [
        {"column_name": "Company", "sql_type": "VARCHAR(100)", "nullability": "NOT NULL"},
    ]
