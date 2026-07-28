# Individual and phenotype input

The workstation stores phenotype information on an **individual**, then links
that individual to one or more VCF sample IDs. This avoids duplicating the same
clinical record when an individual has multiple sequencing runs or reannotated
VCFs.

This release does not map text to HPO, perform phenotype-based prioritization,
or infer genetic ancestry.

## Fields

`individual_id` is required. All other fields are optional:

- one or more `sample_ids`;
- sex at birth;
- age at evaluation and unit;
- age at onset and unit;
- reported race;
- reported ethnicity;
- phenotype summary;
- present features;
- absent features / pertinent negatives;
- current diagnosis;
- notes; and
- source date.

Reported race and reported ethnicity are stored independently and are never
treated as genetic ancestry or used to choose a population-frequency filter.
Multiple values are supported.

## Manual entry

Open **Phenotypes → Manual entry**. Use semicolons to separate multiple sample
IDs, reported race/ethnicity values, or phenotype features. The individual
record becomes visible automatically in the detail page of variants whose VCF
sample ID is linked to that individual.

## Bulk input

Supported input formats are `.csv`, `.tsv`, and `.xlsx`. Legacy `.xls`,
macro-enabled workbooks, PDFs, and Word documents are intentionally excluded.

The import flow:

1. Detects the file type, delimiter, workbook sheets, and likely header row.
2. Displays the source rows before import.
3. Suggests mappings for common column names.
4. Requires confirmation of the individual-ID mapping.
5. Validates exact VCF sample matches and reports unmatched, ambiguous, and
   duplicate identifiers.
6. Imports only after validation.

Unmatched sample links are retained so they can match a VCF added later.
Duplicate individual IDs and ambiguous sample IDs must be resolved first.
Matching is exact and case-sensitive; case-insensitive possibilities are shown
only as suggestions.

Unmapped nonempty columns are preserved as custom individual attributes by
default. Mapping configurations can be named and reused as lab import profiles.

When an individual already exists, the importer can:

- update only fields that are nonblank in the input (default);
- replace the complete record; or
- leave the existing record unchanged.

Each import records its source filename, SHA-256 digest, timestamp, mapping,
row counts, and create/update/skip counts in the local SQLite database. Input
files and phenotype data are not sent to an external service.
