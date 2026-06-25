SELECT
  COUNT(*) AS row_count,
  SUM(CASE WHEN `value` IS NULL THEN 1 ELSE 0 END) AS null_count,
  COUNT(DISTINCT `value`) AS distinct_count,
  MIN(`value`) AS min_value,
  MAX(`value`) AS max_value
FROM `local.target.bq_only_table`
