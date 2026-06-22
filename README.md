# Databricks to BigQuery Validation Agent

This repository is a notebook-first data-quality proof of concept. It validates
Databricks-to-BigQuery migrations and BigQuery-only tables while keeping reusable
business knowledge in a versioned, human-approved BigQuery context store.

The implementation is file-driven and has no UI. Deterministic Python and controlled
SQL perform validation, profiling, reconciliation, tolerance calculations, and
approval routing. A configurable LLM adapter is used only for bounded reasoning.

## Repository Layout

```text
approvals/
  pending/       Workbooks awaiting review
  processed/     Successfully imported workbooks
  approved/      Approval and override receipts
  rejected/      Rejection receipts
  errors/        Import errors
config/
  project.yaml
  llm.yaml
inputs/
  table_mappings.xlsx
  column_mappings.xlsx
  business_context.yaml
  relationships.yaml
  measures.yaml
  human_tests.yaml
  relationship_sample_metadata.yaml
  measure_sample_metadata.yaml
notebooks/
  00_context_store_setup.ipynb
  01_context_management.ipynb
  02_approval_processing.ipynb
  03_relationship_configuration.ipynb
  04_relationship_discovery.ipynb
  05_relationship_validation.ipynb
  06_measure_reconciliation.ipynb
  07_rca_and_context_publication.ipynb
  08_validation_with_context.ipynb
outputs/
  context/<run_id>/
  relationships/<run_id>/
  reconciliation/<run_id>/
  rca/<run_id>/
```

Each notebook writes a timestamped `workflow.log` and `checkpoint.json`. The
checkpoint identifies the last successfully completed step and records full stack
traces for failures.

## Persistent Context

Configure `context_store` in `config/project.yaml` and use Google Application
Default Credentials. The store contains:

- `context_records`: append-only trusted and retired versions
- `context_proposals`: pending create/update proposals
- `approval_decisions`: immutable reviewer decisions
- `trusted_context_current`: latest trusted records
- `pending_context_proposals`: pending proposals

Supported reusable context includes table mappings, column mappings, business
context, human tests, relationships, measures, KPIs, and explicitly selected RCA
learnings. Pending or rejected records are never trusted.

Current explicit configuration wins over trusted context. Approved context wins over
new inference. Changed approved definitions create a proposal against the current
base version; approval creates the next append-only version.

## Approval Workflow

Generated workbooks use these decisions:

- `APPROVE`: publish the proposed payload
- `OVERRIDE`: publish the JSON object in `override_value`
- `REJECT`: record the rejection without publication
- `PENDING`: incomplete; the importer rejects the batch

Review every row and fill `reviewed_by`. Run `approval_processing.ipynb` to recheck
the proposal hash and base version, publish approved records, write receipts, and
move the workbook to `approvals/processed/`.

## Relationships

`inputs/relationships.yaml` defines centralized dimension aliases, custom
relationships, feature flags, composite keys, and parent filters. Relationship
precedence is:

1. Current custom relationship
2. Approved trusted relationship
3. Exact registry relationship
4. Fuzzy inference requiring review

Candidates must pass table, location, column, type, filter, key-width, parent-key
uniqueness, and value-match safeguards. Generated referential-integrity SQL is
read-only and validated before execution.

## Measures And KPIs

`inputs/measures.yaml` supports manual measures and controlled composite expressions:

```yaml
measures:
  - measure_id: total_sales
    pair_id: fact_sales
    source_expression: sales_amount
    target_expression: sales_amount
    aggregation: sum
    tolerance_absolute: 0.01
    tolerance_percentage: 0.5
    date_column:
      source: sales_date
      target: sales_date
    group_by:
      source: [brand_id, market_id]
      target: [brand_sid, market_sid]
    source_filters: []
    target_filters: []
    enabled: true
    approval_status: approved
    publish_to_context: false
```

Expressions may contain column identifiers, numeric literals, parentheses, and
`+ - * /`. Function calls, subqueries, comments, DDL, DML, and arbitrary SQL are
rejected.

Measure precedence is:

1. Approved table-specific manual configuration
2. Approved reusable measure context
3. Existing table business context
4. New high-confidence deterministic inference
5. Low-confidence inference awaiting approval

Inference considers numeric type, measure terminology in names and descriptions,
null and distinct profiles, identifier/status exclusions, and approved column
mappings. Numeric identifiers, years, codes, flags, and sequence fields are not
accepted merely because they are numeric.

## Grouped Reconciliation

Every usable migration measure receives an overall comparison. Additional bounded
groupings come from explicit measure configuration, approved date columns, column
mappings, and useful dimensions such as brand, market, channel, and source system.
The workflow does not generate arbitrary grouping combinations.

Source and target expressions and grouping names may differ. Results record values,
row counts, differences, percentages, tolerances, filters, timestamps, provenance,
and SQL references. Group cardinality and query result sizes are capped.

Outputs include:

- `measure_reconciliation_report.xlsx`
- `measure_reconciliation.csv`
- `failed_measures.csv`
- `execution_errors.csv`
- `measure_rca.csv`
- Per-table JSON reconciliation results

## Controlled Diagnostics And RCA

Failed measure reconciliations can use only configured diagnostic types:

- Date distribution
- Aggregate comparison by an approved dimension
- Null distribution

The LLM may select structured diagnostic intents from this allowlist. It cannot
provide executable SQL, tables, expressions, columns, or filters. Python validates
the intent, maps it to approved measure metadata, generates SQL templates, applies
query safeguards, and executes through read-only connectors.

RCA records contain the failed test, hypotheses, query references, observed source
and target evidence, confidence, classification, explanation, next action, and
review requirement. Classifications distinguish strongly supported causes, possible
causes requiring evidence, and inability to determine. Reusable RCA knowledge is
proposed for approval; one-time incidents are not automatically published.

Diagnostic steps, LLM calls, grouping cardinality, returned rows, query time, and
bytes processed are bounded by `inputs/measures.yaml`, `config/project.yaml`, and
the connector query limits.

## LLM Configuration

`config/llm.yaml` supports Vertex Gemini, OpenAI, Anthropic, or disabled operation.
Provider-specific code remains inside `src/dq_agent/llm.py`.

The LLM may help with semantic column reranking, structured diagnostic planning, and
evidence wording. It never calculates validation status, approves context, or
generates executable SQL.

## Run From VS Code

1. Open the repository and select its `.venv` Python kernel.
2. Configure credentials and run `00_context_store_setup.ipynb`.
3. Run `01_context_management.ipynb` to normalize inputs and create proposals.
4. Review/import workbooks with `02_approval_processing.ipynb`.
5. Use `03_relationship_configuration.ipynb`, `04_relationship_discovery.ipynb`, and
   `05_relationship_validation.ipynb` for dimension checks.
6. Run `06_measure_reconciliation.ipynb` with its offline sample first. Inspect inferred
   measures, precedence, groupings, SQL, comparisons, approvals, and reports.
7. Run `07_rca_and_context_publication.ipynb` to inspect diagnostics, evidence-based RCA,
   and reusable-context proposals.
8. Set the notebooks' sample switches to live mode only after reviewing SQL and
   configuring Databricks, BigQuery, context-store, and LLM credentials.
9. Use `08_validation_with_context.ipynb` for the complete multi-table workflow with
   current configuration and approved context.

No unit-test framework, UI, production orchestrator, unrestricted natural-language
SQL, or automatic publication of unapproved context is included.

## Preparing Inputs

Use Excel or CSV for row-oriented mappings that business and data teams review. Use
YAML for nested configuration such as filters, business meaning, rules, relationships,
and measures. JSON is reserved for machine-generated evidence, manifests, and context
payloads. Parquet is used only for larger profiling output.

### `table_mappings.xlsx`

Add one enabled row per validation target. Adding rows automatically expands the batch;
no code change is required.

Required columns are `pair_id`, `mode`, `target_project`, `target_dataset`, and
`target_table`. Migration rows also require `source_catalog`, `source_schema`, and
`source_table`. `context_id` selects the matching section in `business_context.yaml`.
The old free-form partition-filter columns were removed: structured filters belong in
business context where identifiers, operators, and values can be validated.

### `column_mappings.xlsx`

Use this file for approved source-to-target mappings. Leave it empty when mappings
should be inferred. Manual mappings take precedence and are validated against live
metadata before use. Low-confidence inference creates a review item.

### `business_context.yaml`

Table context supports keys, freshness, filters, SCD2 behavior, relationships,
measures, assumptions, known source systems, and optional column rules:

```yaml
tables:
  fact_sales:
    description: Posted sales transactions.
    primary_key:
      source: [sale_id]
      target: [sale_id]
    audit_columns:
      source: updated_at
      target: updated_at
    freshness_sla_minutes: 1440
    filters:
      source: [{column: transaction_status, operator: eq, value: POSTED}]
      target: [{column: transaction_status, operator: eq, value: POSTED}]
    scd2: {enabled: false}
    columns:
      sales_amount:
        description: Posted sales revenue.
        role: measure
        required: true
        minimum: 0
      transaction_status:
        accepted_values: [POSTED, PENDING, CANCELLED]
      updated_at:
        role: audit
```

`required`, `accepted_values`, `minimum`, and `maximum` generate target-side business
rules. Column descriptions supplement missing warehouse descriptions. A column role can
help identify a primary key or audit column, but profiling evidence is still required
when the decision was not explicitly configured.

### `human_tests.yaml`

Supported types are `row_count`, `null_count`, `distinct_count`, `uniqueness`,
`value_distribution`, `aggregate`, `domain`, `predicate`, `freshness`, `relationship`,
and guarded `custom_sql`. Single-side metric and custom SQL tests can use
`parameters.expected_value`; null checks can use `parameters.allowed`. Migration tests
with `scope: compare` compare source and target evidence. Invalid scopes, missing
columns, missing SQL, and malformed parameters are reported as configuration errors.

## End-to-End Flow

Notebook `08_validation_with_context.ipynb` is the canonical runner:

1. Edit its `RUN_VALIDATION` and `STOP_AFTER` values.
2. Normalize current files and retrieve trusted context when enabled.
3. Inspect the effective context and table list.
4. Run metadata extraction, mapping/key/audit/SCD2 inference, relationship discovery,
   measure selection, rule generation, execution, reconciliation, RCA, and reports.
5. Inspect the displayed table statuses, checkpoint, recent events, and output paths.

Notebooks 03 through 07 remain useful for learning and debugging individual
relationship, measure, and RCA stages. Every table is processed independently. A
low-confidence relationship is skipped and sent for non-blocking review; it does not
prevent unrelated rules from running. Blocking mapping, key, audit, SCD2, or measure
decisions stop only that table.

Migration mode adds bounded mapped-row reconciliation when source and target keys are
known. Up to `small_table_row_limit` rows are held in memory, normalized, and hashed.
Only missing, extra, changed, or duplicate key hashes appear in reports; raw row values
are neither written to evidence files nor sent to the LLM. Larger tables retain count,
key-bucket, distribution, and measure reconciliation and mark exact row comparison
`SKIP`.

BigQuery-only mode profiles supported scalar columns and runs key, required-field,
domain/range, freshness, relationship, and human rules without requiring a source.

## Outputs And Logging

Each run creates `outputs/<run_id>/` with:

- `manifest.json`, `checkpoint.json`, `events.jsonl`, and `run.log`
- `consolidated_report.xlsx`, failures, RCA, freshness, mappings, profiles,
  relationships, row reconciliation, measures, human tests, and execution-error CSVs
- `tables/<pair_id>/validation_report.xlsx` and the table's JSON result
- Stored generated SQL and aggregate/masked evidence for every controlled query

`run.log` includes function and line information. `events.jsonl` is structured and
records stage starts/completions, table and rule identifiers, query IDs, bytes processed
when available, result rows, and errors. `checkpoint.json` identifies the latest
completed or failed workflow stage. Full stack traces are retained in the manifest,
table results, and notebook checkpoints.

## Guardrails And Limitations

- SQL is parsed and restricted to a single read-only query over allowlisted tables.
- BigQuery performs a dry run and enforces `max_bytes_billed`, timeout, and result limits.
- Databricks queries use asynchronous execution and are cancelled after
  `query_limits.timeout_seconds`; result rows are capped.
- The Databricks connector cannot estimate scanned bytes before execution. Enforce cost,
  concurrency, and hard scan policies on the SQL warehouse and use a read-only identity.
- LLMs receive metadata and bounded aggregate evidence, not credentials or executable
  SQL authority. Individual LLM request/response persistence is intentionally deferred.
- Exact mapped-row reconciliation is bounded and is not a replacement for a distributed
  full-table diff on very large tables.
- Automatic SCD2 detection recognizes current flags, common active values, nullable end
  dates, and high-date sentinels. Ambiguous behavior requires review.
- File-only approval without the BigQuery context store, automated tests, and enabled
  live sample mappings are intentionally outside this change.

Lineage is deferred for the POC. It can later be added as an approved YAML/catalog input
when it demonstrably improves relationship inference or RCA; transformation repositories
and Confluence are not assumed.

After migration, keep `mode: bigquery_only` rows and continue using the same business
context, relationships, measures, human tests, profiling, freshness, RCA, and reports.

## Troubleshooting

1. Start with `manifest.json` and the table status.
2. Read `checkpoint.json` to find the last completed stage.
3. Search `events.jsonl` by `pair_id` and `rule_id`.
4. Open `run.log` for function, line, exception, and stack-trace context.
5. Inspect the saved SQL before rerunning a failed query manually with the same read-only
   identity.
6. Treat `WAITING_FOR_REVIEW` as an inference decision, `FAIL` as a data result, and
   `ERROR` as a configuration, connector, query, or execution problem.
