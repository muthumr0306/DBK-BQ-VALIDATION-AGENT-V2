SELECT
  `record_id`,
  COUNT(*) AS duplicate_count
FROM `local.target.bq_only_table`
GROUP BY
  `record_id`
HAVING
  COUNT(*) > 1
ORDER BY
  duplicate_count DESC
LIMIT 200
