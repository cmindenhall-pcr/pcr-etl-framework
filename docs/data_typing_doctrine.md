# Data Typing Doctrine

This document defines the standing doctrine for recommending and approving SQL datatypes during profiling and staging preparation.

The framework may suggest datatypes, but datatype approval remains a `CITL` process.

## Core Principle

Business context outranks physical appearance.

Datatype recommendation is not a pure inference exercise.
A column may look numeric while still being a business identifier that must remain text.

Examples that often stay `VARCHAR` even when numeric-looking:

- `INVOICE`
- `VENDOR`
- `PO_NUMBER`
- `CHECK_NUMBER`
- `ZIP`
- `ACCOUNT`
- `CODE`
- `ID`

## Approval Boundary

- `RecommendedSqlType` is the framework recommendation.
- `DefinedSqlType` is the human-reviewed approved datatype.
- `DefinedSqlType` remains the authoritative datatype used downstream.

## Staging Application Rule

Approved datatypes should take effect at the `raw -> stg` boundary, not only in `zen`.

- profiling writes `RecommendedSqlType`
- reviewer approves into `DefinedSqlType`
- staging rebuilds should use `DefinedSqlType` from the latest `PRELOAD_CSV` profile rows

This means `stg` is the first layer where approved business-aware datatypes are materially applied.
`zen` should inherit from a correctly typed staging layer rather than being the first place where datatype intent appears.

## Recommendation Order

Recommendations should be made in this order:

1. business semantics from the column name
2. profile evidence from `MinimumNonNullValue` and `MaximumNonNullValue`
3. `MinMaxValueTypeMismatchFlag` for ambiguity triage
4. targeted deeper scan only when needed

Do not default to inferred physical datatype when business meaning indicates otherwise.

## Default Rules

- quantity-like fields should recommend `INT`
- dollar amount fields should recommend `MONEY`
- rate or percentage fields should recommend `DECIMAL(17, 4)`
- date or datetime-like fields should recommend `DATE`
- identifier, code, and business key fields should recommend `VARCHAR`

## Min/Max Triage Rule

Use `MinimumNonNullValue` and `MaximumNonNullValue` as the default first-pass evidence.

- if min and max imply the same type family, a recommendation may be made from that evidence plus business semantics
- if `MinMaxValueTypeMismatchFlag = 1`, treat the column as ambiguous
- ambiguous columns should be escalated to targeted review or deeper scan rather than auto-promoted

This keeps profiling scalable across large file sets while preserving safety.

## CITL Doctrine

The correct model is:

- machine recommends
- reviewer approves in `CITL`
- `DefinedSqlType` records the final approved answer

The framework should optimize for safe recommendations, not aggressive automatic conversion.
