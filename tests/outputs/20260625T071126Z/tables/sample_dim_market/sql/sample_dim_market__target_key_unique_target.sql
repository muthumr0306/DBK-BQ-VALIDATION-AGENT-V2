SELECT
  COUNT(*) AS duplicate_groups,
  COALESCE(SUM(n - 1), 0) AS duplicate_rows
FROM (
  SELECT
    `market_sid`,
    COUNT(*) AS n
  FROM `local.target.dim_market`
  GROUP BY
    `market_sid`
  HAVING
    COUNT(*) > 1
) AS dq_duplicates
