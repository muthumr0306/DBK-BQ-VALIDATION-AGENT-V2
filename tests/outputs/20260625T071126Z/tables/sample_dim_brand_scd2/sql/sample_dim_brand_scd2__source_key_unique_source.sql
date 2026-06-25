SELECT
  COUNT(*) AS duplicate_groups,
  COALESCE(SUM(n - 1), 0) AS duplicate_rows
FROM (
  SELECT
    `brand_sid`,
    COUNT(*) AS n
  FROM `local`.`source`.`dim_brand_scd2`
  WHERE
    `source_system` = 'BRANDING FOUNDATION' AND `active_indicator` = TRUE
  GROUP BY
    `brand_sid`
  HAVING
    COUNT(*) > 1
) AS dq_duplicates
