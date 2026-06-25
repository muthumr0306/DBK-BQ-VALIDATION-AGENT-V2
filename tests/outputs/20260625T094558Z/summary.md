# DQ Run: 20260625T094558Z
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
- `row_count`: The row count check for `sample_dim_brand_scd2` failed because the source had 30 rows while the target had 31 rows. Additionally, the date coverage check for `sample_dim_brand_scd2` failed because the
- `distinct__brand_sid` *(cascade)*: The data quality check failed because the distinct count of `brand_sid` in the target dataset (31) is different from the distinct count in the source dataset (30). Specifically, the target dataset has
- `distinct__brand_name` *(cascade)*: The data quality check failed because the distinct count of `brand_name` in the target dataset (31) is different from the distinct count in the source dataset (30). Specifically, the target has one mo
- `distribution__brand_name`: The data quality check failed because the distribution of `brand_name` values in the source and target datasets does not match. Specifically, there are 16 mismatches in the value distribution comparis
- `distribution__active_indicator`: The data quality check failed because the distribution of the 'active_indicator' column differs between the source and target datasets. Specifically, the value associated with hash 'b326b5062b2f0e6904
- `distribution__source_system`: The data quality check failed because the distribution of values in the `source_system` column for the `sample_dim_brand_scd2` pair differs between the source and target systems. Specifically, the val
- `distinct__updated_at` *(cascade)*: The data quality check failed because the distinct count of the `updated_at` column in the target dataset (2) does not match the distinct count in the source dataset (1).
- `key_buckets`: The key bucket check failed because the target dataset contains a key bucket ('b7') that is missing in the source dataset. Specifically, the comparison found 1 mismatch where 'b7' exists in the target
- `exact_keys_when_small`: The data quality check failed because the number of unique keys in the source table (30) did not match the number of unique keys in the target table (31). Specifically, the target table contains one e
- `mapped_rows_when_small`: The data quality check failed because the target table (`sample_dim_brand_scd2`) contained one extra key hash that was not present in the source table. Specifically, the target had 31 rows while the s

---

## [PASS] sample_dim_market
24 rules — 24 passed

---

## [FAIL] sample_bq_only_table
9 rules — 7 passed, **2 failed**

**Failures:**
- `target_key_unique`: The data quality check failed because the target column `record_id` in the `sample_bq_only_table` is not unique. Specifically, there are 2.0 duplicate groups and 2.0 duplicate rows identified in the t
- `freshness`: The data quality check failed because the target table, `sample_bq_only_table`, is not fresh. The maximum timestamp found in the target table is `2026-06-20T06:32:12`, which is significantly older tha

---

## [PASS] pass_test
42 rules — 43 passed

---

## [FAIL] rowmismatch_test
42 rules — 32 passed, **11 failed**
*8 root cause(s), 2 cascading signal(s)*

**Failures:**
- `row_count`: The data quality check failed because the row count of the source data (1200) did not match the row count of the target data (1103). The difference is 97 rows, and the allowed difference was 0.0.
- `distinct__SkuID` *(cascade)*: The data quality check failed because the distinct count of `sku_id` in the source data (10) did not match the distinct count of `SkuID` in the target data (11).
- `distribution__SkuID`: The data quality check failed because the distribution of `sku_id` (source) and `SkuID` (target) columns does not match. Specifically, there are mismatches in the counts of certain values between the 
- `distinct__store_number` *(cascade)*: The data quality check failed because the distinct count of `store_id` in the source data (5) does not match the distinct count of `store_number` in the target data (6).
- `distribution__store_number`: The data quality check failed because the distribution of values in the source column (`store_id`) does not match the distribution of values in the target column (`store_number`). Specifically, there 
- `distribution__PlanType`: The data-quality check failed because the distribution of values in the `plan_type` column (source) does not match the distribution of values in the `PlanType` column (target). Specifically, two value
- `distinct__plan_quantity`: The data quality check failed because the number of distinct values in the source column `plan_qty` (1184) does not match the number of distinct values in the target column `plan_quantity` (1088). Spe
- `key_buckets`: The data sets are largely consistent, but there are notable discrepancies in the counts and specific records for certain time periods and categories. The discrepancies suggest potential data ingestion
- `exact_keys_when_small`: The data quality check failed because the number of unique keys in the source table (1200) does not match the number of unique keys in the target table (1103). Specifically, there are 97 missing key h
- `mapped_rows_when_small`: The data quality check failed because there was a mismatch in the number of records found when comparing the source and target datasets. Specifically, the source dataset had 1200 records, while the ta
- `measure__plan_qty__overall`: 

---

## [FAIL] valuemismatch_test
42 rules — 38 passed, **5 failed**
*2 root cause(s), 2 cascading signal(s)*

**Failures:**
- `distinct__plan_quantity` *(cascade)*: The data quality check failed because the distinct count of the `plan_qty` column in the source data (1184) does not match the distinct count of the `plan_quantity` column in the target data (1185). T
- `distinct__last_update_ts` *(cascade)*: The data quality check failed because the distinct count of the `last_updated_date` column in the source data (12) does not match the distinct count of the `last_update_ts` column in the target data (
- `mapped_rows_when_small`: The data quality check failed because 123 key hashes were found to be changed between the source and target systems. This indicates a mismatch in the data records based on the defined keys.
- `freshness`: The data quality check failed because the source column (`last_updated_date`) is significantly older than the target column (`last_update_ts`), exceeding the allowed freshness tolerance of 1440 minute
- `measure__plan_qty__overall`: 

---

*Key files:*
- `failures_with_rca.csv` — every failed check with its root cause explanation inline
- `consolidated_report.xlsx` — full results across all tables
- `data_profiling.csv` — column-level statistics
- `approval_proposals.json` — pending decisions blocking rule execution