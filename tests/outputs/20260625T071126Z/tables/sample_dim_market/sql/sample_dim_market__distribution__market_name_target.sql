SELECT
  TO_HEX(MD5(COALESCE(CAST(`market_name` AS STRING), '<NULL>'))) AS value_hash,
  COUNT(*) AS value_count
FROM `local.target.dim_market`
GROUP BY
  value_hash
ORDER BY
  value_count DESC
LIMIT 20
