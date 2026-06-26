# LLM Integration Improvements

Ranked by expected impact on agent quality. Each item states what currently happens,
what the LLM-powered version looks like, and the exact files/functions to touch.

---

## 1. Agentic diagnostic loop for standard failures

**Priority: Critical**

### Current behavior
`_perform_rca` (workflow.py:2633) runs 1–2 hardcoded diagnostic queries
(date coverage if audit column is known, top-duplicates if uniqueness failure),
then hands everything to the LLM in one shot to narrate.
The LLM cannot ask for more evidence.

### Target behavior
Mirror what measure failures already do:

```
standard failure
  → LLM selects diagnostic intents from allowlist   (_plan_standard_diagnostics)
  → code compiles + executes each diagnostic SQL     (existing _controlled_execute)
  → LLM synthesizes evidence into RCA conclusion    (existing RCAResponse)
```

### Diagnostic allowlist for standard failures
| intent | applies to rule types | what it runs |
|---|---|---|
| `sample_mismatched_keys` | key_buckets, key_values | rows in source but not target and vice versa |
| `date_range_distribution` | row_count, freshness | GROUP BY month/week on audit column |
| `top_null_columns` | null_count | columns with highest null % |
| `top_duplicate_keys` | uniqueness | already exists — keep it |
| `orphan_sample` | relationship | sample of child rows with no parent match |
| `value_distribution_diff` | value_distribution | top mismatched value buckets |
| `date_coverage` | row_count, freshness | already exists — keep it |

### What to build
1. `DiagnosticPlanResponse`-equivalent for standard failures — reuse the existing
   Pydantic model or create `StandardDiagnosticPlanResponse`.
2. `_plan_standard_diagnostics(rule, failure, audit, filters)` — mirrors
   `_plan_measure_diagnostics`; calls LLM with rule type + failure evidence +
   allowlist, returns ordered list of intents.
3. `_execute_standard_diagnostic(intent, rule, pair, filters, table_dir)` — compiles
   and runs the SQL for each intent, returns a result dict.
4. Replace the hardcoded diagnostic block inside `_perform_rca` with the loop:
   plan → execute each → collect evidence → one LLM RCA call with full evidence.

### Files
- `src/dq_agent/workflow.py` — `_perform_rca`, new helpers
- Optionally `src/dq_agent/measures.py` — extract shared `DiagnosticRequest` model
  if it isn't already importable (it already is via `measures.py`)

---

## 2. LLM-driven SCD and current-record filter inference

**Priority: High**

### Current behavior
`_resolve_scd_and_filters` (workflow.py:1189) is pure regex and string matching:
- checks table/column description for literal "scd" or "slowly changing"
- checks column names against hardcoded sets (`CURRENT_FLAG_NAMES`, `end_names`, `start_names`)
- profiles the end-date or flag column to infer the filter value

Tables with non-standard naming or verbose descriptions that imply SCD semantics
fall through to no filter at all.

### Target behavior
When the deterministic path has low confidence (no exact name match, flag not found,
no end-date column), call LLM with:
- table description
- full column list with types and descriptions
- the candidate columns already scored by the heuristic

LLM returns a `SCDFilterProposal` (new Pydantic model):
```python
class SCDFilterProposal(BaseModel):
    scd_detected: bool
    filter_column: str | None
    operator: str          # "eq", "is_null", etc.
    value: Any | None
    rationale: str
    confidence: float
```

High-confidence proposals auto-apply. Low-confidence go to `approval_proposals.json`.

### Files
- `src/dq_agent/workflow.py` — `_resolve_scd_and_filters`, new `SCDFilterProposal`
  model near the top

---

## 3. LLM assist for primary key inference

**Priority: High**

### Current behavior
`_resolve_keys` (workflow.py:710) selects candidates by name suffix (`id`, `key`,
`sid`, `code`, `number`, `num`), profiles uniqueness for each, picks the highest-
scoring single column or combination. When multiple columns are close in uniqueness
score (common in denormalized tables), the choice is arbitrary.

### Target behavior
After profiling, if no single column achieves 100% uniqueness AND the top candidate
confidence is below `auto_accept`, send the scored candidates + table description +
column descriptions to LLM:

```python
class PKInferenceResponse(BaseModel):
    source_columns: list[str]
    target_columns: list[str]
    rationale: str
    confidence: float
```

The LLM knows what `sale_id`, `transaction_number`, and `record_key` mean in context;
the uniqueness profiler does not.

### Constraint
Primary keys are **never auto-approved** per current thresholds (CLAUDE.md).
LLM output should always go to `approval_proposals.json` — use it to improve the
proposal quality, not to skip the human gate.

### Files
- `src/dq_agent/workflow.py` — `_resolve_keys`, new `PKInferenceResponse` model

---

## 4. Run-level LLM synthesis in the summary report

**Priority: Medium**

### Current behavior
`write_run_summary` (reporting.py) produces a template-driven `summary.md`.
It iterates table results and formats sections. No cross-table pattern reasoning.

### Target behavior
After all tables are processed, pass the full `manifest` + `table_outputs` to LLM
for a cross-table summary:

```python
class RunSynthesisResponse(BaseModel):
    headline: str                      # one sentence, e.g. "3 of 7 tables failed"
    dominant_pattern: str              # e.g. "missing dimension keys in 2 fact tables"
    recommended_actions: list[str]     # ordered, actionable
    safe_to_promote: bool
    safe_to_promote_rationale: str
```

This becomes the top section of `summary.md` before the per-table breakdown.

### What to build
- Add an LLM call in `DQWorkflow.run()` (workflow.py:229) after `write_consolidated_reports`,
  before `write_run_summary`, or pass the synthesis into `write_run_summary` as an
  optional argument.
- Keep `write_run_summary` in `reporting.py` deterministic — only pass the synthesis
  string in as an optional `narrative` parameter.

### Files
- `src/dq_agent/workflow.py` — `run()`
- `src/dq_agent/reporting.py` — `write_run_summary` signature

---

## 5. Semantic measure inference

**Priority: Medium**

### Current behavior
`infer_measure_candidates` (measures.py) is purely structural: finds numeric columns
that aren't part of the primary key, scores them by data type and column name tokens
(`qty`, `amount`, `count`, `sum`, `revenue`, etc.).

It cannot infer that `plan_quantity` should be reconciled by `store_number × fiscal_wk`
without that grouping being pre-configured.

### Target behavior
After the structural pass produces candidates, call LLM with column list + business
context + existing grouping dimensions:

```python
class MeasureInferenceResponse(BaseModel):
    measures: list[dict]    # same shape as measure config entries
```

LLM can reason: "plan_quantity is a planning measure; store_number and fiscal_wk are
the natural grain." High-confidence inferences run; low-confidence go to proposals.

This closes the gap between tables that have pre-approved `measures.yaml` entries
and tables where the LLM has to infer everything fresh.

### Files
- `src/dq_agent/measures.py` — `infer_measure_candidates`
- `src/dq_agent/workflow.py` — `_resolve_measures`

---

## 6. Column mapping with table-level semantic context

**Priority: Low**

### Current behavior
`_resolve_mappings` (workflow.py:585) calls LLM when deterministic score < threshold,
but passes only the individual source column + top-5 target candidates. The LLM has
no knowledge of what table this is, what domain it belongs to, or what the other
already-resolved mappings look like.

### Target behavior
Batch unresolved mappings into a single LLM call (instead of one call per column)
and include:
- table description and context
- already-resolved mappings (so LLM understands the naming pattern)
- all unresolved source columns + their candidates in one prompt

This is both cheaper (fewer LLM calls) and more accurate (cross-column coherence).

### Files
- `src/dq_agent/workflow.py` — `_resolve_mappings`

---

## Architectural principle to maintain

All improvements follow the same pattern the measure path already uses:

```
deterministic score / heuristic
  → if confidence < threshold AND llm.enabled
    → LLM selects / plans (from bounded allowlist or structured schema)
    → code executes (SQL compiled by SQLCompiler, validated by QueryGuard)
    → LLM synthesizes result
  → if result.confidence >= auto_accept → apply
  → else → approval_proposals.json
```

LLM never writes SQL directly. `QueryGuard` stays as the execution boundary.

---

# Cross-Cloud Full Row Comparison (Databricks → BigQuery)

Not an LLM improvement — a connector/engine improvement that unlocks more meaningful
evidence for the LLM to reason over.

---

## Feasibility

Yes, but requires a tiered strategy. "Full row-by-row" means different things at
different scales and the approach that works at 1M rows breaks at 1B.

### What the codebase already has

| Rule type | How it works | Scale limit |
|---|---|---|
| `row_reconciliation` | Pull both sides to Python, hash key→values, diff maps | `small_table_row_limit` (config) |
| `key_values` | Pull key hashes only, diff sets | Same limit |
| `key_buckets` | GROUP BY hash bucket in-warehouse, compare counts per bucket | Unbounded |

`row_reconciliation` is already row-by-row comparison — it's just bounded. Removing
the bound is where the real constraints live.

---

## Constraints when wiring up real Databricks + BigQuery

### 1. You cannot join across clouds in SQL
`QueryGuard` correctly enforces single-side queries. There's no cross-cloud SQL join —
you must either pull both result sets to Python and diff there, or stage one side into
the other cloud.

### 2. Egress cost is the hard ceiling

| Scale | ~Data transferred | Egress cost (AWS/Azure → GCP) |
|---|---|---|
| 10M rows × 20 cols | 5–15 GB | $0.40–$1.80 |
| 100M rows | 50–150 GB | $4–$18 |
| 1B rows | 500 GB–1.5 TB | $40–$180 per run |

At large scale this becomes a finance decision, not a code decision.

### 3. Type system mismatches cause false positives in hash comparison

`_canonical_value` (workflow.py:2456) already handles most of this — normalizes
floats, decimals, timestamps to UTC, bytes to hex. Real-world divergences that
still need explicit handling:

| Issue | Databricks | BigQuery |
|---|---|---|
| Timestamp timezone | Session timezone (configurable) | UTC default |
| FLOAT vs NUMERIC | IEEE 754 double | Fixed scale — `12.00999...` ≠ `12.01` |
| String trailing spaces | Preserved | Sometimes stripped depending on load path |
| NULL in structs/arrays | Neither handles identically | — |

These need explicit normalization in `_canonical_value` before hashing.

### 4. Python as comparison engine doesn't scale

`_execute_row_reconciliation` pulls both DataFrames into Python memory. At 10M rows
this hits orchestrator memory limits. The fix: push hashing into the warehouse, pull
only `(pk, row_hash)` tuples — 2 columns instead of 20.

```sql
-- Databricks
SELECT SHA2(CONCAT_WS('|', col1, col2, ...), 256) AS row_hash, pk FROM table

-- BigQuery
SELECT TO_HEX(SHA256(TO_JSON_STRING(STRUCT(col1, col2, ...)))) AS row_hash, pk FROM table
```

At 100M rows that's ~3 GB transferred instead of ~30 GB.

---

## Tiered strategy

```
row count < small_table_row_limit
  → row_reconciliation (current): pull all rows, compare in Python

row count < large_table_threshold  (new config key)
  → push-down hashing: compute row_hash in each warehouse
  → pull only (pk, row_hash) pairs
  → diff hash sets in Python

row count > large_table_threshold
  → key_buckets (already exists): statistically covers mismatches without full pull
  → optional: GCS staging — export Databricks → GCS → load into BQ → EXCEPT DISTINCT
```

GCS staging is what major DQ tools (Dataplex, Great Expectations with Spark) use for
true cross-cloud full comparison. Accurate but adds pipeline complexity and latency.

---

## What needs to change in the codebase

| File | Change |
|---|---|
| `connectors.py` | Real `DatabricksConnector`; both connectors expose `row_hash_select(pk_cols, value_cols)` push-down SQL |
| `query_engine.py` | `SQLCompiler.row_hash_select()` method per dialect |
| `workflow.py:_execute_row_reconciliation` | Dispatch: small → current path, medium → hash push-down, large → key_buckets |
| `workflow.py:_canonical_value` | Explicit FLOAT/NUMERIC and timezone normalization rules for real warehouses |
| `config.py` / project YAML | Add `large_table_threshold` alongside existing `small_table_row_limit` in `query_limits` |

The broader architecture (scope="compare" rules, `QueryGuard`, `_controlled_execute`)
does not need to change — it's a new dispatch path inside `_execute_row_reconciliation`
and better SQL generation in the connectors.
