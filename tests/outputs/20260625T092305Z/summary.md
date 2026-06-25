# DQ Run: 20260625T092305Z
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
- `row_count`: The data quality check failed because the row count for `sample_dim_brand_scd2` did not match between the source (30 rows) and the target (31 rows), resulting in a difference of 1 row. Additionally, a
- `distinct__brand_sid` *(cascade)*: The data quality check failed because the distinct count of `brand_sid` in the target dataset (31) is different from the distinct count in the source dataset (30). Specifically, the target dataset has
- `distinct__brand_name` *(cascade)*: The data quality check failed because the distinct count of `brand_name` in the target dataset (31) is different from the distinct count in the source dataset (30). The allowed difference was 0.0, and
- `distribution__brand_name`: The data quality check failed because the distribution of `brand_name` values differs between the source and target datasets. Specifically, 8 unique values were found to be present in one dataset but 
- `distribution__active_indicator`: The data quality check failed because the distribution of the 'active_indicator' column differs between the source and target datasets. Specifically, the value corresponding to hash 'b326b5062b2f0e690
- `distribution__source_system`: The data quality check failed because the distribution of values in the `source_system` column differs between the source and target datasets. Specifically, the value corresponding to hash `0f131be311
- `distinct__updated_at` *(cascade)*: The data quality check failed because the distinct count of the `updated_at` column in the target dataset (2) did not match the distinct count in the source dataset (1).
- `key_buckets`: The key bucket check failed because the target dataset contains a key bucket ('b7') that is not present in the source dataset. The comparison shows 1 mismatch, specifically for key bucket 'b7', which 
- `exact_keys_when_small`: The data quality check failed because the number of unique keys in the source table (30) did not match the number of unique keys in the target table (31). Specifically, the target table contains an ex
- `mapped_rows_when_small`: The data quality check failed because the target table (`sample_dim_brand_scd2`) contained one extra record (identified by hash `7382bd10b8f683f5cd8bfcc32d0601cc12ed0fe657d3e9af182620f6370604f1`) that

---

## [PASS] sample_dim_market
24 rules — 24 passed

---

## [FAIL] sample_bq_only_table
9 rules — 7 passed, **2 failed**

**Failures:**
- `target_key_unique`: The data quality check failed because the target column `record_id` in the `sample_bq_only_table` is not unique. Specifically, there are 2.0 duplicate groups and 2.0 duplicate rows found in the target
- `freshness`: The data quality check failed because the target table, `sample_bq_only_table`, is not fresh. The maximum timestamp found in the `updated_at` column is `2026-06-20T06:32:12`, which is significantly ol

---

## [ERROR] pass_test

---

## [ERROR] rowmismatch_test

---

## [ERROR] valuemismatch_test

---

*Key files:*
- `failures_with_rca.csv` — every failed check with its root cause explanation inline
- `consolidated_report.xlsx` — full results across all tables
- `data_profiling.csv` — column-level statistics
- `approval_proposals.json` — pending decisions blocking rule execution