# DBK-BQ Validation Agent

A data quality agent that validates Databricks → BigQuery migrations.
It runs a 6-stage pipeline per table pair and produces structured reports.

## Pipeline stages

```
METADATA → INFERENCE → RULES → EXECUTION → RCA → REPORTS
```

| Stage | What it does |
|-------|-------------|
| METADATA | Reads schema + column profiles from source and target |
| INFERENCE | LLM maps columns, infers primary keys and measures; emits approval proposals for anything uncertain |
| RULES | Generates SQL checks from mappings + business context |
| EXECUTION | Runs all SQL checks; records PASS/FAIL per rule |
| RCA | LLM diagnoses each failure; tags root causes vs. cascades |
| REPORTS | Writes summary.md, CSVs, manifest.json, approval_proposals.json |

## How to run (mock/local tests)

```bash
# activate venv first
python tests/run_mock.py
```

LLM config: `config/llm.yaml` — currently Ollama via OpenAI-compatible endpoint (`gemma4:e4b`).

Regenerate mock CSVs (writes to `tests/data/`, gitignored):
```bash
python tests/generate_mock_data.py
```

## Key input files

| File | Purpose |
|------|---------|
| `tests/inputs/table_mappings.csv` | Which table pairs to run (`pair_id, enabled, mode, source_catalog, ...`) |
| `tests/inputs/column_mappings.csv` | Pre-approved column mappings — bypass the LLM approval gate |
| `tests/inputs/measures.yaml` | Pre-approved measure definitions — bypass the LLM approval gate |
| `inputs/business_context.yaml` | Per-table PK, audit columns, filters, column descriptions, accepted values |
| `inputs/human_tests.yaml` | Hand-written SQL assertions added on top of generated rules |
| `inputs/relationships.yaml` | FK relationship candidates for referential integrity checks |

## Critical constraint: business_context.yaml column names

The `columns:` section under each table **must use TARGET column names**, not source names.
The engine validates them against the target schema at runtime and raises `ValueError` if they don't match.

```yaml
# WRONG — source names
columns:
  sku_id: ...
  store_id: ...

# CORRECT — target names
columns:
  SkuID: ...
  store_number: ...
```

## Approval proposals

When INFERENCE is uncertain it writes `approval_proposals.json` in the run output directory.
The table stays in `WAITING_FOR_REVIEW` until all blocking proposals are resolved.

**Auto-approve with the script:**
```bash
python scripts/approve_proposals.py                        # uses latest run
python scripts/approve_proposals.py path/to/proposals.json --dry-run
```

Thresholds (empirically derived):

| Category | Rule |
|----------|------|
| `column_mapping` | score ≥ 0.50 AND margin_over_2nd ≥ 0.15 → auto-approve |
| `measure` | score ≥ 0.70 AND no metadata_errors → auto-approve |
| `primary_key` | **Never auto-approved** — composite keys require business knowledge |

To pre-approve without running the script:
- Column mappings → add rows to `tests/inputs/column_mappings.csv`
- Measures → add entries to `tests/inputs/measures.yaml`
- Primary keys → add `primary_key: {source: [...], target: [...]}` to `inputs/business_context.yaml`

## Output files (per run, tracked in git)

Outputs land in `tests/outputs/<run_id>/`. Heavy files (events.jsonl, run.log, XLSX, parquet) are gitignored.

| File | Contents |
|------|---------|
| `summary.md` | Human-readable narrative of all table results |
| `manifest.json` | Structured run metadata — statuses, rule counts, root_cause_count, cascade_count |
| `failures_with_rca.csv` | Every failed rule with inline root-cause explanation |
| `failed_tests.csv` | Failed rule IDs and raw evidence |
| `rca_report.csv` | RCA records with is_cascade flag |
| `approval_proposals.json` | Pending LLM inference decisions blocking execution |
| `mapping_confidence.csv` | Column mapping scores and margin_over_2nd |
| `measure_reconciliation.csv` | Source vs target measure totals |
| `freshness.csv` | Audit column timestamps per table |

## Test tables

| pair_id | Mode | Expected result | Injected issues |
|---------|------|----------------|-----------------|
| `sample_fact_sales` | migration | WAITING (1 proposal) | sale_id mapping pending |
| `sample_dim_brand_scd2` | migration | FAIL | extra active record, SCD2 drift |
| `sample_dim_market` | migration | PASS | clean |
| `sample_bq_only_table` | bigquery_only | FAIL | duplicate record_ids, stale data |
| `pass_test` | migration | PASS | clean data, renamed columns |
| `rowmismatch_test` | migration | FAIL | target missing ~97 rows |
| `valuemismatch_test` | migration | FAIL | scattered value diffs + future-dated timestamps |

Source columns: `sku_id, store_id, fiscal_week, plan_type, plan_qty, last_updated_date`
Target columns: `SkuID, store_number, fiscal_wk, PlanType, plan_quantity, last_update_ts`

## Connector

`LocalConnector` in `src/dq_agent/connectors.py` — reads CSVs from `LOCAL_DATA_DIR` (default `tests/data/`).
Naming convention: `source_<table>.csv` / `target_<table>.csv`.

## Source layout

```
src/dq_agent/
  workflow.py     # orchestrates all 6 stages
  connectors.py   # LocalConnector (DuckDB) + BigQueryConnector
  reporting.py    # write_run_summary, write_consolidated_reports, failures_with_rca
  llm.py          # LLM client (Ollama / OpenAI-compatible)
  config.py       # config loading
  measures.py     # measure inference and reconciliation
  relationships.py# FK relationship discovery
scripts/
  approve_proposals.py   # auto-approval CLI
tests/
  run_mock.py            # entry point for local runs
  generate_mock_data.py  # regenerates tests/data/ CSVs
  inputs/                # table_mappings, column_mappings, measures
  outputs/               # run results (CSVs + summary tracked in git)
inputs/                  # business_context.yaml, human_tests.yaml, relationships.yaml
config/
  llm.yaml               # LLM provider config (currently Ollama gemma4:e4b)
```
