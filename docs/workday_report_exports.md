# Workday Report Exports

Workday often exports report-style CSV files rather than simple table extracts. These files may contain title rows, report criteria, blank separator rows, and then the real header row.

## Symptom

Preload profile reports a huge malformed count and only a few proper rows.

Example pattern:

```text
proper rows: 3
malformed rows: 144,434
```

This usually means the loader treated a title or criteria row as the header.

## Diagnosis

Open the first 30 to 80 records with CSV-aware reading if possible.

Look for:

- report title
- criteria rows
- blank separator rows
- the real field header row
- data rows immediately after the real header

The fix is usually `header_row_number`, not a malformed threshold increase.

## Config Fix

Set:

```json
"canonicalize_headers": true,
"header_row_number": <real-header-row-number>
```

Then build `column_mappings` from the canonicalized header names.

## Example

For a supplier payment history report:

```text
VHC - Supplier Payment History
Company,VHC Health Consolidated
Payment Status
Supplier
Start Date,7/1/2023
End Date
,,,,,,Invoices Paid
Transaction Reference,Payment Date,Payment Status,Supplier,Payment Type,Payment Amount,Supplier Invoice,Supplier's Invoice Number,Invoice Date,Invoice Amount
```

The real header row is row 8.

Config:

```json
{
  "preload_profile": {
    "source_files": ["<raw-data-root>\\Supplier Payments.csv"],
    "column_mappings": {
      "Transaction_Reference": "Transaction_Reference",
      "Payment_Date": "Payment_Date",
      "Payment_Status": "Payment_Status",
      "Supplier": "Supplier",
      "Payment_Type": "Payment_Type",
      "Payment_Amount": "Payment_Amount",
      "Supplier_Invoice": "Supplier_Invoice",
      "Supplier_s_Invoice_Number": "Supplier_s_Invoice_Number",
      "Invoice_Date": "Invoice_Date",
      "Invoice_Amount": "Invoice_Amount"
    },
    "max_malformed_rows": 0,
    "has_header": true,
    "delimiter": ",",
    "canonicalize_headers": true,
    "header_row_number": 8
  }
}
```

## Multi-Line Fields

Workday report exports can contain quoted multi-line address or memo fields. The framework can handle these when:

- the real header row is configured correctly
- the CSV quoting is valid
- the delimiter is correct

Do not split or pre-clean these files with line-based tools unless the CSV parser cannot read them.

## Operational Rule

If a Workday file has massive malformed counts, first find the real header row. Do not raise `max_malformed_rows` until quarantine or header analysis proves the rows are truly malformed.

