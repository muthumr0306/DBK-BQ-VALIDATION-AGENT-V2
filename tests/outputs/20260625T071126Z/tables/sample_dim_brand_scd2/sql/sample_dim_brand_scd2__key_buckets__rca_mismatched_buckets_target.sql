SELECT
  TO_HEX(MD5(CONCAT(COALESCE(CAST(`brand_sid` AS STRING), '<NULL>')))) AS key_hash
FROM `local.target.dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION'
  AND `active_indicator` = TRUE
  AND SUBSTR(TO_HEX(MD5(CONCAT(COALESCE(CAST(`brand_sid` AS STRING), '<NULL>')))), 1, 2) IN ('b7')
ORDER BY
  key_hash
LIMIT 200
