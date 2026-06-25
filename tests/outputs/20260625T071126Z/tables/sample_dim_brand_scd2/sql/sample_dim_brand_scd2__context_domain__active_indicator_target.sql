SELECT
  SUM(
    CASE
      WHEN NOT `active_indicator` IS NULL AND NOT `active_indicator` IN (TRUE, FALSE)
      THEN 1
      ELSE 0
    END
  ) AS invalid_count
FROM `local.target.dim_brand_scd2`
WHERE
  `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
