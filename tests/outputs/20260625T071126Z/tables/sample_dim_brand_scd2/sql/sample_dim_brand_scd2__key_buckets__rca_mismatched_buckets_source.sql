SELECT
  MD5(CONCAT_WS('|', COALESCE(CAST(`brand_sid` AS STRING), '<NULL>'))) AS key_hash
FROM `local`.`source`.`dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION'
  AND `active_indicator` = TRUE
  AND SUBSTRING(MD5(CONCAT_WS('|', COALESCE(CAST(`brand_sid` AS STRING), '<NULL>'))), 1, 2) IN ('b7')
ORDER BY
  key_hash
LIMIT 200
