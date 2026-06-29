import argparse
from datetime import date, datetime, timedelta

from src.pipeline_summary import write_pipeline_day_summary_csv, write_pipeline_run_summary_csv


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export pipeline summary CSVs from audit tables."
    )
    parser.add_argument(
        "--run-id",
        help="Export a summary CSV for a single pipeline run.",
    )
    parser.add_argument(
        "--date",
        help="Export one CSV containing all pipeline runs for YYYY-MM-DD.",
    )
    parser.add_argument(
        "--yesterday",
        action="store_true",
        help="Export one CSV containing all pipeline runs for yesterday.",
    )
    return parser.parse_args()


def _resolve_summary_date(args: argparse.Namespace) -> date | None:
    if args.yesterday:
        return datetime.now().date() - timedelta(days=1)
    if args.date:
        return date.fromisoformat(args.date)
    return None


if __name__ == "__main__":
    args = _parse_args()
    summary_date = _resolve_summary_date(args)

    if args.run_id:
        print(write_pipeline_run_summary_csv(args.run_id))
    elif summary_date is not None:
        print(write_pipeline_day_summary_csv(summary_date))
    else:
        raise SystemExit("Provide --run-id, --date YYYY-MM-DD, or --yesterday.")
