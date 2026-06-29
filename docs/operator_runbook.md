# Operator Runbook

This runbook is for the person operating the framework against a client ERP export.

## Prerequisites

- Windows host with Python and Git.
- Local SQL Server reachable from the terminal running the pipeline.
- A SQL Server database already created for the client/load.
- Source files available under an agreed raw-data root.
- A pipeline config under `config/`.

## Environment Setup

Create and activate the virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create a local `.env` file. This file is ignored by Git and should not be committed.

```text
SQL_SERVER=localhost
SQL_PORT=1433
SQL_DATABASE=<database>
SQL_USERNAME=<username>
SQL_PASSWORD=<password>
SQL_DRIVER=ODBC Driver 17 for SQL Server
```

Verify connectivity:

```powershell
.\.venv\Scripts\python.exe check_connection.py
```

If the Codex tool window cannot connect but the VS Code terminal can, run SQL-connected commands from the elevated Codex execution path or from the VS Code terminal. A common symptom is an ODBC/TLS error before login.

## Bootstrap Database

Run this once per database:

```powershell
sqlcmd -S <server> -d <database> -i sql\00_bootstrap_database.sql
```

The bootstrap creates the framework schemas and audit tables.

## Runtime Variables

Set runtime variables before every run:

```powershell
$envs = Get-Content .env | Where-Object { $_ -match '=' }
foreach ($line in $envs) {
  $k,$v=$line -split '=',2
  Set-Item -Path "Env:$k" -Value $v
}

$env:SQL_DATABASE='<database>'
$env:PIPELINE_CONFIG_PATH='config\<pipeline_config>.json'
$env:PYTHONPATH='.'
```

## Preload Evaluation

Run profile-only first:

```powershell
.\.venv\Scripts\python.exe -m src.run_customer_pipeline --all --profile-only
```

Profile-only truncates audit telemetry before the evaluation. Review:

- `audit.ColumnProfileLog`
- `audit.PipelineRunLog`
- `audit.LoadExecutionLog`
- `logs\pipeline_summaries\`
- `logs\quarantine\` when malformed rows are present

Do not relax malformed thresholds casually. If a threshold needs to increase, generate and review quarantine files first.

## Raw Load

Load raw only after profile review:

```powershell
.\.venv\Scripts\python.exe -m src.run_customer_pipeline --all --raw-only
```

Raw tables are truncated and reloaded by the raw loader. Audit telemetry is retained.

## Staging Load

Load staging from existing raw tables:

```powershell
.\.venv\Scripts\python.exe -m src.run_customer_pipeline --all --staging-only
```

The framework rebuilds staging tables, applies identifier and datatype normalization, adds `AutoId`, and validates row counts from `raw` to `stg`.

## Zen Load

Load zen from existing staging tables:

```powershell
.\.venv\Scripts\python.exe -m src.run_customer_pipeline --all --zen-only
```

Zen tables are appended. If rerunning a same-cut load, decide whether existing zen tables should be dropped first.

## Single Pipeline Runs

To run one pipeline:

```powershell
.\.venv\Scripts\python.exe -m src.run_customer_pipeline <PipelineName> --profile-only
.\.venv\Scripts\python.exe -m src.run_customer_pipeline <PipelineName> --raw-only
.\.venv\Scripts\python.exe -m src.run_customer_pipeline <PipelineName> --staging-only
.\.venv\Scripts\python.exe -m src.run_customer_pipeline <PipelineName> --zen-only
```

## Validation

After each layer transition, compare row counts:

```sql
SELECT COUNT(*) FROM raw.<TableName>;
SELECT COUNT(*) FROM stg.<TableName>;
SELECT COUNT(*) FROM zen.<TableName>;
```

For a loaded config, validate every configured table. Expected status is:

```text
raw count = stg count = zen count
```

## Handling Malformed Rows

Malformed rows usually mean one of these:

- wrong delimiter
- wrong header row
- report preamble before the real header
- embedded newlines or delimiters
- variant schema
- true bad source rows

Do not load malformed rows blindly. First inspect quarantine output or file samples, then decide whether the fix belongs in config, source repair, or a reusable loader enhancement.

## Commit Discipline

Commit only framework code and reusable configs. Do not commit:

- `.env`
- raw data
- logs
- temp files
- SQL backups
- client-only exploratory output

