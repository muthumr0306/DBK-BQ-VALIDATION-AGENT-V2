# DQ Run: 20260629T105555Z
**Project**: mock_test  
**Status**: RUNNING  

---

## [WAITING FOR REVIEW] gdp_inv_loc

Rules did not execute — 13 pending decision(s) required.

**Pending decisions:**
- Approve column mapping: **gdp_processed_timestamp** (source) → **insert_dttm** (target)? [confidence: 44%]
- Approve column mapping: **gdp_last_processed_by** (source) → **updated_by** (target)? [confidence: 57%]
- Approve column mapping: **epoch_id** (source) → **channel_sid** (target)? [confidence: 50%]
- Approve column mapping: **latency** (source) → **alternative_location_sid** (target)? [confidence: 49%]
- Approve column mapping: **brand_code** (source) → **default_supply_node** (target)? [confidence: 50%]
- Approve column mapping: **market_code** (source) → **accounts_payable_code** (target)? [confidence: 52%]
- Approve column mapping: **channel_code** (source) → **accounts_payable_code** (target)? [confidence: 55%]
- Approve column mapping: **location_name** (source) → **location_type** (target)? [confidence: 66%]
- Approve column mapping: **location_status** (source) → **location_type** (target)? [confidence: 64%]
- Approve column mapping: **created_on** (source) → **update_dttm** (target)? [confidence: 48%]
- Approve column mapping: **created_by** (source) → **inserted_by** (target)? [confidence: 63%]
- Approve column mapping: **failed_rule_id** (source) → **inserted_by** (target)? [confidence: 41%]
- Approve column mapping: **dq_processed_timestamp** (source) → **insert_dttm** (target)? [confidence: 44%]

---

## [ERROR] gdp_mc_cc

---

## [ERROR] gdp_mc_sku

---

## [ERROR] gdp_division

---

## [ERROR] gdp_style

---

## [WAITING FOR REVIEW] gdp_daily_inv

Rules did not execute — 19 pending decision(s) required.

**Pending decisions:**
- Approve column mapping: **gdp_processed_timestamp** (source) → **source_last_update_timestamp** (target)? [confidence: 61%]
- Approve column mapping: **gdp_last_processed_by** (source) → **updated_by** (target)? [confidence: 57%]
- Approve column mapping: **epoch_id** (source) → **inventory_position_id** (target)? [confidence: 51%]
- Approve column mapping: **latency** (source) → **last_received_qty** (target)? [confidence: 53%]
- Approve column mapping: **virtual_warehouse_location** (source) → **virtual_warehouse_location_number** (target)? [confidence: 72%]
- Approve column mapping: **location_id** (source) → **location_sid** (target)? [confidence: 46%]
- Approve column mapping: **sku_id** (source) → **sku_sid** (target)? [confidence: 43%]
- Approve column mapping: **customer_choice_id** (source) → **customer_choice_sid** (target)? [confidence: 64%]
- Approve column mapping: **style_id** (source) → **style_sid** (target)? [confidence: 45%]
- Approve column mapping: **failed_rule_id** (source) → **updated_by** (target)? [confidence: 42%]
- Approve column mapping: **dq_processed_timestamp** (source) → **insert_dttm** (target)? [confidence: 44%]
- [measure] available_cost: confidence 80%
- [measure] average_cost: confidence 80%
- [measure] current_price: confidence 80%
- [measure] customer_backorder_cost: confidence 80%
- [measure] customer_reserved_cost: confidence 80%
- [measure] effective_price: confidence 80%
- [measure] expected_cost: confidence 80%
- [measure] in_progress_sale_cost: confidence 80%

---

## [ERROR] gdp_location

---

## [ERROR] gdp_fg_color

---

## [ERROR] gdp_bmc

---

## [ERROR] gdp_size

---

## [WAITING FOR REVIEW] ri1_cc_sku

Rules did not execute — 8 pending decision(s) required.

**Pending decisions:**
- [measure] estimated_landed_cost: confidence 80%
- [measure] first_cost: confidence 80%
- [measure] original_estimated_landed_cost: confidence 80%
- [measure] original_first_cost: confidence 80%
- [measure] estimated_landed_cost_currency_sid: confidence 74%
- [measure] first_cost_currency_sid: confidence 74%
- [measure] on_order_quantity: confidence 74%
- [measure] order_quantity: confidence 74%

---

## [FAIL] ri2_cc_fgcolor
45 rules — 43 passed, **2 failed**

**Failures:**
- `freshness`: The source data is significantly lagging behind the target data.
- `active_indicator_consistency`: 303 active records do not have a non-null effective_start_date or have an invalid effective_end_date.

---

## [FAIL] ri3_season
39 rules — 35 passed, **4 failed**

**Failures:**
- `freshness`: The source data is significantly lagging behind the target data.
- `relationship__ri3_season_sid_exists`: The referential integrity check failed because there are season_sids in dim_customer_choice that do not exist in dim_season.
- `season_sid_validity`: The data-quality check for the 'season_sid' column in the 'dim_customer_choice' table failed because there are 176 invalid season_sids that do not exist in the 'dim_season' table.
- `assorted_column_format`: The 'assorted' column contains values other than 'Y' or 'N'.

---

*Key files:*
- `failures_with_rca.csv` — every failed check with its root cause explanation inline
- `consolidated_report.xlsx` — full results across all tables
- `data_profiling.csv` — column-level statistics
- `approval_proposals.json` — pending decisions blocking rule execution