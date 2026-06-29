# DQ Run: 20260629T122111Z
**Project**: mock_test  
**Status**: RUNNING  

---

## [ERROR] gdp_inv_loc
120 rules — 65 passed, **83 failed**, 1 errors
*27 root cause(s), 27 cascading signal(s)*

**Failures:**
- `row_count`: Filtered source and target row counts do not match
- `distinct__inventory_managed_location_id` *(cascade)*: The distinct count of 'inventory_managed_location_id' in the source and target datasets differ by 40 records.
- `distribution__inventory_managed_location_id`: The value distribution of the 'inventory_managed_location_id' column in the source and target datasets does not match, with 40 mismatches observed.
- `distinct__brand_id` *(cascade)*: The distinct count of brand_id in the source and target datasets differ by 40 records.
- `distribution__brand_id`: The value distributions of the 'brand_id' column in the source and target datasets do not match.
- `distinct__market_id` *(cascade)*: The distinct count of market_id in the source and target datasets differ by 40 records.
- `distribution__market_id`: The value distribution of the 'market_id' column in the source and target datasets do not match.
- `distinct__channel_id` *(cascade)*: The distinct count of channel_id in the source and target datasets differ by 40 records.
- `distribution__channel_id`: The value distributions of the 'channel_id' column in the source and target datasets do not match.
- `distinct__location_number` *(cascade)*: The distinct count of location_number in the source and target datasets differ by 40 records.
- `distinct__location_type` *(cascade)*: The distinct count of 'location_type' in the source and target datasets differ by 40 records, which is outside the allowed difference.
- `distribution__location_type`: The value distributions of the 'location_type' column in the source and target datasets do not match.
- `distinct__country` *(cascade)*: The distinct count of countries in the source and target datasets differ by 40 records.
- `distribution__country`: The value distributions of the 'country' column in the source and target datasets do not match.
- `distinct__alternative_location_id` *(cascade)*: The distinct count of 'alternative_location_id' in the source and target datasets differ by 40 records.
- `distribution__alternative_location_id`: The value distributions of the 'alternative_location_id' column in both source and target datasets do not match.
- `distinct__value_type` *(cascade)*: The distinct count of the 'value_type' column in the source and target datasets differ by 40 records.
- `distribution__value_type`: The value types in the source and target datasets do not match, with 40 mismatches identified.
- `distinct__ownership` *(cascade)*: The distinct count of the 'ownership' column in the source and target datasets differs by 40 records.
- `distribution__ownership`: The value distributions of the 'ownership' column in the source and target datasets do not match.
- `distinct__economic_region` *(cascade)*: The distinct count of 'economic_region' in the source and target datasets differs by 40 records.
- `distribution__economic_region`: The value distributions for the 'economic_region' column in both source and target datasets do not match, with 40 mismatches observed.
- `distinct__pick_priority` *(cascade)*: The distinct count of pick_priority in the source and target datasets differ by 40 records, which is outside the allowed difference of 0.0.
- `distinct__default_supply_node` *(cascade)*: The distinct count of the 'default_supply_node' column differs between the source and target datasets.
- `distinct__accounts_payable_code` *(cascade)*: The distinct count of accounts_payable_code in the source and target datasets differ by 40 records.
- `schema__first_pick_date`: The data types of the 'first_pick_date' column in both source and target datasets do not match, leading to a failed schema comparison.
- `distinct__first_pick_date` *(cascade)*: The distinct count of 'first_pick_date' in the source is significantly higher than in the target.
- `schema__temporary_closed_date`: The data types of the 'temporary_closed_date' column in both source and target do not match, leading to a failed schema comparison.
- `distinct__temporary_closed_date` *(cascade)*: The distinct count of 'temporary_closed_date' in the source is significantly higher than in the target, indicating a discrepancy.
- `schema__inventory_management_closed_date`: The data type of 'inventory_management_closed_date' in the source does not match the target, which is a violation of the schema rule.
- `distinct__inventory_management_closed_date` *(cascade)*: The distinct count of 'inventory_management_closed_date' in the source is significantly higher than in the target.
- `schema__reopened_date`: The data types of the 'reopened_date' column in the source and target do not match, leading to a failed schema comparison.
- `distinct__reopened_date` *(cascade)*: The distinct count of reopened_date in the source is significantly higher than in the target.
- `distinct__walkthrough_id_1` *(cascade)*: The distinct count of 'walkthrough_id_1' in the source and target datasets differ by 40 records.
- `distribution__walkthrough_id_1`: The value distributions of the 'walkthrough_id_1' column in both source and target datasets do not match, with 40 mismatches observed.
- `distinct__walkthrough_id_2` *(cascade)*: The distinct count of 'walkthrough_id_2' in the source and target datasets differs by 40 records.
- `distinct__minimum_inventory` *(cascade)*: The distinct count of 'minimum_inventory' in the source and target datasets differ by more than the allowed difference.
- `distinct__maximum_inventory` *(cascade)*: The distinct count of the 'maximum_inventory' column in the source and target datasets differs by 40 records, which exceeds the allowed difference of 0.0.
- `distinct__maximum_shipment_cartons` *(cascade)*: The distinct count of 'maximum_shipment_cartons' in the source and target datasets differ by 40 records, which is outside the allowed difference of 0.0.
- `distinct__updated_by` *(cascade)*: The distinct count of the 'updated_by' column in the source and target datasets differ by 40 records, which is outside the allowed difference.
- `distribution__updated_by`: The updated_by values in the source and target datasets do not match.
- `distinct__allocation_type` *(cascade)*: The distinct count of the 'allocation_type' column differs between the source and target datasets.
- `distribution__allocation_type`: The value distributions of the 'allocation_type' column in the source and target datasets do not match.
- `distinct__abbreviated_location_name` *(cascade)*: The distinct count of 'abbreviated_location_name' in the source and target datasets differ significantly.
- `distribution__abbreviated_location_name`: The value distribution of the 'abbreviated_location_name' column in both source and target datasets does not match, with significant mismatches between the counts of certain values.
- `distinct__real_estate_type` *(cascade)*: The distinct count of real_estate_type in the source and target datasets differ by 40 records, which exceeds the allowed difference of 0.
- `distribution__real_estate_type`: The real estate types in the source and target datasets do not match.
- `distribution__store_hold_pick_indicator`: The value distribution of the 'store_hold_pick_indicator' column in both source and target datasets does not match, with specific mismatches observed.
- `key_buckets`: The source and target datasets have a high degree of similarity in their key buckets, but there are some discrepancies that need to be addressed.
- `exact_keys_when_small`: The source and target tables have different numbers of unique keys despite having the same date coverage.
- `mapped_rows_when_small`: The source and target datasets have the same date coverage for the 'gdp_inv_loc' pair.
- `location_type_mandatory`: The location type column contains null values in 960 rows.
- `location_number_positive`: The location number column contains 960 invalid entries that are not positive integers.
- `pick_priority_non_negative`: The pick priority values for 960 records are less than zero, violating the rule that pick priority should be a non-negative integer.
- `inventory_management_closed_date_after_first_pick`: 
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

## [WAITING FOR REVIEW] gdp_mc_cc

Rules did not execute — 2 pending decision(s) required.

**Pending decisions:**
- [measure] market_channel_customer_choice_country_price: confidence 80%
- [measure] market_channel_style_country_price: confidence 80%

---

## [WAITING FOR REVIEW] gdp_mc_sku

Rules did not execute — 34 pending decision(s) required.

**Pending decisions:**
- Approve column mapping: **sku_id** (source) → **style_id** (target)? [confidence: 56%]
- Approve column mapping: **universal_sku_number** (source) → **universal_style_number** (target)? [confidence: 70%]
- Approve column mapping: **market_channel_sku_style_customer_choice_size_number** (source) → **market_channel_customer_choice_id** (target)? [confidence: 67%]
- Approve column mapping: **sku_number** (source) → **division_number** (target)? [confidence: 61%]
- Approve column mapping: **alternate_sku_number** (source) → **alternate_style_number** (target)? [confidence: 70%]
- Approve column mapping: **market_channel_sku_abbreviated_description** (source) → **market_channel_style_abbreviated_description** (target)? [confidence: 74%]
- Approve column mapping: **market_channel_sku_description** (source) → **market_channel_style_description** (target)? [confidence: 72%]
- Approve column mapping: **market_channel_sku_status_code** (source) → **market_channel_style_status_code** (target)? [confidence: 72%]
- Approve column mapping: **market_channel_sku_status_description** (source) → **market_channel_style_status_description** (target)? [confidence: 73%]
- Approve column mapping: **market_channel_sku_barcodes** (source) → **market_channel_style_id** (target)? [confidence: 66%]
- Approve column mapping: **market_channel_sku_alternate_barcodes** (source) → **market_channel_style_id** (target)? [confidence: 65%]
- Approve column mapping: **sku_description** (source) → **style_description** (target)? [confidence: 69%]
- Approve column mapping: **sku_abbreviated_description** (source) → **style_abbreviated_description** (target)? [confidence: 72%]
- Approve column mapping: **sku_style_customer_choice_size_number** (source) → **customer_choice_number** (target)? [confidence: 67%]
- Approve column mapping: **size_code_id** (source) → **style_id** (target)? [confidence: 57%]
- Approve column mapping: **size_model_code** (source) → **finished_goods_color_code** (target)? [confidence: 57%]
- Approve column mapping: **size_code** (source) → **garment_type_code** (target)? [confidence: 59%]
- Approve column mapping: **size_description** (source) → **style_description** (target)? [confidence: 70%]
- Approve column mapping: **sku_status_code** (source) → **customer_choice_status_code** (target)? [confidence: 60%]
- Approve column mapping: **sku_status_description** (source) → **subclass_description** (target)? [confidence: 67%]
- Approve column mapping: **barcode** (source) → **customer_choice_id** (target)? [confidence: 53%]
- Approve column mapping: **barcode_type** (source) → **style_type** (target)? [confidence: 56%]
- Approve column mapping: **franchise_model** (source) → **customer_choice_id** (target)? [confidence: 49%]
- Approve column mapping: **is_vendor_barcode** (source) → **vendor_style_code** (target)? [confidence: 33%]
- Approve column mapping: **order_channel_code** (source) → **market_channel_style_status_code** (target)? [confidence: 60%]
- Approve column mapping: **style_number** (source) → **legacy_subclass_id** (target)? [confidence: 42%]
- Approve column mapping: **selling_season_type_description** (source) → **corporation_id** (target)? [confidence: 10%]
- Approve column mapping: **subbrand_id** (source) → **corporation_id** (target)? [confidence: 23%]
- Approve column mapping: **subbrand_number** (source) → **corporation_id** (target)? [confidence: 12%]
- Approve column mapping: **subbrand_code** (source) → **corporation_id** (target)? [confidence: 13%]
- Approve column mapping: **subbrand_abbreviated_description** (source) → **corporation_id** (target)? [confidence: 12%]
- Approve column mapping: **subbrand_description** (source) → **corporation_id** (target)? [confidence: 16%]
- [measure] market_channel_customer_choice_country_price: confidence 80%
- [measure] market_channel_style_country_price: confidence 80%

---

## [WAITING FOR REVIEW] gdp_division

Rules did not execute — 33 pending decision(s) required.

**Pending decisions:**
- Approve column mapping: **gdp_processed_timestamp** (source) → **insert_dttm** (target)? [confidence: 44%]
- Approve column mapping: **gdp_last_processed_by** (source) → **updated_by** (target)? [confidence: 57%]
- Approve column mapping: **epoch_id** (source) → **hierarchy_sid** (target)? [confidence: 52%]
- Approve column mapping: **latency** (source) → **division_sid** (target)? [confidence: 43%]
- Approve column mapping: **division_abbreviated_description** (source) → **division_description_abbreviated** (target)? [confidence: 62%]
- Approve column mapping: **brand_market_channel_id** (source) → **division_description_abbreviated** (target)? [confidence: 37%]
- Approve column mapping: **brand_id** (source) → **division_description_abbreviated** (target)? [confidence: 39%]
- Approve column mapping: **market_id** (source) → **inserted_by** (target)? [confidence: 45%]
- Approve column mapping: **channel_id** (source) → **inserted_by** (target)? [confidence: 44%]
- Approve column mapping: **brand_number** (source) → **division_sid** (target)? [confidence: 38%]
- Approve column mapping: **market_number** (source) → **division_sid** (target)? [confidence: 34%]
- Approve column mapping: **channel_number** (source) → **division_sid** (target)? [confidence: 34%]
- Approve column mapping: **brand_code** (source) → **division_description_abbreviated** (target)? [confidence: 42%]
- Approve column mapping: **market_code** (source) → **updated_by** (target)? [confidence: 43%]
- Approve column mapping: **channel_code** (source) → **updated_by** (target)? [confidence: 43%]
- Approve column mapping: **corporation_company_id** (source) → **division_description_abbreviated** (target)? [confidence: 47%]
- Approve column mapping: **corporation** (source) → **division_description_abbreviated** (target)? [confidence: 44%]
- Approve column mapping: **company** (source) → **inserted_by** (target)? [confidence: 46%]
- Approve column mapping: **hierarchy_description** (source) → **division_description_abbreviated** (target)? [confidence: 55%]
- Approve column mapping: **hierarchy_type_id** (source) → **source_system** (target)? [confidence: 46%]
- Approve column mapping: **hierarchy_type_description** (source) → **division_description_abbreviated** (target)? [confidence: 54%]
- Approve column mapping: **version** (source) → **division_sid** (target)? [confidence: 53%]
- Approve column mapping: **last_modified_user** (source) → **updated_by** (target)? [confidence: 43%]
- Approve column mapping: **create_datetime** (source) → **source_system** (target)? [confidence: 46%]
- Approve column mapping: **last_modified_datetime** (source) → **division_description_abbreviated** (target)? [confidence: 42%]
- Approve column mapping: **merchandise_classification_id** (source) → **division_description_abbreviated** (target)? [confidence: 43%]
- Approve column mapping: **subbrand_id** (source) → **division_description_abbreviated** (target)? [confidence: 43%]
- Approve column mapping: **subbrand_number** (source) → **division_description_abbreviated** (target)? [confidence: 43%]
- Approve column mapping: **subbrand_code** (source) → **division_description_abbreviated** (target)? [confidence: 48%]
- Approve column mapping: **subbrand_abbreviated_description** (source) → **division_description_abbreviated** (target)? [confidence: 55%]
- Approve column mapping: **subbrand_description** (source) → **division_description_abbreviated** (target)? [confidence: 57%]
- Approve column mapping: **failed_rule_id** (source) → **updated_by** (target)? [confidence: 42%]
- Approve column mapping: **dq_processed_timestamp** (source) → **insert_dttm** (target)? [confidence: 44%]

---

## [ERROR] gdp_style
26 rules — 16 passed, **58 failed**, 3 errors

**Failures:**
- `distinct__style_id`: The distinct count of style_id in the source differs from that in the target.
- `distribution__style_id`: The value distribution of the 'style_id' column differs between the source and target datasets.
- `schema__style_cc_status_sid`: The data types of the source and target columns do not match, which violates the schema rule.
- `target_key_unique`: The target column 'style_id' contains duplicate values.
- `key_buckets`: There are discrepancies in the key buckets between the source and target datasets.
- `exact_keys_when_small`: The source table has more unique style_id values than the target table, with 1200 missing keys in the target.
- `mapped_rows_when_small`: ERROR
- `freshness`: 
- `end_date_before_start_date`: 
- `style_description_length`: 
- `schema_gap__source__abbreviated_description`: 
- `schema_gap__source__brand_number`: 
- `schema_gap__source__class_number`: 
- `schema_gap__source__create_DateTime`: 
- `schema_gap__source__department_number`: 
- `schema_gap__source__description`: 
- `schema_gap__source__division_number`: 
- `schema_gap__source__dq_processed_timestamp`: 
- `schema_gap__source__epoch_id`: 
- `schema_gap__source__failed_rule_id`: 
- `schema_gap__source__gdp_last_processed_by`: 
- `schema_gap__source__gdp_processed_timestamp`: 
- `schema_gap__source__global_assortment_item_number`: 
- `schema_gap__source__isHazardousMaterial`: 
- `schema_gap__source__last_Modified_DateTime`: 
- `schema_gap__source__last_modified_user`: 
- `schema_gap__source__latency`: 
- `schema_gap__source__merchandise_classification_id`: 
- `schema_gap__source__purge_date`: 
- `schema_gap__source__purge_indicator`: 
- `schema_gap__source__size_model_code`: 
- `schema_gap__source__size_model_id`: 
- `schema_gap__source__status_description`: 
- `schema_gap__source__subbrand_number`: 
- `schema_gap__source__subclass_number`: 
- `schema_gap__source__type`: 
- `schema_gap__source__universal_subclass_id`: 
- `schema_gap__source__version`: 
- `schema_gap__target__active_indicator`: 
- `schema_gap__target__brand_sid`: 
- `schema_gap__target__effective_end_date`: 
- `schema_gap__target__effective_start_date`: 
- `schema_gap__target__insert_date`: 
- `schema_gap__target__insert_dttm`: 
- `schema_gap__target__inserted_by`: 
- `schema_gap__target__size_model_sid`: 
- `schema_gap__target__style_category`: 
- `schema_gap__target__style_collection`: 
- `schema_gap__target__style_description`: 
- `schema_gap__target__style_description_abbreviated`: 
- `schema_gap__target__style_end_date`: 
- `schema_gap__target__style_family`: 
- `schema_gap__target__style_launch_date`: 
- `schema_gap__target__style_purge_date`: 
- `schema_gap__target__style_purge_indicator`: 
- `schema_gap__target__style_sid`: 
- `schema_gap__target__style_type`: 
- `schema_gap__target__subclass_sid`: 
- `schema_gap__target__update_date`: 
- `schema_gap__target__update_dttm`: 
- `schema_gap__target__updated_by`: 

---

## [WAITING FOR REVIEW] gdp_daily_inv

Rules did not execute — 2 pending decision(s) required.

**Pending decisions:**
- [measure] current_price: confidence 80%
- [measure] effective_price: confidence 80%

---

## [WAITING FOR REVIEW] gdp_location

Rules did not execute — 4 pending decision(s) required.

**Pending decisions:**
- [measure] sales_area: confidence 74%
- [measure] total_improvable_area: confidence 74%
- [measure] total_improved_area: confidence 74%
- [measure] total_unimproved_area: confidence 74%

---

## [ERROR] gdp_fg_color
62 rules — 33 passed, **49 failed**, 2 errors
*15 root cause(s), 12 cascading signal(s)*

**Failures:**
- `row_count`: Filtered source and target row counts do not match due to a discrepancy in the number of rows.
- `distinct__finished_good_color_id` *(cascade)*: The distinct count of 'finished_good_color_id' in the source and target datasets differs by 3 records, which exceeds the allowed difference.
- `distribution__finished_good_color_id`: The value distribution of the 'finished_good_color_id' column differs between source and target datasets.
- `distinct__color_standard_number` *(cascade)*: The distinct count of 'color_standard_number' in the source and target datasets differ by 3, which exceeds the allowed difference of 0.
- `distinct__color_type` *(cascade)*: The distinct count of 'color_type' in the source and target datasets differs by 3 records, which exceeds the allowed difference of 0.0.
- `distribution__color_type`: The value distributions of the 'color_type' column in both source and target datasets do not match, with 40 mismatches observed.
- `distinct__finished_good_color_number` *(cascade)*: The distinct count of 'finished_good_color_number' in the source and target datasets differ by 3, which exceeds the allowed difference of 0.
- `distribution__finished_good_color_number`: The finished_good_color_number values in the source and target datasets do not match, with a significant number of mismatches.
- `distinct__finished_good_color_code` *(cascade)*: The distinct count of 'finished_good_color_code' in the source and target datasets differs by 3 records, which exceeds the allowed difference.
- `distribution__finished_good_color_code`: The finished_good_color_code values in the source and target datasets do not match, with a significant number of mismatches.
- `distinct__color_palette_season_code` *(cascade)*: The distinct count of 'season_code' in the source does not match the distinct count of 'color_palette_season_code' in the target, with a difference of 3.
- `distribution__color_palette_season_code`: The value distributions of the source and target columns do not match.
- `distinct__color_palette_season_year` *(cascade)*: The distinct count of 'color_palette_season_year' does not match the distinct count of 'season_year'.
- `distinct__finished_good_color_description_abbreviated` *(cascade)*: The distinct count of the 'finished_good_color_description_abbreviated' column in the target differs from that of the 'abbreviated_description' column in the source by 3 records.
- `distribution__finished_good_color_description_abbreviated`: The value distributions of the source and target columns do not match.
- `distinct__finished_good_color_description` *(cascade)*: The distinct count of 'finished_good_color_description' in the target differs from that in the source by 3 records.
- `distribution__finished_good_color_description`: The value distributions of the source and target columns do not match.
- `distribution__is_global`: The value distribution for the column 'isGlobal' does not match between the source and target datasets.
- `distinct__brand_sid` *(cascade)*: The distinct count of 'brand_number' in the source does not match the distinct count of 'brand_sid' in the target, with a difference of 3.
- `distinct__color_family_sid` *(cascade)*: The distinct count of 'color_family_number' in the source does not match the distinct count of 'color_family_sid' in the target, with a difference of 3.
- `distinct__color_palette_id` *(cascade)*: The distinct count of 'color_palette_id' in the source and target datasets differs by 3 records, which exceeds the allowed difference of 0.0.
- `distribution__color_palette_id`: The value distribution of the color_palette_id column differs between source and target datasets.
- `key_buckets`: Mismatch found in key buckets for 'gdp_fg_color' between source and target datasets.
- `exact_keys_when_small`: The data-quality check failed due to an extra key in the target table.
- `mapped_rows_when_small`: The comparison between the source and target datasets for the 'gdp_fg_color' pair resulted in an extra count of 3 records, which is beyond the expected limit.
- `freshness`: 
- `color_palette_season_year_must_be_valid`: The color palette season year contains invalid calendar years.
- `color_palette_season_code_must_match_year`: 
- `color_standard_number_must_be_numeric`: The color standard number contains non-numeric values.
- `schema_gap__source__color_family_description`: 
- `schema_gap__source__create_DateTime`: 
- `schema_gap__source__dq_processed_timestamp`: 
- `schema_gap__source__epoch_id`: 
- `schema_gap__source__failed_rule_id`: 
- `schema_gap__source__gdp_last_processed_by`: 
- `schema_gap__source__gdp_processed_timestamp`: 
- `schema_gap__source__last_Modified_DateTime`: 
- `schema_gap__source__last_modified_user`: 
- `schema_gap__source__latency`: 
- `schema_gap__source__version`: 
- `schema_gap__target__active_indicator`: 
- `schema_gap__target__effective_end_date`: 
- `schema_gap__target__effective_start_date`: 
- `schema_gap__target__finished_good_color_sid`: 
- `schema_gap__target__insert_date`: 
- `schema_gap__target__insert_dttm`: 
- `schema_gap__target__inserted_by`: 
- `schema_gap__target__source_system`: 
- `schema_gap__target__update_date`: 
- `schema_gap__target__update_dttm`: 
- `schema_gap__target__updated_by`: 

---

## [ERROR] gdp_bmc
108 rules — 68 passed, **56 failed**, 2 errors
*20 root cause(s), 18 cascading signal(s)*

**Failures:**
- `row_count`: Filtered source and target row counts do not match
- `distinct__brand_market_channel_id` *(cascade)*: The distinct count of 'brand_market_channel_id' in the source and target datasets differ.
- `distribution__active_indicator`: The value distribution of the 'is_active' column does not match that of the 'active_indicator' column.
- `distinct__platform_abbreviated_description` *(cascade)*: The distinct count of 'platform_abbreviated_description' in the source and target datasets differ by 27 records.
- `distribution__platform_abbreviated_description`: The value distributions in the source and target datasets for the column 'platform_abbreviated_description' do not match.
- `distinct__description` *(cascade)*: The distinct count of the 'description' column in the source differs from that in the target by 27 records.
- `distribution__description`: The value distributions in the source and target columns do not match.
- `distinct__abbreviated_description` *(cascade)*: The distinct count of 'abbreviated_description' in the source and target datasets differ by 27 records, which is outside the allowed difference.
- `distribution__abbreviated_description`: The value distribution of the 'abbreviated_description' column in both source and target datasets does not match, with 40 mismatches observed.
- `distinct__corporation` *(cascade)*: The distinct count of the 'corporation' column in the source differs from that in the target by 27 records.
- `distribution__corporation`: The value distributions in the 'corporation' column between source and target do not match.
- `distinct__company` *(cascade)*: The distinct count of companies in the source and target datasets differ by 27.
- `distribution__company`: The value distribution of the 'company' column in both source and target datasets does not match, with 30 mismatches observed.
- `distinct__platform_description` *(cascade)*: The distinct count of 'platform_description' in the source and target datasets differ by 27 records.
- `distribution__platform_description`: The value distributions of the 'platform_description' column in the source and target datasets do not match.
- `distinct__version` *(cascade)*: The distinct count of the 'version' column in the source and target datasets differ.
- `distinct__hierarchy_id` *(cascade)*: The distinct count of 'hierarchy_id' in the source and target datasets differ.
- `distinct__hierarchy_description` *(cascade)*: The distinct count of 'hierarchy_description' in the source and target datasets differ by 27 records, which is outside the allowed difference.
- `distribution__hierarchy_description`: The value distribution of the 'hierarchy_description' column does not match between the source and target datasets.
- `distinct__brand_code` *(cascade)*: The distinct count of brand_code in the source and target datasets differ.
- `distribution__brand_code`: The value distributions of the 'brand_code' column in the source and target datasets do not match.
- `distinct__brand_description` *(cascade)*: The distinct count of brand_description in the source and target datasets differ by 27 records.
- `distribution__brand_description`: The value distribution of the 'brand_description' column in the source and target datasets do not match.
- `distinct__brand_abbreviated_description` *(cascade)*: The distinct count of 'brand_abbreviated_description' in the source and target datasets differ by 27 records.
- `distribution__brand_abbreviated_description`: The value distributions of the 'brand_abbreviated_description' column in both source and target datasets do not match.
- `distinct__market_code` *(cascade)*: The distinct count of market codes in the source and target datasets differ by 27.
- `distribution__market_code`: The value distributions of the 'market_code' column in the source and target datasets do not match.
- `distinct__market_description` *(cascade)*: The distinct count of 'market_description' in the source and target datasets differ by 27 records.
- `distribution__market_description`: The value distributions in the source and target columns for 'market_description' do not match.
- `distinct__market_abbreviated_description` *(cascade)*: The distinct count of 'market_abbreviated_description' in the source and target datasets differ, with the source having more unique values.
- `distribution__market_abbreviated_description`: The value distributions of the 'market_abbreviated_description' column in both source and target datasets do not match, with 40 mismatches observed.
- `distinct__channel_code` *(cascade)*: The distinct count of channel_code in the source and target datasets differ by 27 records.
- `distribution__channel_code`: The value distribution of the channel_code column differs between the source and target datasets.
- `distinct__channel_description` *(cascade)*: The distinct count of channel_description in the source and target datasets differ by 27 records.
- `distribution__channel_description`: The value distributions in the source and target columns do not match.
- `key_buckets`: The key buckets for the 'gdp_bmc' pair exhibit discrepancies in date coverage, with some source records missing in the target and vice versa.
- `exact_keys_when_small`: The source table has more unique key hashes than the target table, indicating a discrepancy.
- `mapped_rows_when_small`: ERROR: list index out of range
- `freshness`: 
- `gdp_bmc_start_date_before_end_date`: 
- `schema_gap__source__create_datetime`: 
- `schema_gap__source__dq_processed_timestamp`: 
- `schema_gap__source__epoch_id`: 
- `schema_gap__source__failed_rule_id`: 
- `schema_gap__source__gdp_last_processed_by`: 
- `schema_gap__source__gdp_processed_timestamp`: 
- `schema_gap__source__last_modified_datetime`: 
- `schema_gap__source__last_modified_user`: 
- `schema_gap__source__latency`: 
- `schema_gap__target__brand_market_channel_sid`: 
- `schema_gap__target__effective_end_date`: 
- `schema_gap__target__effective_start_date`: 
- `schema_gap__target__insert_date`: 
- `schema_gap__target__insert_dttm`: 
- `schema_gap__target__inserted_by`: 
- `schema_gap__target__update_date`: 
- `schema_gap__target__update_dttm`: 
- `schema_gap__target__updated_by`: 

---

## [ERROR] gdp_size
28 rules — 13 passed, **33 failed**, 3 errors
*8 root cause(s), 4 cascading signal(s)*

**Failures:**
- `row_count`: Filtered source and target row counts do not match
- `distinct__size_id` *(cascade)*: The distinct count of 'size_id' in the source and target datasets differ by 81 records.
- `distinct__size_code` *(cascade)*: The distinct count of size_code in the source and target datasets differ by 30, which is outside the allowed difference of 0.
- `distribution__size_code`: The value distributions of the 'size_code' column in both source and target datasets do not match.
- `schema__size_model_sid`: The data types of the source and target columns do not match, leading to a failed schema comparison.
- `distinct__size_model_sid` *(cascade)*: The distinct count of 'size_model_code' in the source does not match the distinct count of 'size_model_sid' in the target, with a difference of 30.
- `distinct__size_label` *(cascade)*: The distinct count of 'size_label' in the source and target datasets differ by 30 records, which is outside the allowed difference.
- `target_key_unique`: The target data contains duplicate entries based on the size_id column.
- `key_buckets`: The key buckets for the 'gdp_size' pair do not have complete date coverage, leading to mismatches in some key hashes.
- `exact_keys_when_small`: The source table has more unique key values than the target table, indicating a discrepancy.
- `mapped_rows_when_small`: ERROR
- `freshness`: 
- `effective_date_range`: 
- `size_label_length`: 
- `active_indicator_consistency`: The active indicator is not set to true for 174 records with a non-empty size label.
- `schema_gap__source__create_DateTime`: 
- `schema_gap__source__dq_processed_timestamp`: 
- `schema_gap__source__epoch_id`: 
- `schema_gap__source__failed_rule_id`: 
- `schema_gap__source__gdp_last_processed_by`: 
- `schema_gap__source__gdp_processed_timestamp`: 
- `schema_gap__source__last_Modified_DateTime`: 
- `schema_gap__source__last_modified_user`: 
- `schema_gap__source__latency`: 
- `schema_gap__source__size_model_id`: 
- `schema_gap__source__version`: 
- `schema_gap__target__active_indicator`: 
- `schema_gap__target__effective_end_date`: 
- `schema_gap__target__effective_start_date`: 
- `schema_gap__target__insert_date`: 
- `schema_gap__target__insert_dttm`: 
- `schema_gap__target__inserted_by`: 
- `schema_gap__target__size_sid`: 
- `schema_gap__target__update_date`: 
- `schema_gap__target__update_dttm`: 
- `schema_gap__target__updated_by`: 

---

## [ERROR] ri1_cc_sku
89 rules — 86 passed, **2 failed**, 1 errors

**Failures:**
- `freshness`: The target data is older than the allowed freshness tolerance.
- `cc_line_sku_consistency`: 
- `cost_consistency`: The first cost for 800 records is less than zero, violating the rule that first cost cannot be negative.

---

## [FAIL] ri2_cc_fgcolor
46 rules — 43 passed, **3 failed**

**Failures:**
- `freshness`: The target data is older than the allowed freshness tolerance.
- `color_type_valid_values`: The color_type values in the target dataset do not match the allowed valid values.
- `active_indicator_consistency`: The active_indicator is not set to true for records with non-null effective_start_date and null or future effective_end_date.

---

## [FAIL] ri3_season
40 rules — 35 passed, **5 failed**

**Failures:**
- `freshness`: The source data is significantly lagging behind the target data.
- `relationship__ri3_season_sid_exists`: The referential integrity check failed because there are season_sids in dim_customer_choice that do not exist in dim_season.
- `status_code_validity`: The status_code column contains invalid values.
- `season_sid_validity`: The target table contains 176 season_sids that do not exist in the dim_season table.
- `assorted_format_validation`: The 'assorted' column contains more than just the value 'N'.

---

*Key files:*
- `failures_with_rca.csv` — every failed check with its root cause explanation inline
- `consolidated_report.xlsx` — full results across all tables
- `data_profiling.csv` — column-level statistics
- `approval_proposals.json` — pending decisions blocking rule execution