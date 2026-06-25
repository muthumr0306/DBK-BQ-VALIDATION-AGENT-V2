SELECT
  TO_HEX(MD5(COALESCE(CAST(`category` AS STRING), '<NULL>'))) AS value_hash,
  COUNT(*) AS value_count
FROM `local.target.bq_only_table`
GROUP BY
  value_hash
ORDER BY
  value_count DESC
LIMIT 20
