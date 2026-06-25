SELECT
  COUNT(*) AS row_count,
  COUNTIF(`sales_amount` IS NULL) AS null_count,
  COUNT(DISTINCT `sales_amount`) AS distinct_count,
  MIN(`sales_amount`) AS minimum_value,
  MAX(`sales_amount`) AS maximum_value
FROM `local.target.fact_sales`
