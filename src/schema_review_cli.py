import argparse
import json
from pathlib import Path

from src.bcp_format import build_bcp_format_files
from src.schema_inference import (
    apply_review_decisions,
    initialize_review_artifact,
    load_reviewed_schema,
    render_review_markdown,
    summarize_review_status,
    validate_review_approval,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply, validate, and export schema review decisions."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    normalize_parser = subparsers.add_parser(
        "normalize",
        help="Normalize a schema review artifact by adding default review metadata.",
    )
    normalize_parser.add_argument("review_artifact", help="Path to the schema review artifact JSON.")
    normalize_parser.add_argument(
        "--output",
        required=True,
        help="Path to write the normalized artifact JSON.",
    )

    apply_parser = subparsers.add_parser(
        "apply-decisions",
        help="Apply approval and override decisions to a schema review artifact.",
    )
    apply_parser.add_argument("review_artifact", help="Path to the schema review artifact JSON.")
    apply_parser.add_argument(
        "--decisions",
        required=True,
        help="Path to the decision JSON file.",
    )
    apply_parser.add_argument(
        "--output",
        required=True,
        help="Path to write the updated review artifact JSON.",
    )

    check_parser = subparsers.add_parser(
        "check-approval",
        help="Validate that every file in the review artifact is approved.",
    )
    check_parser.add_argument("review_artifact", help="Path to the schema review artifact JSON.")

    export_parser = subparsers.add_parser(
        "export-reviewed-schema",
        help="Export the approved schema view used by downstream automation.",
    )
    export_parser.add_argument("review_artifact", help="Path to the schema review artifact JSON.")
    export_parser.add_argument(
        "--output",
        required=True,
        help="Path to write the approved schema JSON.",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Summarize review approval state and override usage.",
    )
    status_parser.add_argument("review_artifact", help="Path to the schema review artifact JSON.")

    markdown_parser = subparsers.add_parser(
        "render-markdown",
        help="Render a human-readable markdown review summary from a schema review artifact.",
    )
    markdown_parser.add_argument("review_artifact", help="Path to the schema review artifact JSON.")
    markdown_parser.add_argument(
        "--output",
        required=True,
        help="Path to write the markdown review summary.",
    )
    markdown_parser.add_argument(
        "--confidence-warning-threshold",
        type=float,
        default=95.0,
        help="Flag inferred columns below this confidence level in the markdown summary.",
    )

    format_parser = subparsers.add_parser(
        "generate-bcp-format-files",
        help="Generate non-XML BCP .fmt files from an approved schema review artifact.",
    )
    format_parser.add_argument("review_artifact", help="Path to the schema review artifact JSON.")
    format_parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to write generated .fmt files.",
    )
    format_parser.add_argument(
        "--include-raw-metadata",
        action="store_true",
        help="Append LoadDate and SFileName fields for the current raw-load bulk file layout.",
    )

    args = parser.parse_args()

    if args.command == "normalize":
        review_artifact = _read_json(args.review_artifact)
        normalized = initialize_review_artifact(review_artifact)
        _write_json(args.output, normalized)
        print(json.dumps(summarize_review_status(normalized), indent=2))
        return

    if args.command == "apply-decisions":
        review_artifact = _read_json(args.review_artifact)
        decisions = _read_json(args.decisions)
        updated = apply_review_decisions(review_artifact, decisions)
        _write_json(args.output, updated)
        print(json.dumps(summarize_review_status(updated), indent=2))
        return

    if args.command == "check-approval":
        review_artifact = _read_json(args.review_artifact)
        validate_review_approval(review_artifact)
        print(json.dumps(summarize_review_status(review_artifact), indent=2))
        return

    if args.command == "export-reviewed-schema":
        review_artifact = _read_json(args.review_artifact)
        reviewed_schema = load_reviewed_schema(review_artifact)
        _write_json(args.output, reviewed_schema)
        print(
            json.dumps(
                {
                    "approved_table_count": len(reviewed_schema["approved_tables"]),
                    "approved_column_count": len(reviewed_schema["columns"]),
                },
                indent=2,
            )
        )
        return

    if args.command == "status":
        review_artifact = _read_json(args.review_artifact)
        print(json.dumps(summarize_review_status(review_artifact), indent=2))
        return

    if args.command == "render-markdown":
        review_artifact = _read_json(args.review_artifact)
        markdown = render_review_markdown(
            review_artifact,
            confidence_warning_threshold=args.confidence_warning_threshold,
        )
        output_path = Path(args.output)
        output_path.write_text(markdown, encoding="utf-8")
        print(
            json.dumps(
                {
                    "output_path": str(output_path),
                    "file_count": len(review_artifact.get("files", [])),
                },
                indent=2,
            )
        )
        return

    if args.command == "generate-bcp-format-files":
        review_artifact = _read_json(args.review_artifact)
        reviewed_schema = load_reviewed_schema(review_artifact)
        generated_paths = build_bcp_format_files(
            reviewed_schema=reviewed_schema,
            output_dir=Path(args.output_dir),
            include_raw_metadata=args.include_raw_metadata,
        )
        print(
            json.dumps(
                {
                    "output_dir": str(Path(args.output_dir)),
                    "format_file_count": len(generated_paths),
                    "files": [str(path) for path in generated_paths],
                },
                indent=2,
            )
        )
        return


def _read_json(path: str) -> dict:
    json_path = Path(path)
    if not json_path.exists():
        raise FileNotFoundError(f"JSON file not found: {json_path}")
    return json.loads(json_path.read_text(encoding="utf-8"))


def _write_json(path: str, payload: dict) -> None:
    output_path = Path(path)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
