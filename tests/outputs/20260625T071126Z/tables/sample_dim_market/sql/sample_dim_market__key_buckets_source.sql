SELECT
  SUBSTRING(MD5(CONCAT_WS('|', COALESCE(CAST(`market_sid` AS STRING), '<NULL>'))), 1, 2) AS key_bucket,
  COUNT(*) AS row_count
FROM `local`.`source`.`dim_market`
GROUP BY
  key_bucket
ORDER BY
  key_bucket
