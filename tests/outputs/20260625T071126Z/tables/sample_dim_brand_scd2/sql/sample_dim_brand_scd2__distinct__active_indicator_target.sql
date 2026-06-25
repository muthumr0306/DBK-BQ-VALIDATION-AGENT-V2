SELECT
  COUNT(DISTINCT `active_indicator`) AS distinct_count
FROM `local.target.dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
