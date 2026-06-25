SELECT
  COUNT(DISTINCT `brand_name`) AS distinct_count
FROM `local`.`source`.`dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
