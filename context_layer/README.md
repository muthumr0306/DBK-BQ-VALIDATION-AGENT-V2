# Context layer field reference

Versioned YAML is canonical. `context.db` is a generated SQLite/FTS5 index and must not be edited or committed.

## `tables.yaml`

`tables.<pair_id>` supports:

- `table_type`: `fact`, `dimension`, `bridge`, `reference`, `aggregate`, `audit`, or `unknown`.
- `business_entity`, `description`, `aliases`: table semantics used by retrieval and reasoning.
- `source_table`, `target_table`: optional fully qualified identity hints.
- `primary_key.source|target`: ordered key columns.
- `audit_columns.source|target`, `freshness_sla_minutes`: freshness semantics and lag limit.
- `assumptions`, `known_source_systems`: bounded business descriptions, never executable by themselves.
- `columns.<name>`: `description`, `role`, `required`, `accepted_values`, `minimum`, `maximum`, aliases, and optional `low_cardinality`.
- `filters.source|target`: predicates using the runtime filter schema.
- `scd2.enabled` and `scd2.current_filter.source|target`: current-row behavior.
- `domains[]`: `source_column`, `target_column`, and non-empty `values` for approved domain rules.
- `row_count_tolerance`, `distribution_tolerance`: `absolute` and percentage-point tolerances.
- `grouped_profiles[]`: `id`, `enabled`, description, side-specific groupings/filters/approved joins, `metrics`, `limit`, and tolerance. A join has `table`, equal-width `local_columns`/`remote_columns`, and optional filters. A metric has `name`, `aggregation`, side-specific columns, and optional tolerances.

## `relationships.yaml`

- `settings`: inference/profile switches and bounded candidate/group counts.
- `table_overrides.<pair_id>`: per-pair inference switch.
- `dimensions.<id>`: `enabled`, qualified `table`, description, `keys`, and `parent_filters`. Each key declares `parent_columns` and allowed `child_column_sets`.
- `custom_relationships[]`: `id`, `enabled`, child pair/table and columns, qualified parent table and columns, child/parent filters, and description. Only configured/approved relationships become executable joins.

## `measures.yaml`

- `settings`: inference/profile/reuse switches, candidate limits, grouping limits, and maximum cardinality.
- `diagnostics`: maximum steps, maximum LLM calls, failed-sample limit, and allowlisted diagnostic types.
- `measures[]`: `measure_id`, `definition_type`, `pair_id`, source/target tables and controlled expressions, business name/description, aggregation, absolute/percentage tolerances, optional currency, date columns, side-specific groupings and filters, `enabled`, severity, and approval status.

Expressions are parsed against an arithmetic/function allowlist; they are not raw SQL.

## Pattern and learned files

- `patterns.yaml`: suffix/term lists for identifiers, measures, audit fields, SCD flags/values/end dates, common filters, and accepted-value patterns.
- `issue_patterns.yaml`: records with `id`, applicable validation modes/categories, and allowlisted diagnostic names.
- `learned_context.yaml`: `records[]` written only from reviewed Excel rows. Each record carries context/subject identity, payload, pair/table/column identity, confidence, reviewer, comments, version, and approval timestamp.

Developer context and approved learning receive the highest retrieval trust. Pending/rejected proposals are never indexed. Every returned record includes a stable ID and source-file provenance.
