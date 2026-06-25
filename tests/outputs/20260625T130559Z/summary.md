# DQ Run: 20260625T130559Z
**Project**: mock_test  
**Status**: RUNNING  

---

## [WAITING FOR REVIEW] sample_fact_sales

Rules did not execute — 1 pending decision(s) required.

**Pending decisions:**
- Approve column mapping: **sale_id** (source) → **sale_id** (target)? [confidence: 73%]

---

## [FAIL] sample_dim_brand_scd2
29 rules — 19 passed, **10 failed**
*7 root cause(s), 3 cascading signal(s)*

**Failures:**
- `row_count`: Filtered source and target row counts differ. Review date coverage and applied filters.
- `distinct__brand_sid` *(cascade)*: The mapped source and target metrics differ after configured filters and tolerances.
- `distinct__brand_name` *(cascade)*: The mapped source and target metrics differ after configured filters and tolerances.
- `distribution__brand_name`: The rule failed; available evidence does not establish a unique root cause.
- `distribution__active_indicator`: The rule failed; available evidence does not establish a unique root cause.
- `distribution__source_system`: The rule failed; available evidence does not establish a unique root cause.
- `distinct__updated_at` *(cascade)*: The mapped source and target metrics differ after configured filters and tolerances.
- `key_buckets`: The rule failed; available evidence does not establish a unique root cause.
- `exact_keys_when_small`: The rule failed; available evidence does not establish a unique root cause.
- `mapped_rows_when_small`: The rule failed; available evidence does not establish a unique root cause.

---

## [PASS] sample_dim_market
24 rules — 24 passed

---

## [FAIL] sample_bq_only_table
9 rules — 7 passed, **2 failed**

**Failures:**
- `target_key_unique`: Duplicate groups exist for the configured or inferred key.
- `freshness`: The configured audit timestamps exceed the allowed freshness lag.

---

## [PASS] pass_test
42 rules — 43 passed

---

## [FAIL] rowmismatch_test
42 rules — 32 passed, **11 failed**
*8 root cause(s), 2 cascading signal(s)*

**Failures:**
- `row_count`: Filtered source and target row counts differ. Review date coverage and applied filters.
- `distinct__SkuID` *(cascade)*: The mapped source and target metrics differ after configured filters and tolerances.
- `distribution__SkuID`: The rule failed; available evidence does not establish a unique root cause.
- `distinct__store_number` *(cascade)*: The mapped source and target metrics differ after configured filters and tolerances.
- `distribution__store_number`: The rule failed; available evidence does not establish a unique root cause.
- `distribution__PlanType`: The rule failed; available evidence does not establish a unique root cause.
- `distinct__plan_quantity`: The mapped source and target metrics differ after configured filters and tolerances.
- `key_buckets`: The rule failed; available evidence does not establish a unique root cause.
- `exact_keys_when_small`: The rule failed; available evidence does not establish a unique root cause.
- `mapped_rows_when_small`: The rule failed; available evidence does not establish a unique root cause.
- `measure__plan_qty__overall`: 

---

## [FAIL] valuemismatch_test
42 rules — 38 passed, **5 failed**
*2 root cause(s), 2 cascading signal(s)*

**Failures:**
- `distinct__plan_quantity` *(cascade)*: The mapped source and target metrics differ after configured filters and tolerances.
- `distinct__last_update_ts` *(cascade)*: The mapped source and target metrics differ after configured filters and tolerances.
- `mapped_rows_when_small`: The rule failed; available evidence does not establish a unique root cause.
- `freshness`: The configured audit timestamps exceed the allowed freshness lag.
- `measure__plan_qty__overall`: 

---

*Key files:*
- `failures_with_rca.csv` — every failed check with its root cause explanation inline
- `consolidated_report.xlsx` — full results across all tables
- `data_profiling.csv` — column-level statistics
- `approval_proposals.json` — pending decisions blocking rule execution