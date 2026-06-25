SELECT
  MD5(COALESCE(CAST(`active_indicator` AS STRING), '<NULL>')) AS value_hash,
  COUNT(*) AS value_count
FROM `local`.`source`.`dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
GROUP BY
  value_hash
ORDER BY
  value_count DESC
LIMIT 20
