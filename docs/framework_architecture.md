# Framework Architecture

The framework is a config-driven ETL system for healthcare ERP exports. It is local-first, SQL Server-backed, and designed to preserve auditability while moving source files through increasingly trusted layers.

## Layer Model

```text
profile -> audit/review -> raw -> hrm if needed -> stg -> zen
```

Core schemas:

- `raw`: source files reflected as SQL tables, close to source shape
- `hrm`: harmonized raw for schema drift or file variants
- `stg`: cleaned, typed, application-friendly preparation layer
- `zen`: trusted ERP-context accumulation layer
- `audit`: operational telemetry, profiling, and validation history

The `hrm` layer is skipped when there is no schema drift to resolve.

## Design Principles

- Profile first, load second.
- Keep runtime behavior config-driven.
- Preserve audit telemetry for every meaningful operation.
- Treat datatype recommendations as advisory until reviewed.
- Let business meaning outrank physical appearance.
- Normalize application-facing identifiers at the staging boundary.
- Keep raw tables as close as practical to the incoming source.
- Use reusable code paths for ERP-specific quirks when they recur.

## Pipeline Runner

`src/run_customer_pipeline.py` is the main entry point.

Supported modes:

- `--profile-only`: profile source files and write audit telemetry
- `--raw-only`: profile and load raw
- `--staging-only`: rebuild staging from raw
- `--zen-only`: append zen from staging
- `--all`: run every pipeline in the active config

The runner preserves `run_id`, writes `PipelineRunLog`, writes load execution rows, validates row counts, and writes pipeline summary CSV files.

## Profiling

`src/preload_profiler.py` reads configured source files and records:

- column names
- ordinal positions
- min/max values
- max string length
- blank and non-null row counts
- nullable flag
- recommended SQL type
- malformed row counts
- duplicate header warnings

Profile output lands in `audit.ColumnProfileLog` and related audit tables.

## Raw Loading

`src/csv_loader.py` creates or validates raw table structure, then loads configured CSV files.

Raw load behavior:

- supports single-file and multi-file table loads
- supports configured delimiter
- supports configured header row
- supports canonicalized headers
- adds `LoadDate` and `SFileName`
- uses BCP for larger chunks
- falls back to pyodbc direct insert when needed
- truncates the raw target before loading

## Staging Loading

`src/stg_loader.py` rebuilds staging from raw.

Staging behavior:

- normalizes column names
- applies approved datatypes
- converts blank strings to `NULL`
- handles money/accounting numeric patterns
- handles date null sentinels
- adds rightmost `AutoId INT IDENTITY(1,1)`
- validates raw/stg row counts

## Harmonization

Use `hrm` when files that belong to one logical table have schema variants or column drift.

The harmonization process should:

- preserve provenance
- collapse likely same fields when approved
- omit unnamed all-null generated columns
- isolate unnamed columns with values for review
- produce a cleaner source for staging

If there is no drift, skip `hrm`.

## Zen Loading

`src/zen_loader.py` appends staging rows to zen tables.

Zen behavior:

- creates the zen schema if needed
- creates the target table if missing
- mirrors the staging schema except staging `AutoId`
- adds its own rightmost `AutoId INT IDENTITY(1,1)`
- appends rather than truncates

Because zen is cumulative, decide whether to drop or retain existing zen tables before reruns.

## Audit Model

Audit tables capture:

- pipeline start/end
- profile results
- load execution status
- row counts
- durations
- warnings and failures

Audit is the control plane. Operational decisions should be grounded in audit evidence.

