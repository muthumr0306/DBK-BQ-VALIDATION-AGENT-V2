SELECT
  TO_HEX(MD5(CONCAT(COALESCE(CAST(`market_sid` AS STRING), '<NULL>')))) AS key_hash,
  COUNT(*) OVER () AS total_rows
FROM `local.target.dim_market`
ORDER BY
  key_hash
LIMIT 5000
