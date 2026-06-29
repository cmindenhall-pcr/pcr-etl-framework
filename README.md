# PCR ETL Framework

Local-first Python + SQL Server ETL framework for healthcare ERP data.

The framework profiles raw ERP exports, loads them into SQL Server, normalizes identifiers and datatypes, handles schema drift when needed, preserves audit telemetry, and promotes trusted tables through a layered model.

## Current State

This project is an ERP-agnostic loader for healthcare source systems.

The framework supports ERP/client configs for source systems such as:

- Infor
- PeopleSoft
- Oracle
- SAP
- Workday
- Meditech
- Premier and miscellaneous source layouts

The orchestration code is generic. Runtime behavior is selected by config, usually with `PIPELINE_CONFIG_PATH` and `SQL_DATABASE`.

## Layer Model

Core schemas:

- `raw`: source files reflected as SQL tables, close to source shape
- `hrm`: harmonized raw used only when file variants or column drift need consolidation
- `stg`: cleaned, typed, application-friendly tables
- `zen`: trusted ERP-context accumulation layer
- `audit`: operational telemetry, profiling, validation, and run history

Operating principle:

```text
profile -> audit/review -> raw -> hrm if needed -> stg -> zen
```

The `.hrm` layer is skipped when there is no schema drift to resolve.

## Key Doctrine

- Profile first, load second.
- Pre-load evaluations should truncate audit telemetry first unless intentionally preserving prior audit rows.
- Malformed rows should be quarantined with surrounding context before thresholds are relaxed.
- `RecommendedSqlType` is advisory.
- `DefinedSqlType` is authoritative.
- Business meaning outranks physical appearance.
- Identifier-like fields should not be treated as numeric just because they look numeric.
- Blank text should become SQL `NULL` in typed layers.
- Date sentinel `1900-01-01` should become `NULL` for date fields.
- Money fields may use commas and accounting parentheses for negatives.
- PeopleSoft and Oracle date formats require source-aware normalization.
- Field names are normalized for SQL/application ergonomics, including dash/space handling and punctuation rules.

See [docs/data_typing_doctrine.md](docs/data_typing_doctrine.md) for the standing datatype doctrine.

## Repository Layout

- `config/`: pipeline configs and client/ERP load definitions
- `src/`: Python orchestration, profiling, loading, typing, harmonization, and validation modules
- `scripts/`: operational PowerShell/Python entry points and repair utilities
- `sql/`: SQL assets and generated/review scripts
- `docs/`: onboarding templates and datatype doctrine
- `tests/`: pytest coverage for loaders, typing, harmonization, and utilities
- `logs/`: generated runtime logs and quarantine/review outputs; ignored except placeholders
- `tmp/`: temporary/generated scratch files; ignored except placeholders

Important modules:

- `src/run_customer_pipeline.py`: main batch runner
- `src/preload_profiler.py`: pre-load profiling
- `src/csv_loader.py`: raw CSV loading
- `src/stg_loader.py`: raw-to-stg typed loading
- `src/harmonized_stg_loader.py`: harmonized-source staging loads
- `src/zen_loader.py`: stg-to-zen loading
- `src/audit_logger.py`: audit telemetry and datatype recommendation logging
- `src/quarantine_malformed_rows.py`: malformed-row context extraction
- `src/schema_variant_analyzer.py`: file variant and drift analysis

## Setup

Create the virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Verify SQL connectivity:

```powershell
.\.venv\Scripts\python.exe check_connection.py
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Bootstrap a SQL Server database before the first load:

```powershell
sqlcmd -S <server> -d <database> -i sql\00_bootstrap_database.sql
```

## Runtime Pattern

Pipeline runs are config-driven:

```powershell
$env:PYTHONPATH='.'
$env:PIPELINE_CONFIG_PATH='config\pipeline_config_client_erp_raw.json'
$env:SQL_DATABASE='Client_erp'
```

Load staging from prepared lower layers:

```powershell
.\.venv\Scripts\python.exe -c "from src.run_customer_pipeline import run_all_pipelines; run_all_pipelines(staging_only=True)"
```

Load zen from staging:

```powershell
.\.venv\Scripts\python.exe -c "from src.run_customer_pipeline import run_all_pipelines; run_all_pipelines(zen_only=True)"
```

Operational scripts in `scripts/` provide additional entry points for preflight/profile/load workflows.

## Onboarding A New ERP Export

1. Place source files under the agreed raw-data root, for example `<raw-data-root>\<client_erp>`.
2. Create or confirm the matching SQL Server database.
3. Run pre-load evaluation and review `audit.ColumnProfileLog`.
4. Review malformed-row quarantine files when present.
5. Load `.raw`.
6. Run `.hrm` only when schema variants require harmonization.
7. Load `.stg`.
8. Load `.zen`.
9. Compare row counts across loaded layers.
10. Drop temporary lower-layer tables when the trusted layer has been validated and retained.

Use [docs/erp_file_onboarding_template.md](docs/erp_file_onboarding_template.md) when documenting a new inbound file family.
