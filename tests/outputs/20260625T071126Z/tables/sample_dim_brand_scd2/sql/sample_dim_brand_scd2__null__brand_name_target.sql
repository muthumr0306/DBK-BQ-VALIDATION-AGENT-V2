SELECT
  SUM(CASE WHEN `brand_name` IS NULL THEN 1 ELSE 0 END) AS null_count
FROM `local.target.dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
