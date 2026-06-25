SELECT
  MAX(`updated_at`) AS max_timestamp
FROM `local.target.dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
