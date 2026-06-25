SELECT
  SUBSTR(TO_HEX(MD5(CONCAT(COALESCE(CAST(`market_sid` AS STRING), '<NULL>')))), 1, 2) AS key_bucket,
  COUNT(*) AS row_count
FROM `local.target.dim_market`
GROUP BY
  key_bucket
ORDER BY
  key_bucket
