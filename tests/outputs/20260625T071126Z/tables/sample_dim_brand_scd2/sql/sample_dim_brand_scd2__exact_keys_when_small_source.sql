SELECT
  MD5(CONCAT_WS('|', COALESCE(CAST(`brand_sid` AS STRING), '<NULL>'))) AS key_hash,
  COUNT(*) OVER () AS total_rows
FROM `local`.`source`.`dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
ORDER BY
  key_hash
LIMIT 5000
