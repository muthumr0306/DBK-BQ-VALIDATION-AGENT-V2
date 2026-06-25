SELECT
  SUBSTRING(MD5(CONCAT_WS('|', COALESCE(CAST(`brand_sid` AS STRING), '<NULL>'))), 1, 2) AS key_bucket,
  COUNT(*) AS row_count
FROM `local`.`source`.`dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
GROUP BY
  key_bucket
ORDER BY
  key_bucket
