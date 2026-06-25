SELECT
  SUM(CASE WHEN `region` IS NULL THEN 1 ELSE 0 END) AS null_count
FROM `local`.`source`.`dim_market`
