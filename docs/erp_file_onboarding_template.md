# ERP File Onboarding Template

Use this template before adding a new pipeline for an inbound ERP file. The goal is to define the file contract clearly before creating config, SQL, and runtime pipeline assets.

## Blank Template

### Source Overview
- Source file name:
- Business purpose:
- Grain of the file:
- Inbound path:
- Delimiter:
- Has header:

### File Dependency
- Upstream dependency:
- Downstream dependency:
- Can onboard independently:

### Target Layers
- Raw table:
- Staging table:
- Zen table:

### Load Strategy
- Full replace or append:
- Expected frequency:
- Historical or current-state:
- Natural business key:

### Profiling Rules
- Column mappings:

```json
{
}
```

### Data Quality Expectations
- Likely data type issues:
- Likely malformed row risks:
- Likely join keys:

### Notes
- Notes / unknowns:
- Onboarding status:

### Business Risk
- What breaks if wrong:
- Validation priority:

---

## Example: Customer

### Source Overview
- Source file name: `customer.csv`
- Business purpose: customer master data used to load trusted customer records
- Grain of the file: one row per customer
- Inbound path: `<raw-data-root>\customer.csv`
- Delimiter: `,`
- Has header: `true`

### File Dependency
- Upstream dependency: client ERP customer master export
- Downstream dependency: customer dimensions, customer joins in sales, receivables, and invoice reporting
- Can onboard independently: yes

### Target Layers
- Raw table: `raw.Customer`
- Staging table: `stg.Customer`
- Zen table: `zen.Customer`

### Load Strategy
- Full replace or append: full replace
- Expected frequency: daily or as-needed refresh
- Historical or current-state: current-state
- Natural business key: `CustomerID`

### Profiling Rules
- Column mappings:

```json
{
  "CustomerID": "CustomerID",
  "FullName": "FullName",
  "City": "City",
  "LoadDate": "LoadDate"
}
```

### Data Quality Expectations
- Likely data type issues: `CustomerID` should cast to `INT`; `LoadDate` may arrive as datetime text and must cast cleanly to `DATE`
- Likely malformed row risks: missing trailing `LoadDate`, extra delimiter in free-text fields, blank `FullName`
- Likely join keys: `CustomerID`

### Notes
- Notes / unknowns: decide later whether blank text values should remain nullable in `stg` / `zen` or be defaulted during transformation
- Onboarding status: active example pipeline

### Business Risk
- What breaks if wrong: customer joins fail, customer-level reporting becomes inaccurate, and downstream sales or receivables analysis can misstate results
- Validation priority: high

---

## Example: Vendor

### Source Overview
- Source file name: `vendor.csv`
- Business purpose: vendor master data used to load trusted vendor records
- Grain of the file: one row per vendor
- Inbound path: `<raw-data-root>\vendor.csv`
- Delimiter: `,`
- Has header: `true`

### File Dependency
- Upstream dependency: client ERP vendor master export
- Downstream dependency: vendor dimensions, purchasing, AP, and payment reporting joins
- Can onboard independently: yes

### Target Layers
- Raw table: `raw.Vendor`
- Staging table: `stg.Vendor`
- Zen table: `zen.Vendor`

### Load Strategy
- Full replace or append: full replace
- Expected frequency: daily or as-needed refresh
- Historical or current-state: current-state
- Natural business key: `VendorID`

### Profiling Rules
- Column mappings:

```json
{
  "VendorID": "VendorID",
  "VendorName": "VendorName",
  "City": "City",
  "LoadDate": "LoadDate"
}
```

### Data Quality Expectations
- Likely data type issues: `VendorID` should cast to `INT`; `LoadDate` may arrive as datetime text and must cast cleanly to `DATE`
- Likely malformed row risks: missing `VendorName`, wrong column count, blank `LoadDate`
- Likely join keys: `VendorID`

### Notes
- Notes / unknowns: vendor naming may vary by source system, and future files may require source-to-target header remapping
- Onboarding status: active example pipeline

### Business Risk
- What breaks if wrong: vendor joins fail, purchasing and AP reporting become inaccurate, and payment analysis can tie to the wrong vendor or miss vendors entirely
- Validation priority: high
