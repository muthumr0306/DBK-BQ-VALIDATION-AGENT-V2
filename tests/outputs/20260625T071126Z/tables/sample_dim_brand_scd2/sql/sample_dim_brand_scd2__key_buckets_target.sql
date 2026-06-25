SELECT
  SUBSTR(TO_HEX(MD5(CONCAT(COALESCE(CAST(`brand_sid` AS STRING), '<NULL>')))), 1, 2) AS key_bucket,
  COUNT(*) AS row_count
FROM `local.target.dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
GROUP BY
  key_bucket
ORDER BY
  key_bucket
