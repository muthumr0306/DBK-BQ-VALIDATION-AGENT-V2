# Runtime input reference

These files describe one validation run. Reusable organizational knowledge belongs in `context_layer/`.

## `table_mappings.xlsx`

| Field | Meaning |
|---|---|
| `pair_id` | Stable run-local identifier; also selects matching table context. |
| `enabled` | Whether the row is loaded. |
| `mode` | `migration` or `bigquery_only`. |
| `source_catalog`, `source_schema`, `source_table` | Required safe identifiers in migration mode. |
| `target_project`, `target_dataset`, `target_table` | Required safe BigQuery identifiers. |

## `column_mappings.xlsx`

| Field | Meaning |
|---|---|
| `pair_id` | Owning table pair. |
| `source_column`, `target_column` | Safe source/target identifiers. |
| `status` | `manual`, `approved`, or `confirmed` locks the mapping; `draft`, `pending`, and `rejected` are ignored for execution. Blank defaults to `approved`. |

## `runtime_overrides.yaml`

`tables.<pair_id>.filters.source` and `.target` are optional lists. A supplied side replaces reusable filters for that side during this run. Each item has `column`, `operator`, and—except for `is_null`/`not_null`—`value`. Operators are `eq`, `ne`, `gt`, `gte`, `lt`, `lte`, `in`, `not_in`, `is_null`, and `not_null`.

## `human_tests.yaml`

Each item under `tests` supports:

| Field | Meaning |
|---|---|
| `id` | Unique test ID. `test_id` is accepted as an alias. |
| `pair_id`, `enabled` | Owning pair and activation flag. |
| `type` | `null`, `accepted_values`, `row_count`, `duplicate`, `freshness`, `aggregate`, or `custom_sql`. Hyphenated forms are also accepted. |
| `scope` | `source`, `target`, `both`, or `compare`. |
| `column`, `columns` | Shared source/target column names. |
| `source_column`, `target_column` | Side-specific scalar names when they differ. |
| `source_columns`, `target_columns` | Side-specific lists for composite checks. |
| `values` | Non-empty accepted-value list. |
| `allowed_nulls`, `allowed_duplicates` | Maximum allowed violation count. |
| `max_delay_minutes` | Freshness lag limit. |
| `aggregation` | `sum`, `avg`, `min`, `max`, or `count`. |
| `comparison` | `eq`, `gte`, or `lte` for aggregate/expected-value checks. |
| `group_by` | Reserved grouping list for typed aggregate tests. |
| `expected_value` | Optional expected scalar result. |
| `tolerance_absolute`, `tolerance_percentage` | Allowed absolute difference and percentage points. |
| `severity`, `description` | Reporting metadata. |
| `source_sql`, `target_sql` | Only for `custom_sql`; each remains subject to single-statement, read-only, function, and table allowlists. |

Generic nested parameter dictionaries are rejected by the schema.
