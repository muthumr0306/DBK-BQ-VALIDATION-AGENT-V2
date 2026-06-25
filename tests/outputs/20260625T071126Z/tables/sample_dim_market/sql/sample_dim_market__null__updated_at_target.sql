SELECT
  SUM(CASE WHEN `updated_at` IS NULL THEN 1 ELSE 0 END) AS null_count
FROM `local.target.dim_market`
