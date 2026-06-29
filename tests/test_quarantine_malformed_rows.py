import json
from pathlib import Path

from src.quarantine_malformed_rows import write_quarantine_files


def test_write_quarantine_files_respects_header_row_number(tmp_path: Path) -> None:
    source_file = tmp_path / "excel_export.csv"
    source_file.write_text(
        "sep=,\n"
        "A,B\n"
        "1,2\n"
        "3\n"
        "4,5\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "ExcelExport": {
                    "preload_profile": {
                        "source_files": [str(source_file)],
                        "delimiter": ",",
                        "header_row_number": 2,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = write_quarantine_files(
        config_path=config_path,
        database_name="TestDb",
        context_rows=1,
        output_root=tmp_path / "quarantine",
    )

    assert result["total_malformed_rows"] == 1
    output_path = Path(result["written_files"][0]["output_path"])
    output_text = output_path.read_text(encoding="utf-8")
    assert "excel_export:row:4" in output_text
    assert "sep=," not in output_text
