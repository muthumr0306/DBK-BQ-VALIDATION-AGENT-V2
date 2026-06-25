SELECT
  COUNT(*) AS row_count,
  SUM(CASE WHEN `updated_at` IS NULL THEN 1 ELSE 0 END) AS null_count,
  COUNT(DISTINCT `updated_at`) AS distinct_count,
  MIN(`updated_at`) AS min_value,
  MAX(`updated_at`) AS max_value
FROM `local.target.bq_only_table`
