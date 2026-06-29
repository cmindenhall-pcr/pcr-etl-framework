from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID


@dataclass(frozen=True)
class InferenceCandidate:
    sql_type: str
    confidence_pct: float
    matched_value_count: int
    priority: int = 0


APPROVAL_PENDING = "PENDING"
APPROVAL_APPROVED = "APPROVED"
APPROVAL_REJECTED = "REJECTED"
VALID_APPROVAL_STATUSES = {
    APPROVAL_PENDING,
    APPROVAL_APPROVED,
    APPROVAL_REJECTED,
}
INFERENCE_CONFIDENCE_THRESHOLD = 80.0


def infer_column_schema(
    column_name: str,
    values: list[str],
    max_string_length: int | None,
    blank_row_count: int,
) -> dict[str, int | float | str | None]:
    non_blank_values = [value.strip() for value in values if value.strip()]
    non_null_count = len(non_blank_values)
    nullable_flag = 1 if blank_row_count > 0 else 0
    nullability = "NULL" if nullable_flag else "NOT NULL"

    if non_null_count == 0:
        return {
            "column_name": column_name,
            "defined_sql_type": _varchar_type(max_string_length),
            "inferred_sql_type": _varchar_type(max_string_length),
            "confidence_pct": 0.0,
            "matched_value_count": 0,
            "non_null_row_count": 0,
            "blank_row_count": blank_row_count,
            "max_string_length": max_string_length,
            "nullable_flag": nullable_flag,
            "nullability": nullability,
            "inference_reason": "all values blank",
        }

    candidates = [
        _infer_bit(non_blank_values),
        _infer_tinyint(non_blank_values),
        _infer_smallint(non_blank_values),
        _infer_uniqueidentifier(non_blank_values),
        _infer_time(non_blank_values),
        _infer_datetimeoffset(non_blank_values),
        _infer_datetime2(non_blank_values),
        _infer_date(non_blank_values),
        _infer_int(non_blank_values),
        _infer_bigint(non_blank_values),
        _infer_decimal(non_blank_values),
    ]

    best_candidate = max(candidates, key=lambda candidate: (candidate.confidence_pct, candidate.priority))
    varchar_type = _varchar_type(max_string_length)

    if best_candidate.confidence_pct < INFERENCE_CONFIDENCE_THRESHOLD:
        inferred_sql_type = varchar_type
        inference_reason = "fallback_to_varchar_low_confidence"
        confidence_pct = best_candidate.confidence_pct
        matched_value_count = best_candidate.matched_value_count
    else:
        inferred_sql_type = best_candidate.sql_type
        inference_reason = "high_confidence_match"
        confidence_pct = best_candidate.confidence_pct
        matched_value_count = best_candidate.matched_value_count

    return {
        "column_name": column_name,
        "defined_sql_type": inferred_sql_type,
        "inferred_sql_type": inferred_sql_type,
        "confidence_pct": round(confidence_pct, 2),
        "matched_value_count": matched_value_count,
        "non_null_row_count": non_null_count,
        "blank_row_count": blank_row_count,
        "max_string_length": max_string_length,
        "nullable_flag": nullable_flag,
        "nullability": nullability,
        "inference_reason": inference_reason,
    }


def compare_review_artifacts(
    current_review: dict,
    prior_review: dict,
) -> dict[str, object]:
    prior_files = {
        item["file_name"]: item
        for item in prior_review.get("files", [])
    }

    drift_summary = {
        "new_files": [],
        "removed_files": [],
        "changed_files": [],
    }

    current_file_names = {item["file_name"] for item in current_review.get("files", [])}
    prior_file_names = set(prior_files.keys())

    drift_summary["new_files"] = sorted(current_file_names - prior_file_names)
    drift_summary["removed_files"] = sorted(prior_file_names - current_file_names)

    for current_file in current_review.get("files", []):
        prior_file = prior_files.get(current_file["file_name"])
        if prior_file is None:
            continue

        file_drift = _compare_file_schema(current_file, prior_file)
        if file_drift["has_drift"]:
            drift_summary["changed_files"].append(file_drift)

    drift_summary["has_drift"] = bool(
        drift_summary["new_files"]
        or drift_summary["removed_files"]
        or drift_summary["changed_files"]
    )
    return drift_summary


def initialize_review_artifact(review_artifact: dict) -> dict:
    normalized = deepcopy(review_artifact)
    for file_item in normalized.get("files", []):
        _initialize_file_review(file_item)
    return normalized


def apply_review_decisions(review_artifact: dict, decisions: dict) -> dict:
    updated_artifact = initialize_review_artifact(review_artifact)
    files_by_name = {
        file_item["file_name"]: file_item
        for file_item in updated_artifact.get("files", [])
    }

    for file_name, file_decision in decisions.get("files", {}).items():
        if file_name not in files_by_name:
            raise ValueError(f"Decision references unknown file: {file_name}")

        file_item = files_by_name[file_name]
        _apply_file_decision(file_item, file_decision)

    return updated_artifact


def validate_review_approval(review_artifact: dict) -> None:
    pending_files = []
    rejected_files = []

    for file_item in initialize_review_artifact(review_artifact).get("files", []):
        review_status = file_item["review_status"]
        if review_status == APPROVAL_PENDING:
            pending_files.append(file_item["file_name"])
        elif review_status == APPROVAL_REJECTED:
            rejected_files.append(file_item["file_name"])

    if pending_files or rejected_files:
        issues = []
        if pending_files:
            issues.append(
                f"{len(pending_files)} files still pending review: {', '.join(sorted(pending_files)[:10])}"
            )
        if rejected_files:
            issues.append(
                f"{len(rejected_files)} files marked rejected: {', '.join(sorted(rejected_files)[:10])}"
            )
        raise ValueError("Schema review is not fully approved. " + " | ".join(issues))


def load_reviewed_schema(review_artifact: dict) -> dict[str, object]:
    normalized = initialize_review_artifact(review_artifact)
    validate_review_approval(normalized)

    approved_tables = []
    approved_columns = []

    for file_item in normalized.get("files", []):
        table_name = file_item.get("table_name") or file_item["file_name"]
        approved_tables.append(table_name)

        for column in file_item.get("schema_review", {}).get("columns", []):
            approved_columns.append(
                {
                    "file_name": file_item["file_name"],
                    "table_name": table_name,
                    "column_name": column["column_name"],
                    "defined_sql_type": column["defined_sql_type"],
                    "final_sql_type": column["final_sql_type"],
                    "override_sql_type": column.get("override_sql_type"),
                    "nullability": column.get("nullability"),
                    "nullable_flag": column.get("nullable_flag"),
                    "confidence_pct": column.get("confidence_pct"),
                    "review_notes": column.get("review_notes", ""),
                }
            )

    return {
        "approved_tables": approved_tables,
        "columns": approved_columns,
    }


def summarize_review_status(review_artifact: dict) -> dict[str, object]:
    normalized = initialize_review_artifact(review_artifact)
    counts = {
        APPROVAL_PENDING: 0,
        APPROVAL_APPROVED: 0,
        APPROVAL_REJECTED: 0,
    }

    pending_files = []
    rejected_files = []
    override_count = 0

    for file_item in normalized.get("files", []):
        review_status = file_item["review_status"]
        counts[review_status] = counts.get(review_status, 0) + 1
        if review_status == APPROVAL_PENDING:
            pending_files.append(file_item["file_name"])
        elif review_status == APPROVAL_REJECTED:
            rejected_files.append(file_item["file_name"])

        for column in file_item.get("schema_review", {}).get("columns", []):
            if column.get("override_sql_type"):
                override_count += 1

    return {
        "file_count": len(normalized.get("files", [])),
        "status_counts": counts,
        "pending_files": pending_files,
        "rejected_files": rejected_files,
        "override_column_count": override_count,
        "all_approved": not pending_files and not rejected_files,
    }


def render_review_markdown(
    review_artifact: dict,
    confidence_warning_threshold: float = 95.0,
) -> str:
    normalized = initialize_review_artifact(review_artifact)
    status_summary = summarize_review_status(normalized)

    lines = [
        "# Schema Review Summary",
        "",
        f"- Files: {status_summary['file_count']}",
        f"- Approved: {status_summary['status_counts'][APPROVAL_APPROVED]}",
        f"- Pending: {status_summary['status_counts'][APPROVAL_PENDING]}",
        f"- Rejected: {status_summary['status_counts'][APPROVAL_REJECTED]}",
        f"- Column overrides: {status_summary['override_column_count']}",
        f"- All approved: {'yes' if status_summary['all_approved'] else 'no'}",
    ]

    drift_summary = normalized.get("drift_summary")
    if drift_summary:
        lines.extend(
            [
                "",
                "## Drift",
                "",
                f"- Has drift: {'yes' if drift_summary.get('has_drift') else 'no'}",
                f"- New files: {len(drift_summary.get('new_files', []))}",
                f"- Removed files: {len(drift_summary.get('removed_files', []))}",
                f"- Changed files: {len(drift_summary.get('changed_files', []))}",
            ]
        )

    for file_item in normalized.get("files", []):
        lines.extend(_render_file_markdown(file_item, confidence_warning_threshold))

    return "\n".join(lines).strip() + "\n"


def _compare_file_schema(current_file: dict, prior_file: dict) -> dict[str, object]:
    current_columns = {
        column["column_name"]: column
        for column in current_file.get("schema_review", {}).get("columns", [])
    }
    prior_columns = {
        column["column_name"]: column
        for column in prior_file.get("schema_review", {}).get("columns", [])
    }

    current_names = set(current_columns.keys())
    prior_names = set(prior_columns.keys())

    added_columns = sorted(current_names - prior_names)
    removed_columns = sorted(prior_names - current_names)
    changed_columns = []

    for column_name in sorted(current_names & prior_names):
        current_column = current_columns[column_name]
        prior_column = prior_columns[column_name]
        column_changes = {}

        for key in ("defined_sql_type", "nullability", "max_string_length"):
            if current_column.get(key) != prior_column.get(key):
                column_changes[key] = {
                    "current": current_column.get(key),
                    "prior": prior_column.get(key),
                }

        if column_changes:
            changed_columns.append(
                {
                    "column_name": column_name,
                    "changes": column_changes,
                }
            )

    has_drift = bool(added_columns or removed_columns or changed_columns)
    return {
        "file_name": current_file["file_name"],
        "table_name": current_file.get("table_name"),
        "has_drift": has_drift,
        "added_columns": added_columns,
        "removed_columns": removed_columns,
        "changed_columns": changed_columns,
    }


def _infer_int(values: list[str]) -> InferenceCandidate:
    matched = 0
    for value in values:
        try:
            parsed = int(value)
        except ValueError:
            continue

        if -(2**31) <= parsed <= (2**31 - 1):
            matched += 1

    return _build_candidate("INT", matched, len(values), priority=40)


def _infer_bigint(values: list[str]) -> InferenceCandidate:
    matched = 0
    for value in values:
        try:
            parsed = int(value)
        except ValueError:
            continue

        if -(2**63) <= parsed <= (2**63 - 1):
            matched += 1

    return _build_candidate("BIGINT", matched, len(values), priority=30)


def _infer_decimal(values: list[str]) -> InferenceCandidate:
    matched = 0
    max_precision = 1
    max_scale = 0
    for value in values:
        try:
            parsed = Decimal(value)
        except (InvalidOperation, ValueError):
            continue
        matched += 1
        precision, scale = _decimal_precision_scale(parsed)
        max_precision = max(max_precision, precision)
        max_scale = max(max_scale, scale)

    sql_type = _decimal_sql_type(max_precision, max_scale)
    return _build_candidate(sql_type, matched, len(values), priority=20)


def _infer_date(values: list[str]) -> InferenceCandidate:
    matched = 0
    for value in values:
        parsed = _parse_datetime_like(value)
        if parsed is None:
            continue
        if parsed.time() == datetime.min.time():
            matched += 1

    return _build_candidate("DATE", matched, len(values), priority=75)


def _infer_datetime2(values: list[str]) -> InferenceCandidate:
    matched = 0
    for value in values:
        parsed = _parse_datetime_like(value)
        if parsed is not None and parsed.tzinfo is None:
            matched += 1

    return _build_candidate("DATETIME2", matched, len(values), priority=70)


def _infer_datetimeoffset(values: list[str]) -> InferenceCandidate:
    matched = 0
    for value in values:
        parsed = _parse_datetime_like(value)
        if parsed is not None and parsed.tzinfo is not None:
            matched += 1

    return _build_candidate("DATETIMEOFFSET", matched, len(values), priority=80)


def _infer_time(values: list[str]) -> InferenceCandidate:
    matched = 0
    for value in values:
        if _parse_time_like(value) is not None:
            matched += 1

    return _build_candidate("TIME", matched, len(values), priority=85)


def _infer_bit(values: list[str]) -> InferenceCandidate:
    matched = 0
    for value in values:
        if _parse_boolean_like(value) is not None:
            matched += 1

    return _build_candidate("BIT", matched, len(values), priority=110)


def _infer_tinyint(values: list[str]) -> InferenceCandidate:
    matched = 0
    for value in values:
        parsed = _parse_integer_like(value)
        if parsed is None:
            continue
        if 0 <= parsed <= 255:
            matched += 1

    return _build_candidate("TINYINT", matched, len(values), priority=60)


def _infer_smallint(values: list[str]) -> InferenceCandidate:
    matched = 0
    for value in values:
        parsed = _parse_integer_like(value)
        if parsed is None:
            continue
        if -(2**15) <= parsed <= (2**15 - 1):
            matched += 1

    return _build_candidate("SMALLINT", matched, len(values), priority=50)


def _infer_uniqueidentifier(values: list[str]) -> InferenceCandidate:
    matched = 0
    for value in values:
        if _parse_uuid_like(value) is not None:
            matched += 1

    return _build_candidate("UNIQUEIDENTIFIER", matched, len(values), priority=90)


def _parse_datetime_like(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None

    iso_candidate = normalized
    if iso_candidate.endswith("Z"):
        iso_candidate = iso_candidate[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(iso_candidate)
    except ValueError:
        pass

    patterns = (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%m/%d/%Y",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M %p",
    )
    for pattern in patterns:
        try:
            return datetime.strptime(normalized, pattern)
        except ValueError:
            continue
    return None


def _parse_time_like(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None

    patterns = (
        "%H:%M",
        "%H:%M:%S",
        "%H:%M:%S.%f",
        "%I:%M %p",
        "%I:%M:%S %p",
    )
    for pattern in patterns:
        try:
            return datetime.strptime(normalized, pattern)
        except ValueError:
            continue
    return None


def _parse_boolean_like(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "t", "yes", "y"}:
        return True
    if normalized in {"0", "false", "f", "no", "n"}:
        return False
    return None


def _parse_integer_like(value: str) -> int | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return int(normalized)
    except ValueError:
        return None


def _parse_uuid_like(value: str) -> UUID | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return UUID(normalized)
    except (ValueError, AttributeError):
        return None


def _varchar_type(max_string_length: int | None) -> str:
    if max_string_length is None:
        return "VARCHAR(100)"
    if max_string_length <= 100:
        return "VARCHAR(100)"
    if max_string_length <= 255:
        return "VARCHAR(255)"
    if max_string_length <= 1000:
        return "VARCHAR(1000)"
    return "VARCHAR(4000)"


def _build_candidate(
    sql_type: str,
    matched_count: int,
    total_count: int,
    priority: int = 0,
) -> InferenceCandidate:
    confidence_pct = 0.0 if total_count == 0 else (matched_count / total_count) * 100.0
    return InferenceCandidate(
        sql_type=sql_type,
        confidence_pct=confidence_pct,
        matched_value_count=matched_count,
        priority=priority,
    )


def _decimal_precision_scale(value: Decimal) -> tuple[int, int]:
    normalized = value.normalize()
    sign, digits, exponent = normalized.as_tuple()
    digit_count = len(digits)
    scale = max(-exponent, 0)
    precision = max(digit_count, scale + 1)
    return precision, scale


def _decimal_sql_type(max_precision: int, max_scale: int) -> str:
    precision = min(max(max_precision, 1), 38)
    scale = min(max(max_scale, 0), precision)
    if scale == 0 and precision <= 18:
        return "DECIMAL(18,0)"
    return f"DECIMAL({precision},{scale})"


def _initialize_file_review(file_item: dict) -> None:
    file_item.setdefault("review_status", APPROVAL_PENDING)
    file_item.setdefault("review_notes", "")
    file_item.setdefault("approved_by", None)
    file_item.setdefault("approved_at_utc", None)

    for column in file_item.get("schema_review", {}).get("columns", []):
        column.setdefault("override_sql_type", None)
        column.setdefault("final_sql_type", column.get("defined_sql_type"))
        column.setdefault("review_notes", "")
        _refresh_column_final_sql_type(column)


def _apply_file_decision(file_item: dict, file_decision: dict) -> None:
    review_status = file_decision.get("review_status")
    if review_status is not None:
        normalized_status = str(review_status).strip().upper()
        if normalized_status not in VALID_APPROVAL_STATUSES:
            raise ValueError(
                f"Invalid review_status '{review_status}' for file {file_item['file_name']}"
            )
        file_item["review_status"] = normalized_status

    if "review_notes" in file_decision:
        file_item["review_notes"] = str(file_decision.get("review_notes") or "")

    if "approved_by" in file_decision:
        approved_by = file_decision.get("approved_by")
        file_item["approved_by"] = None if approved_by in (None, "") else str(approved_by)

    if file_item["review_status"] == APPROVAL_APPROVED:
        file_item["approved_at_utc"] = datetime.now(timezone.utc).isoformat()
    elif file_item["review_status"] != APPROVAL_APPROVED:
        file_item["approved_at_utc"] = None

    if file_decision.get("approve_all_columns"):
        for column in file_item.get("schema_review", {}).get("columns", []):
            column["review_notes"] = column.get("review_notes", "")

    column_decisions = file_decision.get("columns", {})
    columns_by_name = {
        column["column_name"]: column
        for column in file_item.get("schema_review", {}).get("columns", [])
    }

    for column_name, column_decision in column_decisions.items():
        if column_name not in columns_by_name:
            raise ValueError(
                f"Decision references unknown column '{column_name}' in file {file_item['file_name']}"
            )

        column = columns_by_name[column_name]
        if "override_sql_type" in column_decision:
            override_sql_type = column_decision.get("override_sql_type")
            column["override_sql_type"] = (
                None if override_sql_type in (None, "") else str(override_sql_type).strip()
            )
        if "review_notes" in column_decision:
            column["review_notes"] = str(column_decision.get("review_notes") or "")
        _refresh_column_final_sql_type(column)


def _refresh_column_final_sql_type(column: dict) -> None:
    final_sql_type = column.get("override_sql_type") or column.get("defined_sql_type")
    column["final_sql_type"] = final_sql_type


def _render_file_markdown(file_item: dict, confidence_warning_threshold: float) -> list[str]:
    review_status = file_item.get("review_status", APPROVAL_PENDING)
    schema_columns = file_item.get("schema_review", {}).get("columns", [])
    override_columns = [column for column in schema_columns if column.get("override_sql_type")]
    low_confidence_columns = [
        column
        for column in schema_columns
        if float(column.get("confidence_pct") or 0.0) < confidence_warning_threshold
    ]

    lines = [
        "",
        f"## {file_item.get('table_name') or file_item['file_name']}",
        "",
        f"- File: `{file_item['file_name']}`",
        f"- Review status: `{review_status}`",
        f"- Data rows: {file_item.get('data_row_count', 0)}",
        f"- Malformed rows: {file_item.get('malformed_row_count', 0)}",
        f"- Duplicate headers: {len(file_item.get('duplicate_headers', []))}",
        f"- Column overrides: {len(override_columns)}",
        f"- Low-confidence columns (< {confidence_warning_threshold:.1f}%): {len(low_confidence_columns)}",
    ]

    if file_item.get("review_notes"):
        lines.append(f"- Review notes: {file_item['review_notes']}")
    if file_item.get("approved_by"):
        lines.append(f"- Approved by: `{file_item['approved_by']}`")
    if file_item.get("approved_at_utc"):
        lines.append(f"- Approved at UTC: `{file_item['approved_at_utc']}`")

    if not schema_columns:
        return lines

    lines.extend(
        [
            "",
            "| Column | Final SQL Type | Inferred Type | Confidence % | Nullability | Override | Notes |",
            "| --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )

    prioritized_columns = _prioritize_review_columns(schema_columns, confidence_warning_threshold)
    for column in prioritized_columns:
        lines.append(
            "| {column_name} | {final_sql_type} | {defined_sql_type} | {confidence_pct:.2f} | {nullability} | {override_sql_type} | {review_notes} |".format(
                column_name=_escape_markdown_cell(column["column_name"]),
                final_sql_type=_escape_markdown_cell(column.get("final_sql_type") or ""),
                defined_sql_type=_escape_markdown_cell(column.get("defined_sql_type") or ""),
                confidence_pct=float(column.get("confidence_pct") or 0.0),
                nullability=_escape_markdown_cell(column.get("nullability") or ""),
                override_sql_type=_escape_markdown_cell(column.get("override_sql_type") or ""),
                review_notes=_escape_markdown_cell(column.get("review_notes") or ""),
            )
        )

    return lines


def _prioritize_review_columns(
    schema_columns: list[dict],
    confidence_warning_threshold: float,
    max_rows: int = 25,
) -> list[dict]:
    prioritized = sorted(
        schema_columns,
        key=lambda column: (
            0 if column.get("override_sql_type") else 1,
            0 if float(column.get("confidence_pct") or 0.0) < confidence_warning_threshold else 1,
            float(column.get("confidence_pct") or 0.0),
            str(column.get("column_name") or ""),
        ),
    )
    return prioritized[:max_rows]


def _escape_markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()
