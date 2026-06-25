SELECT
  SUM(CASE WHEN `record_id` IS NULL THEN 1 ELSE 0 END) AS null_count
FROM `local.target.bq_only_table`
