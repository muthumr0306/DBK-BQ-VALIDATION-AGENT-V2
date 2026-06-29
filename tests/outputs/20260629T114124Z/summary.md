# DQ Run: 20260629T114124Z
**Project**: mock_test  
**Status**: RUNNING  

---

## [ERROR] gdp_inv_loc
118 rules — 68 passed, **78 failed**, 1 errors
*22 root cause(s), 27 cascading signal(s)*

**Failures:**
- `row_count`: Filtered source and target row counts do not match.
- `distinct__inventory_managed_location_id` *(cascade)*: The distinct count of 'inventory_managed_location_id' in the source and target datasets differ by 40 records, which is outside the allowed tolerance.
- `distinct__brand_id` *(cascade)*: The distinct count of brand_id in the source and target datasets differ by 40 records.
- `distribution__brand_id`: The value distributions of the 'brand_id' column in the source and target datasets do not match.
- `distinct__market_id` *(cascade)*: The distinct count of market_id in the source and target datasets differ by 40 records.
- `distinct__channel_id` *(cascade)*: The distinct count of channel_id in the source and target datasets differ by 40 records, which is outside the allowed difference.
- `distinct__location_number` *(cascade)*: The distinct count of location_number in the source and target datasets differ by 40 records.
- `distinct__location_type` *(cascade)*: The distinct count of 'location_type' in the source and target datasets differs by 40 records, which is outside the allowed difference.
- `distribution__location_type`: The value distributions of the 'location_type' column in both source and target datasets do not match, with 40 mismatches observed.
- `distinct__country` *(cascade)*: The distinct count of countries in the source and target datasets differs by 40 records.
- `distribution__country`: The value distributions of the 'country' column in the source and target datasets do not match.
- `distinct__alternative_location_id` *(cascade)*: The distinct count of 'alternative_location_id' in the source and target datasets differ by 40 records.
- `distribution__alternative_location_id`: The value distributions of the 'alternative_location_id' column in both source and target datasets do not match, with 40 mismatches found.
- `distinct__value_type` *(cascade)*: The distinct count of the 'value_type' column in the source and target datasets differ by 40 records.
- `distribution__value_type`: The value types in the source and target datasets do not match.
- `distinct__ownership` *(cascade)*: The distinct count of the 'ownership' column in the source and target datasets differ by 40 records.
- `distribution__ownership`: The value distributions of the 'ownership' column in the source and target datasets do not match.
- `distinct__economic_region` *(cascade)*: The distinct count of 'economic_region' in the source and target datasets differ by 40 records.
- `distribution__economic_region`: The value distributions in the source and target columns for 'economic_region' do not match.
- `distinct__pick_priority` *(cascade)*: The distinct count of pick_priority in the source and target datasets differs by 40 records, which is outside the allowed difference of 0.0.
- `distinct__default_supply_node` *(cascade)*: The distinct count of the 'default_supply_node' column differs between the source and target datasets.
- `distinct__accounts_payable_code` *(cascade)*: The distinct count of accounts_payable_code in the source and target datasets differ by 40 records.
- `distribution__accounts_payable_code`: The value distribution of the 'accounts_payable_code' column in the source and target datasets do not match.
- `schema__first_pick_date`: The data types of the 'first_pick_date' column in both source and target do not match, leading to a failed schema comparison.
- `distinct__first_pick_date` *(cascade)*: The distinct count of 'first_pick_date' in the source data is significantly higher than in the target data.
- `schema__temporary_closed_date`: The data types of the 'temporary_closed_date' column in both source and target do not match, leading to a failed schema comparison.
- `distinct__temporary_closed_date` *(cascade)*: The distinct count of 'temporary_closed_date' in the source is significantly higher than in the target.
- `schema__inventory_management_closed_date`: The data types of the 'inventory_management_closed_date' column in both source and target do not match, leading to a failed schema comparison.
- `distinct__inventory_management_closed_date` *(cascade)*: The distinct count of 'inventory_management_closed_date' in the source is significantly higher than in the target, indicating a discrepancy.
- `schema__reopened_date`: The data types of the 'reopened_date' column in the source and target do not match, leading to a failed schema comparison.
- `distinct__reopened_date` *(cascade)*: The distinct count of 'reopened_date' in the source is significantly higher than in the target.
- `distinct__walkthrough_id_1` *(cascade)*: The distinct count of 'walkthrough_id_1' in the source and target datasets differ by 40 records, which is outside the allowed difference.
- `distribution__walkthrough_id_1`: The value distributions of the 'walkthrough_id_1' column in both source and target datasets do not match, with a significant number of mismatches.
- `distinct__walkthrough_id_2` *(cascade)*: The distinct count of 'walkthrough_id_2' in the source and target datasets differs by 40 records, which is outside the allowed difference.
- `distribution__walkthrough_id_2`: The value distributions of the 'walkthrough_id_2' column in both source and target datasets do not match, with 40 mismatches observed.
- `distinct__minimum_inventory` *(cascade)*: The distinct count of 'minimum_inventory' in the source and target datasets differs by 40 records, which exceeds the allowed difference of 0.0.
- `distinct__maximum_inventory` *(cascade)*: The distinct count of the 'maximum_inventory' column in the source and target datasets differs by 40 records, which is outside the allowed difference.
- `distinct__maximum_shipment_cartons` *(cascade)*: The distinct count of 'maximum_shipment_cartons' in the source and target datasets differ by 40 records, which is outside the allowed difference of 0.0.
- `distinct__updated_by` *(cascade)*: The distinct count of the 'updated_by' column in the source and target datasets differ by 40 records.
- `distinct__allocation_type` *(cascade)*: The distinct count of the 'allocation_type' column in the source and target datasets differ by 40 records.
- `distinct__abbreviated_location_name` *(cascade)*: The distinct count of 'abbreviated_location_name' in the source and target datasets differ significantly.
- `distribution__abbreviated_location_name`: The value distribution of 'abbreviated_location_name' in the source and target datasets does not match, with some values present in one dataset but missing or having different counts in the other.
- `distinct__real_estate_type` *(cascade)*: The distinct count of real_estate_type in the source and target datasets differ by 40 records.
- `distribution__real_estate_type`: The value distributions of the 'real_estate_type' column in both source and target datasets do not match, with 40 mismatches observed.
- `distribution__store_hold_pick_indicator`: The value distribution of the 'store_hold_pick_indicator' column differs between the source and target datasets.
- `key_buckets`: The source and target data have significant differences in the distribution of key buckets, particularly with some mismatches in counts. However, the date coverage is identical.
- `exact_keys_when_small`: The source and target tables have different numbers of unique keys despite having the same date coverage.
- `mapped_rows_when_small`: The source and target datasets have the same date coverage for the 'gdp_inv_loc' pair.
- `location_type_mandatory`: The target dataset contains 960 rows where the location_type column is not specified, failing the rule that all location types must be specified.
- `minimum_inventory_cannot_exceed_maximum`: 
- `schema_gap__source__brand_code`: 
- `schema_gap__source__channel_code`: 
- `schema_gap__source__created_by`: 
- `schema_gap__source__created_on`: 
- `schema_gap__source__dq_processed_timestamp`: 
- `schema_gap__source__epoch_id`: 
- `schema_gap__source__failed_rule_id`: 
- `schema_gap__source__gdp_last_processed_by`: 
- `schema_gap__source__gdp_processed_timestamp`: 
- `schema_gap__source__latency`: 
- `schema_gap__source__location_name`: 
- `schema_gap__source__location_status`: 
- `schema_gap__source__market_code`: 
- `schema_gap__target__active_indicator`: 
- `schema_gap__target__alternative_location_sid`: 
- `schema_gap__target__brand_sid`: 
- `schema_gap__target__channel_sid`: 
- `schema_gap__target__country_sid`: 
- `schema_gap__target__effective_end_date`: 
- `schema_gap__target__effective_start_date`: 
- `schema_gap__target__insert_date`: 
- `schema_gap__target__insert_dttm`: 
- `schema_gap__target__inserted_by`: 
- `schema_gap__target__inventory_managed_location_sid`: 
- `schema_gap__target__location_groups`: 
- `schema_gap__target__location_sid`: 
- `schema_gap__target__market_sid`: 
- `schema_gap__target__merchandise_channel_sid`: 
- `schema_gap__target__update_date`: 

---

## [ERROR] gdp_mc_cc

---

## [ERROR] gdp_mc_sku

---

## [ERROR] gdp_division

---

## [ERROR] gdp_style

---

## [ERROR] gdp_daily_inv

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

## [ERROR] ri2_cc_fgcolor
44 rules — 40 passed, **3 failed**, 1 errors

**Failures:**
- `freshness`: The target column 'update_dttm' has a maximum timestamp of 2026-12-28T10:00:00, which is significantly older than the allowed tolerance of 48 hours (2880 minutes).
- `color_type_valid_values`: The color_type values in the target dataset do not match the allowed values.
- `finished_good_color_sid_exists_in_customer_choice`: 
- `active_indicator_consistency`: There are 600 active finished good colors that have an end date set, which violates the rule.

---

## [ERROR] ri3_season
41 rules — 36 passed, **4 failed**, 1 errors

**Failures:**
- `freshness`: The target data is older than the allowed freshness tolerance.
- `relationship__ri3_season_sid_exists`: The referential integrity check failed because there are season_sids in dim_customer_choice that do not exist in dim_season.
- `status_code_validity`: The status_code column contains invalid values.
- `season_sid_validity`: The target table contains 176 season_sids that do not exist in the dim_season table.
- `customer_choice_number_length`: 

---

*Key files:*
- `failures_with_rca.csv` — every failed check with its root cause explanation inline
- `consolidated_report.xlsx` — full results across all tables
- `data_profiling.csv` — column-level statistics
- `approval_proposals.json` — pending decisions blocking rule execution