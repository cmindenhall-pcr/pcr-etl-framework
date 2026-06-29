def repair_csv_row(
    row: list[str], expected_column_count: int, repair_strategy: str | None = None
) -> list[str] | None:
    if len(row) == expected_column_count:
        return row
    return None


def repair_csv_row_with_next(
    row: list[str],
    expected_column_count: int,
    repair_strategy: str | None = None,
    next_row: list[str] | None = None,
) -> tuple[list[str] | None, bool]:
    if len(row) == expected_column_count:
        return row, False
    return None, False


def iter_repaired_csv_rows(
    reader, expected_column_count: int, repair_strategy: str | None = None
):
    physical_row_number = 1
    for row in reader:
        physical_row_number += 1
        if not any((value or "").strip() for value in row):
            continue
        repaired_row = repair_csv_row(row, expected_column_count, repair_strategy)
        yield physical_row_number, repaired_row, False
