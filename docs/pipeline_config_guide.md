# Pipeline Config Guide

Pipeline configs are JSON files under `config/`. The active config is selected with `PIPELINE_CONFIG_PATH`.

## Basic Shape

```json
{
  "Vendor": {
    "source_table": "raw.Vendor",
    "preload_profile": {
      "source_files": [
        "<raw-data-root>\\Vendor.csv"
      ],
      "column_mappings": {
        "Vendor": "Vendor",
        "VendorName": "VendorName"
      },
      "max_malformed_rows": 0,
      "has_header": true,
      "delimiter": ","
    },
    "staging_table": "stg.Vendor",
    "target_table": "zen.Vendor"
  }
}
```

The top-level key is the pipeline name.

## Required Keys

`source_table`

Raw destination table.

`preload_profile`

Source file and profiling/load rules.

`source_files`

List of files to profile and load into one logical table.

`column_mappings`

Map source column names to destination raw column names. When `canonicalize_headers` is enabled, use the canonicalized source names as keys.

`max_malformed_rows`

Allowed malformed row count. Keep this at `0` unless malformed rows have been reviewed.

`has_header`

Usually `true`.

`delimiter`

Common values are `","` and `"\t"`.

`staging_table`

Staging target.

`target_table`

Zen target. Required for `--zen-only`.

## Optional Keys

`header_row_number`

One-based row number containing the real CSV header. Use this for report exports with title/filter preambles.

`canonicalize_headers`

When `true`, source headers are normalized before matching `column_mappings`.

`row_repair_strategy`

Named repair strategy for known row-shape issues.

`date_null_sentinels`

Date-like source values that should become SQL `NULL` in staging.

```json
"date_null_sentinels": ["1900-01-01", "1753-01-01 00:00:00.000"]
```

`column_sql_types`

Raw load override for destination column SQL types.

`chunk_size_mb`

Optional chunking control for very large files.

## Normal CSV Example

```json
{
  "Vendor": {
    "source_table": "raw.Vendor",
    "preload_profile": {
      "source_files": ["<raw-data-root>\\Vendor.csv"],
      "column_mappings": {
        "Vendor": "Vendor",
        "VendorName": "VendorName"
      },
      "max_malformed_rows": 0,
      "has_header": true,
      "delimiter": ","
    },
    "staging_table": "stg.Vendor",
    "target_table": "zen.Vendor"
  }
}
```

## Tab-Delimited Example

```json
{
  "Invoice": {
    "source_table": "raw.Invoice",
    "preload_profile": {
      "source_files": ["<raw-data-root>\\Invoice.csv"],
      "column_mappings": {
        "Invoice": "Invoice",
        "Vendor": "Vendor"
      },
      "max_malformed_rows": 0,
      "has_header": true,
      "delimiter": "\t"
    },
    "staging_table": "stg.Invoice",
    "target_table": "zen.Invoice"
  }
}
```

## Workday Report Export Example

```json
{
  "SupplierPayments": {
    "source_table": "raw.SupplierPayments",
    "preload_profile": {
      "source_files": ["<raw-data-root>\\Supplier Payments.csv"],
      "column_mappings": {
        "Transaction_Reference": "Transaction_Reference",
        "Payment_Date": "Payment_Date",
        "Payment_Status": "Payment_Status"
      },
      "max_malformed_rows": 0,
      "has_header": true,
      "delimiter": ",",
      "canonicalize_headers": true,
      "header_row_number": 8
    },
    "staging_table": "stg.SupplierPayments",
    "target_table": "zen.SupplierPayments"
  }
}
```

## Multi-File Same Table Example

```json
{
  "Voucher": {
    "source_table": "raw.Voucher",
    "preload_profile": {
      "source_files": [
        "<raw-data-root>\\Voucher_1.csv",
        "<raw-data-root>\\Voucher_2.csv"
      ],
      "column_mappings": {
        "Voucher_ID": "Voucher_ID",
        "Vendor_ID": "Vendor_ID"
      },
      "max_malformed_rows": 0,
      "has_header": true,
      "delimiter": ",",
      "canonicalize_headers": true
    },
    "staging_table": "stg.Voucher",
    "target_table": "zen.Voucher"
  }
}
```

## Naming Guidance

- Raw table names may reflect source file families.
- Staging and zen names should be application-friendly.
- Avoid spaces, dashes, periods, and brackets in new target table names when practical.
- Use stable pipeline names that do not depend on file receipt dates unless the date is part of the business contract.

