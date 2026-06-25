SELECT
  MIN(`updated_at`) AS min_value,
  MAX(`updated_at`) AS max_value
FROM `local`.`source`.`dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
