SELECT
  MAX(`updated_at`) AS max_timestamp
FROM `local.target.bq_only_table`
