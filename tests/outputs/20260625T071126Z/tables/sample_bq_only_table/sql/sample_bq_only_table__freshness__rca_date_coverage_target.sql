SELECT
  MIN(`updated_at`) AS min_value,
  MAX(`updated_at`) AS max_value
FROM `local.target.bq_only_table`
