from __future__ import annotations

import re
from typing import Any, Literal

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

from .config import IDENTIFIER_RE, TablePair


class RuleSpec(BaseModel):
    rule_id: str
    pair_id: str
    type: str
    scope: Literal["source", "target", "both", "compare"] = "both"
    category: str
    source_columns: list[str] = Field(default_factory=list)
    target_columns: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    tolerance: dict[str, Any] = Field(default_factory=dict)
    severity: str = "error"
    origin: str = "generated"
    description: str | None = None
    source_sql: str | None = None
    target_sql: str | None = None


class DiagnosticIntent(BaseModel):
    intent: Literal[
        "date_coverage", "null_distribution", "duplicate_distribution",
        "value_distribution", "missing_keys", "extra_keys", "inactive_members",
        "masked_failed_samples", "filter_impact", "column_profile",
        "relationship_gap", "grouped_metric",
    ]
    side: Literal["source", "target"]
    columns: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str


class QueryGuard:
    SAFE_ANONYMOUS_FUNCTIONS = {
        "ABS", "AVG", "COALESCE", "CONCAT", "CONCAT_WS", "COUNT", "DATE",
        "FORMAT_TIMESTAMP", "IF", "IFNULL", "LOWER", "MAX", "MD5", "MIN",
        "ROUND", "SAFE_CAST", "SUBSTR", "SUM", "TO_HEX", "TO_JSON_STRING", "TRIM", "UPPER",
    }
    BLOCKED_NODES = (
        exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter,
        exp.Command, exp.Merge, exp.Grant, exp.Revoke, exp.Transaction,
    )

    @staticmethod
    def _normalize_table(table: exp.Table) -> str:
        parts = [table.catalog, table.db, table.name]
        return ".".join(str(part).strip("`").lower() for part in parts if part)

    def validate(self, sql: str, dialect: str, allowed_tables: set[str]) -> str:
        if not sql or not sql.strip():
            raise ValueError("SQL must not be empty")
        expressions = sqlglot.parse(sql, read=dialect)
        if len(expressions) != 1:
            raise ValueError("Exactly one SQL statement is allowed")
        expression = expressions[0]
        if not isinstance(expression, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise ValueError("Only SELECT or CTE queries are allowed")
        for blocked in self.BLOCKED_NODES:
            if expression.find(blocked):
                raise ValueError(f"Blocked SQL operation: {blocked.__name__}")
        allowed = {name.lower().strip("`") for name in allowed_tables}
        cte_names = {cte.alias_or_name.lower() for cte in expression.find_all(exp.CTE)}
        referenced = {
            self._normalize_table(table)
            for table in expression.find_all(exp.Table)
            if table.name.lower() not in cte_names
        }
        unexpected = referenced - allowed
        if unexpected:
            raise ValueError(f"SQL references non-allowlisted tables: {sorted(unexpected)}")
        for function in expression.find_all(exp.Anonymous):
            if function.name.upper() not in self.SAFE_ANONYMOUS_FUNCTIONS:
                raise ValueError(f"Function is not allowlisted: {function.name}")
        return expression.sql(dialect=dialect, pretty=True)


class SQLCompiler:
    OPERATORS = {
        "eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<=",
        "in": "IN", "not_in": "NOT IN", "is_null": "IS NULL", "not_null": "IS NOT NULL",
    }

    def __init__(self, dialect: Literal["databricks", "bigquery"]):
        self.dialect = dialect

    def identifier(self, name: str) -> str:
        if not IDENTIFIER_RE.fullmatch(name):
            raise ValueError(f"Unsafe identifier: {name!r}")
        return f"`{name}`"

    def table(self, qualified_name: str) -> str:
        parts = qualified_name.split(".")
        if len(parts) != 3 or any(not IDENTIFIER_RE.fullmatch(part) for part in parts):
            raise ValueError(f"Expected a safe three-part table name: {qualified_name!r}")
        if self.dialect == "bigquery":
            return f"`{qualified_name}`"
        return ".".join(f"`{part}`" for part in parts)

    @staticmethod
    def literal(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "TRUE" if value else "FALSE"
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    def predicate(self, item: dict[str, Any], alias: str | None = None) -> str:
        column = self.identifier(item["column"])
        if alias:
            column = f"{self.identifier(alias)}.{column}"
        operator = str(item.get("operator", "eq")).lower()
        if operator not in self.OPERATORS:
            raise ValueError(f"Unsupported filter operator: {operator}")
        sql_operator = self.OPERATORS[operator]
        if operator in {"is_null", "not_null"}:
            return f"{column} {sql_operator}"
        value = item.get("value")
        if operator in {"in", "not_in"}:
            if not isinstance(value, list) or not value:
                raise ValueError(f"{operator} requires a non-empty list")
            rendered = ", ".join(self.literal(entry) for entry in value)
            return f"{column} {sql_operator} ({rendered})"
        return f"{column} {sql_operator} {self.literal(value)}"

    def where(self, filters: list[dict[str, Any]], extra: str | None = None) -> str:
        predicates = [self.predicate(item) for item in filters]
        if extra:
            predicates.append(extra)
        return " WHERE " + " AND ".join(predicates) if predicates else ""

    def _columns(self, rule: RuleSpec, side: str) -> list[str]:
        return rule.source_columns if side == "source" else rule.target_columns

    def compile_rule(
        self,
        rule: RuleSpec,
        pair: TablePair,
        side: Literal["source", "target"],
        filters: list[dict[str, Any]] | None = None,
    ) -> str:
        qualified = pair.source_name if side == "source" else pair.target_name
        if not qualified:
            raise ValueError(f"No {side} table is configured for {pair.pair_id}")
        table = self.table(qualified)
        columns = [self.identifier(name) for name in self._columns(rule, side)]
        where = self.where(filters or [])
        rule_type = rule.type
        if rule_type == "row_count":
            return f"SELECT COUNT(*) AS row_count FROM {table}{where}"
        if rule_type == "null_count":
            return f"SELECT SUM(CASE WHEN {columns[0]} IS NULL THEN 1 ELSE 0 END) AS null_count FROM {table}{where}"
        if rule_type == "distinct_count":
            return f"SELECT COUNT(DISTINCT {columns[0]}) AS distinct_count FROM {table}{where}"
        if rule_type == "column_profile":
            metrics = set(rule.parameters.get("metrics") or [])
            if rule.parameters.get("include_min_max"):
                metrics.update({"min_value", "max_value"})
            selected = [
                "COUNT(*) AS row_count",
                f"SUM(CASE WHEN {columns[0]} IS NULL THEN 1 ELSE 0 END) AS null_count",
                f"COUNT(DISTINCT {columns[0]}) AS distinct_count",
            ]
            if "min_value" in metrics:
                selected.append(f"MIN({columns[0]}) AS min_value")
            if "max_value" in metrics:
                selected.append(f"MAX({columns[0]}) AS max_value")
            if "average_value" in metrics:
                selected.append(f"AVG({columns[0]}) AS average_value")
            if "sum_value" in metrics:
                selected.append(f"SUM({columns[0]}) AS sum_value")
            return f"SELECT {', '.join(selected)} FROM {table}{where}"
        if rule_type == "uniqueness":
            grouped = ", ".join(columns)
            inner = f"SELECT {grouped}, COUNT(*) AS n FROM {table}{where} GROUP BY {grouped} HAVING COUNT(*) > 1"
            return f"SELECT COUNT(*) AS duplicate_groups, COALESCE(SUM(n - 1), 0) AS duplicate_rows FROM ({inner}) dq_duplicates"
        if rule_type == "freshness":
            return f"SELECT MAX({columns[0]}) AS max_timestamp FROM {table}{where}"
        if rule_type == "aggregate":
            function = str(rule.parameters.get("aggregate", "sum")).upper()
            if function not in {"SUM", "MIN", "MAX", "AVG"}:
                raise ValueError(f"Unsupported aggregate: {function}")
            return f"SELECT {function}({columns[0]}) AS metric_value FROM {table}{where}"
        if rule_type == "grouped_profile":
            group_items = list(rule.parameters.get(f"{side}_group_by") or [])
            metric_items = list(rule.parameters.get("metrics") or [])
            joins = list(rule.parameters.get(f"{side}_joins") or [])
            if not group_items or not metric_items:
                raise ValueError("grouped_profile requires group-by columns and metrics")
            from_sql = f"{table} b"
            for index, join in enumerate(joins, 1):
                alias = f"j{index}"
                join_table = self.table(str(join["table"]))
                local = list(join.get("local_columns") or [])
                remote = list(join.get("remote_columns") or [])
                if not local or len(local) != len(remote):
                    raise ValueError("Approved grouped-profile joins require equal-width keys")
                predicates = [
                    f"b.{self.identifier(left)} = {alias}.{self.identifier(right)}"
                    for left, right in zip(local, remote)
                ]
                predicates.extend(self.predicate(item, alias=alias) for item in join.get("filters", []))
                from_sql += f" JOIN {join_table} {alias} ON " + " AND ".join(predicates)
            selected_groups: list[str] = []
            grouped_expressions: list[str] = []
            for index, item in enumerate(group_items, 1):
                if isinstance(item, dict):
                    column = self.identifier(str(item["column"]))
                    join_index = int(item.get("join_index", 0))
                    expression = f"j{join_index}.{column}" if join_index else f"b.{column}"
                else:
                    expression = f"b.{self.identifier(str(item))}"
                selected_groups.append(f"{expression} AS group_{index}")
                grouped_expressions.append(expression)
            selected_metrics: list[str] = []
            for index, metric in enumerate(metric_items, 1):
                function = str(metric.get("aggregation", "count")).lower()
                column = metric.get(f"{side}_column") or metric.get("column")
                if function == "count" and not column:
                    expression = "COUNT(*)"
                else:
                    if function not in {"count", "distinct_count", "sum", "avg", "min", "max"}:
                        raise ValueError(f"Unsupported grouped metric aggregation: {function}")
                    rendered = f"b.{self.identifier(str(column))}"
                    expression = f"COUNT(DISTINCT {rendered})" if function == "distinct_count" else f"{function.upper()}({rendered})"
                selected_metrics.append(f"{expression} AS metric_{index}")
            predicates = [self.predicate(item, alias="b") for item in (filters or [])]
            predicates.extend(self.predicate(item, alias="b") for item in rule.parameters.get(f"{side}_filters", []))
            grouped_where = " WHERE " + " AND ".join(predicates) if predicates else ""
            return (
                f"SELECT {', '.join([*selected_groups, *selected_metrics])} FROM {from_sql}"
                f"{grouped_where} GROUP BY {', '.join(grouped_expressions)} "
                f"ORDER BY {', '.join(grouped_expressions)} LIMIT {int(rule.parameters.get('limit', 500))}"
            )
        if rule_type == "date_coverage":
            return f"SELECT MIN({columns[0]}) AS min_value, MAX({columns[0]}) AS max_value FROM {table}{where}"
        if rule_type == "value_distribution":
            limit = min(int(rule.parameters.get("limit", 20)), 200)
            if self.dialect == "bigquery":
                value = f"TO_HEX(MD5(COALESCE(CAST({columns[0]} AS STRING), '<NULL>')))"
            else:
                value = f"MD5(COALESCE(CAST({columns[0]} AS STRING), '<NULL>'))"
            return (
                f"SELECT {value} AS value_hash, COUNT(*) AS value_count FROM {table}{where} "
                f"GROUP BY value_hash ORDER BY value_count DESC LIMIT {limit}"
            )
        if rule_type == "domain":
            values = rule.parameters.get("values") or []
            if not values:
                raise ValueError(f"Domain rule {rule.rule_id} has no accepted values")
            invalid = f"{columns[0]} IS NOT NULL AND {columns[0]} NOT IN ({', '.join(self.literal(v) for v in values)})"
            return f"SELECT SUM(CASE WHEN {invalid} THEN 1 ELSE 0 END) AS invalid_count FROM {table}{where}"
        if rule_type == "predicate":
            predicate = self.predicate(rule.parameters["predicate"])
            failure = f"NOT ({predicate})"
            return f"SELECT SUM(CASE WHEN {failure} THEN 1 ELSE 0 END) AS invalid_count FROM {table}{where}"
        if rule_type == "key_buckets":
            return self.key_buckets(qualified, [name.strip("`") for name in columns], filters or [], int(rule.parameters.get("buckets", 256)))
        if rule_type == "key_values":
            return self.key_values(
                qualified, [name.strip("`") for name in columns], filters or [],
                int(rule.parameters.get("limit", 10_000)),
            )
        if rule_type == "top_duplicates":
            digest = self._key_digest([name.strip("`") for name in columns])
            return f"SELECT {digest} AS key_hash, COUNT(*) AS duplicate_count FROM {table}{where} GROUP BY key_hash HAVING COUNT(*) > 1 ORDER BY duplicate_count DESC LIMIT {int(rule.parameters.get('limit', 200))}"
        if rule_type == "relationship":
            if side != "target":
                raise ValueError("Relationship checks are target-side checks")
            return self.relationship_query(rule, pair, filters or [])
        if rule_type == "custom_sql":
            supplied = rule.source_sql if side == "source" else rule.target_sql
            if not supplied:
                raise ValueError(f"No {side}_sql supplied for custom_sql rule {rule.rule_id}")
            return supplied.replace("{{ table }}", table)
        raise ValueError(f"Unsupported rule type: {rule_type}")

    def row_values(
        self,
        qualified_name: str,
        key_columns: list[str],
        value_columns: list[str],
        filters: list[dict[str, Any]],
        limit: int,
    ) -> str:
        if not key_columns or not value_columns:
            raise ValueError("Row reconciliation requires key and mapped value columns")
        if not 1 <= limit <= 100_000:
            raise ValueError("Row reconciliation limit must be between 1 and 100000")
        selected = [
            *(f"{self.identifier(name)} AS key_{index}" for index, name in enumerate(key_columns, 1)),
            *(f"{self.identifier(name)} AS value_{index}" for index, name in enumerate(value_columns, 1)),
        ]
        return (
            f"SELECT {', '.join(selected)}, COUNT(*) OVER () AS total_rows "
            f"FROM {self.table(qualified_name)}{self.where(filters)} "
            f"ORDER BY key_1 LIMIT {int(limit)}"
        )

    def key_buckets(
        self,
        qualified_name: str,
        columns: list[str],
        filters: list[dict[str, Any]],
        buckets: int,
    ) -> str:
        table = self.table(qualified_name)
        digest = self._key_digest(columns)
        prefix_width = max(1, min(4, len(f"{buckets - 1:x}")))
        bucket = f"SUBSTR({digest}, 1, {prefix_width})"
        where = self.where(filters)
        return f"SELECT {bucket} AS key_bucket, COUNT(*) AS row_count FROM {table}{where} GROUP BY key_bucket ORDER BY key_bucket"

    def key_values(
        self,
        qualified_name: str,
        columns: list[str],
        filters: list[dict[str, Any]],
        limit: int,
    ) -> str:
        if not 1 <= limit <= 100_000:
            raise ValueError("key_values limit must be between 1 and 100000")
        table = self.table(qualified_name)
        digest = self._key_digest(columns)
        where = self.where(filters)
        return (
            f"SELECT {digest} AS key_hash, COUNT(*) OVER () AS total_rows "
            f"FROM {table}{where} ORDER BY key_hash LIMIT {limit}"
        )

    def key_hashes_for_buckets(
        self,
        qualified_name: str,
        columns: list[str],
        filters: list[dict[str, Any]],
        buckets: list[str],
        limit: int,
    ) -> str:
        if not buckets:
            raise ValueError("At least one mismatched bucket is required")
        table = self.table(qualified_name)
        digest = self._key_digest(columns)
        width = len(buckets[0])
        safe_buckets = [bucket for bucket in buckets if re.fullmatch(r"[0-9a-fA-F]+", bucket)]
        if len(safe_buckets) != len(buckets):
            raise ValueError("Invalid key bucket value")
        bucket_values = ", ".join(self.literal(bucket) for bucket in safe_buckets)
        where = self.where(filters, f"SUBSTR({digest}, 1, {width}) IN ({bucket_values})")
        return f"SELECT {digest} AS key_hash FROM {table}{where} ORDER BY key_hash LIMIT {int(limit)}"

    def _key_digest(self, columns: list[str]) -> str:
        rendered = [self.identifier(column) for column in columns]
        if self.dialect == "bigquery":
            values: list[str] = []
            for index, column in enumerate(rendered):
                if index:
                    values.append("'|'")
                values.append(f"COALESCE(CAST({column} AS STRING), '<NULL>')")
            return f"TO_HEX(MD5(CONCAT({', '.join(values)})))"
        values = ", ".join(f"COALESCE(CAST({column} AS STRING), '<NULL>')" for column in rendered)
        return f"MD5(CONCAT_WS('|', {values}))"

    def relationship_query(
        self,
        rule: RuleSpec,
        pair: TablePair,
        filters: list[dict[str, Any]],
    ) -> str:
        referenced = self.table(rule.parameters["referenced_table"])
        local_columns = [self.identifier(name) for name in rule.target_columns]
        remote_columns = [self.identifier(name) for name in rule.parameters["referenced_columns"]]
        if len(local_columns) != len(remote_columns):
            raise ValueError("Relationship local and referenced column counts differ")
        join = " AND ".join(f"l.{local} = r.{remote}" for local, remote in zip(local_columns, remote_columns))
        referenced_filters = list(rule.parameters.get("referenced_filters", []))
        active = rule.parameters.get("active_filter")
        if active and not referenced_filters:
            referenced_filters.append(active)
        for item in referenced_filters:
            join += " AND " + self.predicate(item, alias="r")
        child_filters = [*filters, *rule.parameters.get("child_filters", [])]
        local_where = self.where(child_filters)
        not_null = " AND ".join(f"l.{column} IS NOT NULL" for column in local_columns)
        missing = f"r.{remote_columns[0]} IS NULL"
        combined = " WHERE " + " AND ".join(filter(None, [local_where.removeprefix(" WHERE "), not_null]))
        return (
            f"SELECT COUNT(*) AS checked_count, "
            f"COALESCE(SUM(CASE WHEN {missing} THEN 1 ELSE 0 END), 0) AS orphan_count, "
            f"COALESCE(SUM(CASE WHEN NOT ({missing}) THEN 1 ELSE 0 END), 0) AS matched_count "
            f"FROM {self.table(pair.target_name)} l "
            f"LEFT JOIN {referenced} r ON {join}{combined}"
        )


def allowed_tables_for_rule(rule: RuleSpec, pair: TablePair, side: str) -> set[str]:
    tables = {pair.source_name if side == "source" else pair.target_name}
    if rule.type == "relationship":
        tables.add(rule.parameters["referenced_table"])
    if rule.type == "grouped_profile":
        tables.update(str(item["table"]) for item in rule.parameters.get(f"{side}_joins", []))
    return {table for table in tables if table}


def normalized_type(data_type: str) -> str:
    value = re.sub(r"\(.*\)", "", data_type.upper()).strip()
    groups = {
        "integer": {"BYTEINT", "SMALLINT", "INT", "INTEGER", "BIGINT", "LONG", "INT64"},
        "decimal": {"DECIMAL", "NUMERIC", "BIGNUMERIC", "FLOAT", "DOUBLE", "FLOAT64", "REAL"},
        "string": {"STRING", "VARCHAR", "CHAR", "TEXT"},
        "boolean": {"BOOLEAN", "BOOL"},
        "date": {"DATE"},
        "timestamp": {"TIMESTAMP", "TIMESTAMP_NTZ", "DATETIME"},
        "binary": {"BINARY", "BYTES"},
    }
    for group, values in groups.items():
        if value in values:
            return group
    return value.lower()
