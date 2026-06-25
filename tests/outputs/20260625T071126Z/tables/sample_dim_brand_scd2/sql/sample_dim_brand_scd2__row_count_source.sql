SELECT
  COUNT(*) AS row_count
FROM `local`.`source`.`dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
