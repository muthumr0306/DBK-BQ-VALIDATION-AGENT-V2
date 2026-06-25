SELECT
  COUNT(*) AS row_count,
  SUM(CASE WHEN `category` IS NULL THEN 1 ELSE 0 END) AS null_count,
  COUNT(DISTINCT `category`) AS distinct_count,
  MIN(`category`) AS min_value,
  MAX(`category`) AS max_value
FROM `local.target.bq_only_table`
