from src.harmonization_anomaly_review import (
    _is_generated_blank_header_column,
    _looks_like_rename_candidate,
)


def test_generated_blank_header_column_detection() -> None:
    assert _is_generated_blank_header_column("Column_031") is True
    assert _is_generated_blank_header_column("Column_3") is True
    assert _is_generated_blank_header_column("Amount_in_LC") is False


def test_rename_candidate_detection_uses_generic_name_similarity() -> None:
    assert _looks_like_rename_candidate("Amount_LC", "Amount_in_LC") is True
    assert _looks_like_rename_candidate("DN_Qty", "Del_Note_Qty") is True
    assert _looks_like_rename_candidate("Created_By", "Created") is True
    assert _looks_like_rename_candidate("Supplier", "Amount") is False
