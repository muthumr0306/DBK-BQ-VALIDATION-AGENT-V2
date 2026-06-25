SELECT
  TO_HEX(MD5(COALESCE(CAST(`source_system` AS STRING), '<NULL>'))) AS value_hash,
  COUNT(*) AS value_count
FROM `local.target.dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
GROUP BY
  value_hash
ORDER BY
  value_count DESC
LIMIT 20
