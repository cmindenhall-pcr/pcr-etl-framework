# Troubleshooting

## SQL Connection Works In VS Code But Not Codex

Symptom:

```text
Encryption not supported on the client
SSL Provider: No credentials are available in the security package
```

Meaning:

The command is running under a different Windows security context than the interactive terminal.

Fix:

- Run SQL-connected commands from an elevated execution context.
- Confirm `.env` is loaded.
- Confirm `SQL_DRIVER` and `SQL_PORT` are set correctly.
- Test with:

```powershell
.\.venv\Scripts\python.exe check_connection.py
```

## Pipeline Says `pipeline_name` Is Required

Use the current CLI form:

```powershell
.\.venv\Scripts\python.exe -m src.run_customer_pipeline --all --profile-only
```

For one pipeline:

```powershell
.\.venv\Scripts\python.exe -m src.run_customer_pipeline Vendor --profile-only
```

## Massive Malformed Row Count

Likely causes:

- wrong delimiter
- wrong header row
- Workday report preamble
- embedded delimiter not quoted
- multi-line quoted field mishandled
- schema variant

First actions:

1. Inspect the first records.
2. Confirm delimiter.
3. Find the true header row.
4. Use `header_row_number` when needed.
5. Generate quarantine files before relaxing thresholds.

## Duplicate Header Warnings

Duplicate header warnings mean the source file has repeated field names.

If loading raw/stg succeeds and row counts match, this may be acceptable for first-pass ingestion, but review before relying on those duplicate columns in downstream analytics.

When needed, use `canonicalize_headers` to make duplicate names unique and stable.

## Raw Table Schema Drift

Raw load may fail if the table already exists with different columns.

Fix:

- verify the source file belongs to the same logical table
- inspect the new header
- drop the raw table if this is a clean reload
- use `hrm` if variants need to be harmonized

## Staging Row Count Mismatch

If raw/stg counts do not match:

- stop before zen
- inspect load execution logs
- check datatype conversion failures
- check whether blank or sentinel conversions changed row eligibility
- verify staging SQL or staging loader assumptions

## Zen Append Duplicates

Zen appends by design. If a same-cut load is rerun into existing zen tables, counts will increase.

Before rerunning zen:

- decide whether this is cumulative history or a reload
- drop zen tables when a clean rebuild is intended
- keep audit logs unless intentionally resetting telemetry

## Pytest Temp Cleanup Error

On Windows, pytest may pass tests but fail while cleaning `%TEMP%`.

Use a repo-local temp directory:

```powershell
New-Item -ItemType Directory -Force tmp\pytest | Out-Null
$env:TEMP=(Resolve-Path tmp\pytest).Path
$env:TMP=$env:TEMP
.\.venv\Scripts\python.exe -m pytest tests --import-mode=importlib
```

