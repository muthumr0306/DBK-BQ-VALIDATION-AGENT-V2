# DQ Issues Capability Matrix

`DQ Issues.xlsx` is treated as benchmark intent, not executable truth: several supplied SQL statements contain misspellings, missing conjunctions, invalid quoting, or incomplete clauses. The agent reconstructs these checks from verified metadata and allowlisted templates rather than executing workbook SQL.

| Workbook row | Issue intent | Implemented capability | RCA diagnostics | Status / boundary |
|---:|---|---|---|---|
| 3 | Overall count mismatch; missing Store ID | Row-count comparison and mapped-key reconciliation | `missing_keys`, `extra_keys`, failed samples | Implemented; live data required to reproduce the stated ID |
| 4 | Distinct abbreviated location names | Type-aware column profile with distinct count | `column_profile`, `value_distribution` | Implemented |
| 5 | Distinct customer-choice IDs | Distinct profile on mapped semantic column | `column_profile`, `missing_keys` | Implemented; typo-tolerant semantic mapping still requires evidence |
| 6 | Distinct SKU IDs | Distinct profile on mapped semantic column | `column_profile`, `missing_keys` | Implemented |
| 7 | Target-heavy `size_code` nulls | Null-count/rate rule and scalar profile | `null_analysis`, `column_profile` | Implemented |
| 8 | Distinct universal customer-choice numbers | Distinct profile and semantic mapping | `column_profile`, `missing_keys` | Implemented |
| 9 | Entirely-null `class_id` | Null rule plus profile evidence | `null_analysis` | Implemented |
| 10 | Duplicate primary key | Key uniqueness and duplicate rule | `duplicate_analysis`, top duplicate evidence | Implemented |
| 11 | Missing target column | Schema/mapping comparison | No SQL needed; metadata evidence is conclusive | Implemented |
| 12 | Distinct `style_id` mismatch | Distinct mapped-column profile | `column_profile`, `missing_keys`, `extra_keys` | Implemented |
| 13 | Fiscal week/channel grouped metrics and incomplete date coverage | Approved joined grouped profiles with count, distinct, min/max and filters | `date_coverage`, `grouped_metric`, `filter_impact` | Implemented; calendar join and mappings must be approved context |
| 14 | Parent CC rows without SKU children and quantity comparison | Approved relationship integrity and governed measure reconciliation | `relationship_integrity`, `missing_keys`, `grouped_metric` | Implemented; stated workbook SQL is not trusted raw SQL |
| 15 | Missing active/current locations under different SCD filters | Source/target filters, SCD active-row resolution, distinct keys | `filter_impact`, `missing_keys`, `inactive_members` | Implemented; asymmetric filters are explicit/approved context |
| 16 | Row and dimensional distinct-count discrepancies | Multi-column type-aware profiles with target active filter | `filter_impact`, `column_profile` | Implemented |
| 17 | Row count and multiple distinct dimension counts | Multi-column profiles | `column_profile`, `missing_keys` | Implemented |
| 18 | Customer choice references absent finished-good colors | Approved relationship integrity | `relationship_integrity`, `missing_keys` | Implemented |
| 19 | Customer-choice season references absent season dimension | Approved relationship integrity with active filter | `relationship_integrity`, `missing_keys`, `inactive_members` | Implemented |
| 20 | Three extra rows plus absent target season columns | Counts/distinct profiles and schema comparison | `extra_keys`, `column_profile` | Implemented; intended model differences can be approved exceptions |
| 21 | Selected mismatching and matching brand/market/channel metrics | Multi-metric profiles with active filter | `filter_impact`, `column_profile` | Implemented |
| 22 | Row and distinct size-ID discrepancies with renamed columns | Mapping-aware profiles and SCD filter | `column_profile`, `missing_keys`, `filter_impact` | Implemented |

## Coverage interpretation

- **Implemented** means the configuration model, controlled compiler, evidence normalization, reporting surface, and agentic diagnostic intent exist and are covered by offline verification.
- Reproducing a workbook-specific root cause requires accessible source/target tables and credentials.
- Undocumented joins, filters, measures, or semantic equivalences are proposed for approval; they are not invented and auto-executed.
- Raw workbook SQL and raw LLM SQL are never execution paths.
