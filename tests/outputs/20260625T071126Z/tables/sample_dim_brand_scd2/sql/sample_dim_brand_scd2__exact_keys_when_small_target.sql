SELECT
  TO_HEX(MD5(CONCAT(COALESCE(CAST(`brand_sid` AS STRING), '<NULL>')))) AS key_hash,
  COUNT(*) OVER () AS total_rows
FROM `local.target.dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
ORDER BY
  key_hash
LIMIT 5000
