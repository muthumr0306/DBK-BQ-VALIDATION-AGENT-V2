SELECT
  `brand_sid` AS key_1,
  `brand_sid` AS value_1,
  `brand_name` AS value_2,
  `active_indicator` AS value_3,
  `source_system` AS value_4,
  `updated_at` AS value_5,
  COUNT(*) OVER () AS total_rows
FROM `local`.`source`.`dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
ORDER BY
  key_1
LIMIT 5000
