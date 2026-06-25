SELECT
  COUNT(*) AS row_count,
  SUM(CASE WHEN `record_id` IS NULL THEN 1 ELSE 0 END) AS null_count,
  COUNT(DISTINCT `record_id`) AS distinct_count,
  MIN(`record_id`) AS min_value,
  MAX(`record_id`) AS max_value
FROM `local.target.bq_only_table`
