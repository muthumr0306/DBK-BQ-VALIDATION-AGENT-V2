SELECT
  COUNT(*) AS duplicate_groups,
  COALESCE(SUM(n - 1), 0) AS duplicate_rows
FROM (
  SELECT
    `record_id`,
    COUNT(*) AS n
  FROM `local.target.bq_only_table`
  GROUP BY
    `record_id`
  HAVING
    COUNT(*) > 1
) AS dq_duplicates
