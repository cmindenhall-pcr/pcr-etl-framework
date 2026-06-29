# AGENTS.md

## Project
Python + SQL Server ETL framework for local-first, cloud-ready data engineering.

## Architecture
- Schemas:
  - raw = landing
  - stg = preparation
  - zen = trusted final layer
  - audit = operational telemetry
- Never use dbo for final-layer tables.
- Prefer file-driven SQL in /sql over inline SQL in notebooks.
- Keep orchestration in /src and transformations in /sql.

## Execution rules
- One step at a time.
- Do not make broad refactors unless explicitly asked.
- Preserve run_id, audit logging, and PipelineRunLog behavior.
- Do not remove validation or logging unless replacing it with something stronger.
- Keep notebook usage limited to lab/testing; production behavior belongs in scripts/modules.

## Environment
- Windows host, local SQL Server for development.
- VS Code is the primary editor.
- Python environment and Git are already configured.

## Coding preferences
- Be explicit over clever.
- Keep modules small and reusable.
- Maintain naming discipline and schema consistency.
- Do not hardcode secrets.