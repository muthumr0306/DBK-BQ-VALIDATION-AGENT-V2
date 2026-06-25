SELECT
  `market_sid` AS key_1,
  `market_sid` AS value_1,
  `market_name` AS value_2,
  `region` AS value_3,
  `updated_at` AS value_4,
  COUNT(*) OVER () AS total_rows
FROM `local`.`source`.`dim_market`
ORDER BY
  key_1
LIMIT 5000
