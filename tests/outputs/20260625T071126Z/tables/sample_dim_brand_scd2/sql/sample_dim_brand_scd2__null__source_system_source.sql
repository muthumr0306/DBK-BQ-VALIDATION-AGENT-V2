SELECT
  SUM(CASE WHEN `source_system` IS NULL THEN 1 ELSE 0 END) AS null_count
FROM `local`.`source`.`dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
