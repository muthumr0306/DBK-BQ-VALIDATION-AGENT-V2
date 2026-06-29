from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import AppConfig, TablePair
from .context_utils import make_context_record
from .query_engine import SQLCompiler, normalized_type


MEASURE_WORDS = {
    "amount", "balance", "cost", "count", "discount", "margin", "price",
    "profit", "quantity", "qty", "revenue", "sales", "total", "units", "volume",
}
KPI_WORDS = {"margin", "percent", "percentage", "rate", "ratio", "score"}
AVERAGE_WORDS = {"average", "avg", "margin", "percent", "percentage", "price", "rate", "ratio"}
EXCLUDED_WORDS = {
    "code", "day", "flag", "id", "key", "month", "number", "rank", "sequence",
    "sid", "status", "version", "year",
}
EXPRESSION_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*|\d+(?:\.\d+)?|[()+\-*/]")
SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _tokens(value: Any) -> set[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
    return {item.lower() for item in re.split(r"[^A-Za-z0-9]+", text) if item}


class MeasureSettings(BaseModel):
    inference_enabled: bool = True
    profile_values: bool = True
    propose_inferred_for_reuse: bool = True
    max_inferred_per_table: int = Field(default=8, ge=1, le=50)
    max_groupings_per_measure: int = Field(default=6, ge=1, le=20)
    maximum_group_cardinality: int = Field(default=500, ge=10, le=10_000)


class DiagnosticSettings(BaseModel):
    maximum_steps_per_failure: int = Field(default=3, ge=1, le=10)
    maximum_llm_calls_per_failure: int = Field(default=1, ge=1, le=10)
    maximum_failed_samples: int = Field(default=50, ge=1, le=500)
    allowed_types: list[str] = Field(default_factory=lambda: [
        "date_distribution", "aggregate_by_dimension", "null_distribution",
    ])

    @field_validator("allowed_types")
    @classmethod
    def validate_types(cls, value: list[str]) -> list[str]:
        supported = {"date_distribution", "aggregate_by_dimension", "null_distribution"}
        invalid = sorted(set(value) - supported)
        if invalid:
            raise ValueError(f"Unsupported diagnostic types: {invalid}")
        return value


class MeasureDefinition(BaseModel):
    measure_id: str
    definition_type: Literal["measure", "kpi"] = "measure"
    pair_id: str | None = None
    source_table: str | None = None
    target_table: str | None = None
    source_expression: str | None = None
    target_expression: str
    business_name: str | None = None
    description: str | None = None
    aggregation: Literal["sum", "avg", "min", "max", "count"] = "sum"
    tolerance_absolute: float = Field(default=0.0, ge=0)
    tolerance_percentage: float = Field(default=0.0, ge=0)
    currency: str | None = None
    unit: str | None = None
    date_column: dict[str, str] = Field(default_factory=dict)
    group_by: dict[str, list[str]] = Field(default_factory=dict)
    source_filters: list[dict[str, Any]] = Field(default_factory=list)
    target_filters: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = True
    severity: str = "error"
    approval_status: Literal["approved", "pending", "disabled"] = "approved"

    @field_validator("source_expression", "target_expression")
    @classmethod
    def validate_expression(cls, value: str | None) -> str | None:
        if value is not None:
            expression_columns(value)
        return value

    @model_validator(mode="after")
    def validate_groups(self) -> "MeasureDefinition":
        source = self.group_by.get("source", [])
        target = self.group_by.get("target", [])
        if source and len(source) != len(target):
            raise ValueError(f"Measure {self.measure_id} has different source/target grouping widths")
        return self


class MeasureConfiguration(BaseModel):
    settings: MeasureSettings = Field(default_factory=MeasureSettings)
    diagnostics: DiagnosticSettings = Field(default_factory=DiagnosticSettings)
    measures: list[MeasureDefinition] = Field(default_factory=list)


class DiagnosticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_type: Literal["date_distribution", "aggregate_by_dimension", "null_distribution"]
    purpose: str
    grouping_id: str | None = None
    granularity: Literal["year", "month"] | None = None


def load_measure_configuration(config: AppConfig) -> MeasureConfiguration:
    path = config.path(config.project.measures)
    if not path.exists():
        return MeasureConfiguration()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return MeasureConfiguration.model_validate(raw)


def expression_columns(expression: str) -> list[str]:
    if any(marker in expression for marker in ("--", "/*", "*/", "#", ";")):
        raise ValueError("SQL comments and statement separators are not allowed in measure expressions")
    compact = re.sub(r"\s+", "", expression)
    tokens = EXPRESSION_TOKEN.findall(expression)
    if not compact or "".join(tokens) != compact:
        raise ValueError(
            "Measure expressions may contain only column identifiers, numeric literals, parentheses, and + - * /"
        )
    balance = 0
    expect_operand = True
    columns: list[str] = []
    for index, token in enumerate(tokens):
        if token == "(" and expect_operand:
            balance += 1
        elif token == ")" and not expect_operand:
            balance -= 1
            if balance < 0:
                raise ValueError("Measure expression has unbalanced parentheses")
        elif token in {"+", "-", "*", "/"}:
            if expect_operand and token != "-":
                raise ValueError("Measure expression has an operator without a left operand")
            if not expect_operand:
                expect_operand = True
        elif SAFE_IDENTIFIER.fullmatch(token) or re.fullmatch(r"\d+(?:\.\d+)?", token):
            if not expect_operand:
                raise ValueError("Measure expression has adjacent operands")
            if SAFE_IDENTIFIER.fullmatch(token):
                if index + 1 < len(tokens) and tokens[index + 1] == "(":
                    raise ValueError("Functions are not allowed in measure expressions")
                if token not in columns:
                    columns.append(token)
            expect_operand = False
        else:
            raise ValueError(f"Unsupported measure expression token: {token}")
    if balance != 0 or expect_operand:
        raise ValueError("Measure expression has unbalanced parentheses")
    if not columns:
        raise ValueError("Measure expression must reference at least one column")
    return columns


def render_expression(expression: str, compiler: SQLCompiler) -> str:
    tokens = EXPRESSION_TOKEN.findall(expression)
    return " ".join(compiler.identifier(token) if SAFE_IDENTIFIER.fullmatch(token) else token for token in tokens)


def measure_profile_sql(table: str, column: str) -> str:
    compiler = SQLCompiler("bigquery")
    rendered = compiler.identifier(column)
    return (
        f"SELECT COUNT(*) AS row_count, COUNTIF({rendered} IS NULL) AS null_count, "
        f"COUNT(DISTINCT {rendered}) AS distinct_count, MIN({rendered}) AS minimum_value, "
        f"MAX({rendered}) AS maximum_value FROM {compiler.table(table)}"
    )


def infer_measure_candidates(
    pair: TablePair,
    metadata: dict[str, Any],
    mappings: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    maximum: int,
) -> list[dict[str, Any]]:
    mapping_by_target = {str(row["target_column"]).lower(): row for row in mappings}
    output: list[dict[str, Any]] = []
    for column in metadata.get("target", []):
        name = str(column["name"])
        name_tokens = _tokens(name)
        kind = normalized_type(str(column.get("data_type") or ""))
        if kind not in {"integer", "decimal"}:
            continue
        excluded = bool(name_tokens & EXCLUDED_WORDS) or any(
            name.lower().endswith(suffix) for suffix in ("_id", "_sid", "_key", "_code", "_year", "_seq")
        )
        measure_hits = name_tokens & MEASURE_WORDS
        kpi_hits = name_tokens & KPI_WORDS
        description_tokens = _tokens(column.get("description"))
        description_hits = description_tokens & MEASURE_WORDS
        description_kpi_hits = description_tokens & KPI_WORDS
        if excluded and not measure_hits and not kpi_hits:
            continue
        if not measure_hits and not description_hits and not kpi_hits and not description_kpi_hits:
            continue
        profile = profiles.get(name, {})
        rows = int(profile.get("row_count") or 0)
        distinct = int(profile.get("distinct_count") or 0)
        nulls = int(profile.get("null_count") or 0)
        distinct_ratio = distinct / rows if rows else 0.0
        non_null_ratio = (rows - nulls) / rows if rows else 0.0
        name_score = 0.45 if (measure_hits or kpi_hits) else 0.20
        type_score = 0.20 if kind == "decimal" else 0.14
        description_score = min(0.15, 0.05 * len(description_hits | description_kpi_hits))
        profile_score = 0.10 * non_null_ratio + (0.05 if distinct_ratio < 0.98 else 0.0)
        mapped = mapping_by_target.get(name.lower())
        mapping_score = 0.05 if pair.mode != "migration" or mapped else 0.0
        confidence = round(min(1.0, name_score + type_score + description_score + profile_score + mapping_score), 4)
        definition_type = "kpi" if (kpi_hits or description_kpi_hits) else "measure"
        aggregation = "avg" if definition_type == "kpi" or (name_tokens | description_tokens) & AVERAGE_WORDS else "sum"
        output.append({
            "measure_id": re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_"),
            "definition_type": definition_type,
            "pair_id": pair.pair_id,
            "source_table": pair.source_name,
            "target_table": pair.target_name,
            "source_expression": mapped["source_column"] if mapped else (name if pair.mode != "migration" else None),
            "target_expression": name,
            "business_name": str(column.get("description") or name.replace("_", " ").title()),
            "description": column.get("description"),
            "aggregation": aggregation,
            "tolerance_absolute": 0.0,
            "tolerance_percentage": 0.5,
            "date_column": {},
            "group_by": {},
            "source_filters": [],
            "target_filters": [],
            "enabled": True,
            "severity": "error",
            "approval_status": "inferred",
            "origin": "inference",
            "confidence": confidence,
            "evidence": {
                "data_type": column.get("data_type"),
                "measure_name_tokens": sorted(measure_hits),
                "kpi_name_tokens": sorted(kpi_hits),
                "description_tokens": sorted(description_hits),
                "description_kpi_tokens": sorted(description_kpi_hits),
                "profile": profile,
                "distinct_ratio": round(distinct_ratio, 4),
                "non_null_ratio": round(non_null_ratio, 4),
                "score_components": {
                    "name": round(name_score, 4), "type": round(type_score, 4),
                    "description": round(description_score, 4), "profile": round(profile_score, 4),
                    "source_mapping": round(mapping_score, 4),
                },
            },
        })
    return sorted(output, key=lambda row: (-float(row["confidence"]), row["measure_id"]))[:maximum]


def definition_payload(definition: MeasureDefinition, origin: str, confidence: float = 1.0) -> dict[str, Any]:
    return {**definition.model_dump(), "origin": origin, "confidence": confidence}


def resolve_measure_precedence(
    pair: TablePair,
    configuration: MeasureConfiguration,
    table_context: dict[str, Any],
    inferred: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    priorities = {"manual": 4, "trusted": 3, "business_context": 2, "inference": 1}
    candidates: list[dict[str, Any]] = []
    selectors = {pair.pair_id, pair.source_name, pair.target_name, None}
    for definition in configuration.measures:
        if definition.pair_id not in selectors:
            continue
        if definition.source_table and definition.source_table != pair.source_name:
            continue
        if definition.target_table and definition.target_table != pair.target_name:
            continue
        candidates.append(definition_payload(definition, "manual"))
    for raw in table_context.get("measures", []):
        item = dict(raw)
        item.setdefault("measure_id", item.get("target_column") or item.get("target_expression"))
        item.setdefault("target_expression", item.get("target_column"))
        item.setdefault("source_expression", item.get("source_column", item.get("target_expression")))
        item.setdefault("aggregation", item.get("aggregate", "sum"))
        tolerance = item.get("tolerance", {})
        item.setdefault("tolerance_absolute", tolerance.get("absolute", 0))
        item.setdefault("tolerance_percentage", float(tolerance.get("percentage", 0)) * 100)
        item.setdefault("enabled", True)
        item.setdefault("approval_status", "approved")
        item.setdefault("origin", "trusted" if item.get("origin") == "trusted" else "business_context")
        item.setdefault("confidence", 1.0)
        candidates.append(item)
    candidates.extend(inferred)

    selected: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        measure_id = str(candidate.get("measure_id") or "").lower()
        if not measure_id:
            continue
        expression = re.sub(r"\s+", "", str(candidate.get("target_expression") or measure_id)).lower()
        identity = expression or measure_id
        current = selected.get(identity)
        if current is None or priorities.get(str(candidate.get("origin")), 0) > priorities.get(str(current.get("origin")), 0):
            selected[identity] = candidate
    return [row for row in selected.values() if row.get("enabled", True) and row.get("approval_status") != "disabled"]


def validate_measure_metadata(
    measure: dict[str, Any],
    pair: TablePair,
    metadata: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    for side in ("source", "target"):
        if side == "source" and pair.mode != "migration":
            continue
        expression = measure.get(f"{side}_expression")
        if not expression:
            errors.append(f"Missing {side}_expression")
            continue
        available = {str(item["name"]).lower() for item in metadata.get(side, [])}
        for column in expression_columns(str(expression)):
            if column.lower() not in available:
                errors.append(f"{side} expression column does not exist: {column}")
        for column in (measure.get("group_by") or {}).get(side, []):
            if str(column).lower() not in available:
                errors.append(f"{side} grouping column does not exist: {column}")
        date_column = (measure.get("date_column") or {}).get(side)
        if date_column and str(date_column).lower() not in available:
            errors.append(f"{side} date column does not exist: {date_column}")
        for filter_item in measure.get(f"{side}_filters", []):
            if str(filter_item.get("column") or "").lower() not in available:
                errors.append(f"{side} filter column does not exist: {filter_item.get('column')}")
    return errors


def select_reconciliation_groups(
    measure: dict[str, Any],
    metadata: dict[str, Any],
    mappings: list[dict[str, Any]],
    maximum: int,
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = [{"grouping_id": "overall", "source": [], "target": [], "granularity": None}]
    configured_source = list((measure.get("group_by") or {}).get("source", []))
    configured_target = list((measure.get("group_by") or {}).get("target", []))
    if configured_target and len(configured_source) == len(configured_target):
        groups.append({
            "grouping_id": "configured", "source": configured_source,
            "target": configured_target, "granularity": None,
        })
    date_columns = measure.get("date_column") or {}
    if date_columns.get("target") and date_columns.get("source"):
        for granularity in ("date", "year", "month"):
            groups.append({
                "grouping_id": f"by_{granularity}", "source": [date_columns["source"]],
                "target": [date_columns["target"]], "granularity": granularity,
            })
    mapping_by_target = {str(row["target_column"]).lower(): str(row["source_column"]) for row in mappings}
    target_columns = {str(row["name"]).lower(): row for row in metadata.get("target", [])}
    useful = ("brand", "market", "channel", "source_system")
    for target_name, item in target_columns.items():
        normalized = target_name.replace("_", "")
        label = next((name for name in useful if name.replace("_", "") in normalized), None)
        source_name = mapping_by_target.get(target_name)
        if not label or not source_name:
            continue
        if target_name in {name.lower() for name in expression_columns(str(measure["target_expression"]))}:
            continue
        groups.append({
            "grouping_id": f"by_{label}", "source": [source_name],
            "target": [item["name"]], "granularity": None,
        })
    unique: dict[tuple[tuple[str, ...], tuple[str, ...], Any], dict[str, Any]] = {}
    for group in groups:
        key = (tuple(group["source"]), tuple(group["target"]), group["granularity"])
        unique.setdefault(key, group)
    return list(unique.values())[:maximum]


def _group_expression(column: str, granularity: str | None, compiler: SQLCompiler) -> str:
    rendered = compiler.identifier(column)
    if granularity == "date":
        return f"CAST({rendered} AS DATE)"
    if granularity == "year":
        return f"EXTRACT(YEAR FROM {rendered})"
    if granularity == "month":
        return f"EXTRACT(MONTH FROM {rendered})"
    return rendered


def reconciliation_sql(
    measure: dict[str, Any],
    pair: TablePair,
    side: Literal["source", "target"],
    grouping: dict[str, Any],
    base_filters: list[dict[str, Any]],
    maximum_cardinality: int,
) -> str:
    dialect = "databricks" if side == "source" else "bigquery"
    compiler = SQLCompiler(dialect)
    table_name = pair.source_name if side == "source" else pair.target_name
    if not table_name:
        raise ValueError(f"Measure reconciliation requires a {side} table")
    expression = measure.get(f"{side}_expression")
    if not expression:
        raise ValueError(f"Measure {measure['measure_id']} has no {side}_expression")
    rendered_measure = render_expression(str(expression), compiler)
    aggregate = str(measure.get("aggregation", "sum")).upper()
    if aggregate not in {"SUM", "AVG", "MIN", "MAX", "COUNT"}:
        raise ValueError(f"Unsupported measure aggregation: {aggregate}")
    columns = grouping.get(side, [])
    group_expressions = [
        _group_expression(column, grouping.get("granularity"), compiler) for column in columns
    ]
    select_groups = ", ".join(
        f"{expression} AS group_{index + 1}" for index, expression in enumerate(group_expressions)
    )
    select_prefix = f"{select_groups}, " if select_groups else ""
    filters = [*base_filters, *measure.get(f"{side}_filters", [])]
    where = compiler.where(filters)
    group_by = f" GROUP BY {', '.join(str(index + 1) for index in range(len(group_expressions)))}" if group_expressions else ""
    limit = f" LIMIT {maximum_cardinality + 1}" if group_expressions else ""
    return (
        f"SELECT {select_prefix}{aggregate}({rendered_measure}) AS measure_value, COUNT(*) AS row_count "
        f"FROM {compiler.table(table_name)}{where}{group_by}{limit}"
    )


def compare_reconciliation_results(
    measure: dict[str, Any],
    grouping: dict[str, Any],
    source: pd.DataFrame,
    target: pd.DataFrame,
) -> list[dict[str, Any]]:
    group_columns = [column for column in source.columns if column.startswith("group_")]
    if group_columns:
        for frame in (source, target):
            for column in group_columns:
                if column not in frame:
                    frame[column] = None
                frame[column] = frame[column].astype("string").fillna("<NULL>")
        combined = source.merge(target, on=group_columns, how="outer", suffixes=("_source", "_target"))
    else:
        source_row = source.iloc[0].to_dict() if not source.empty else {}
        target_row = target.iloc[0].to_dict() if not target.empty else {}
        combined = pd.DataFrame([{
            "measure_value_source": source_row.get("measure_value"),
            "row_count_source": source_row.get("row_count"),
            "measure_value_target": target_row.get("measure_value"),
            "row_count_target": target_row.get("row_count"),
        }])
    def _num(value: Any) -> float:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return 0.0
        return float(value)

    rows: list[dict[str, Any]] = []
    for raw in combined.to_dict("records"):
        source_value = _num(raw.get("measure_value_source"))
        target_value = _num(raw.get("measure_value_target"))
        difference = abs(source_value - target_value)
        percentage = difference / abs(source_value) * 100 if source_value else (0.0 if target_value == 0 else 100.0)
        allowed_absolute = float(measure.get("tolerance_absolute") or 0)
        allowed_percentage = float(measure.get("tolerance_percentage") or 0)
        passed = difference <= allowed_absolute or percentage <= allowed_percentage
        rows.append({
            "measure_id": measure["measure_id"], "grouping_id": grouping["grouping_id"],
            "group_values": {column: raw.get(column) for column in group_columns},
            "source_result": source_value, "target_result": target_value,
            "source_rows": int(_num(raw.get("row_count_source"))),
            "target_rows": int(_num(raw.get("row_count_target"))),
            "absolute_difference": round(difference, 6),
            "percentage_difference": round(percentage, 6),
            "tolerance_absolute": allowed_absolute, "tolerance_percentage": allowed_percentage,
            "status": "PASS" if passed else "FAIL",
        })
    return rows


def validate_diagnostic_request(
    request: DiagnosticRequest,
    measure: dict[str, Any],
    groupings: list[dict[str, Any]],
    settings: DiagnosticSettings,
) -> None:
    if request.query_type not in settings.allowed_types:
        raise ValueError(f"Diagnostic type is not allowed: {request.query_type}")
    if request.query_type == "date_distribution":
        dates = measure.get("date_column") or {}
        if not dates.get("source") or not dates.get("target"):
            raise ValueError("date_distribution requires approved source and target date columns")
    if request.query_type == "aggregate_by_dimension":
        valid = {item["grouping_id"] for item in groupings if item["grouping_id"] != "overall"}
        if request.grouping_id not in valid:
            raise ValueError(f"Unknown or unapproved grouping: {request.grouping_id}")


def diagnostic_sql(
    request: DiagnosticRequest,
    measure: dict[str, Any],
    pair: TablePair,
    side: Literal["source", "target"],
    groupings: list[dict[str, Any]],
    base_filters: list[dict[str, Any]],
    maximum_cardinality: int,
) -> str:
    if request.query_type == "date_distribution":
        dates = measure["date_column"]
        grouping = {
            "grouping_id": f"diagnostic_by_{request.granularity or 'year'}",
            "source": [dates["source"]], "target": [dates["target"]],
            "granularity": request.granularity or "year",
        }
        return reconciliation_sql(measure, pair, side, grouping, base_filters, maximum_cardinality)
    if request.query_type == "aggregate_by_dimension":
        grouping = next(item for item in groupings if item["grouping_id"] == request.grouping_id)
        return reconciliation_sql(measure, pair, side, grouping, base_filters, maximum_cardinality)
    compiler = SQLCompiler("databricks" if side == "source" else "bigquery")
    table = pair.source_name if side == "source" else pair.target_name
    expression = render_expression(str(measure[f"{side}_expression"]), compiler)
    filters = [*base_filters, *measure.get(f"{side}_filters", [])]
    return (
        f"SELECT COUNT(*) AS row_count, SUM(CASE WHEN ({expression}) IS NULL THEN 1 ELSE 0 END) AS null_count "
        f"FROM {compiler.table(table or '')}{compiler.where(filters)}"
    )


def measure_context_record(
    measure: dict[str, Any],
    config: AppConfig,
    run_id: str,
    source_file: str,
    origin: str,
) -> dict[str, Any]:
    pair_id = str(measure["pair_id"])
    payload = {key: value for key, value in measure.items() if key not in {"evidence", "approval_status"}}
    return make_context_record(
        config, run_id, str(measure.get("definition_type", "measure")),
        f"{pair_id}:{measure['measure_id']}", payload,
        source_file, pair_id=pair_id,
        target_table=measure.get("target_table"), source_table=measure.get("source_table"),
        column_name=str(measure.get("target_expression") or ""),
        confidence=float(measure.get("confidence") or 0), origin=origin,
    )


def rca_context_record(
    rca: dict[str, Any],
    pair: TablePair,
    config: AppConfig,
    run_id: str,
) -> dict[str, Any]:
    return make_context_record(
        config, run_id, "rca_learning", f"{pair.pair_id}:{rca['measure_id']}:{rca.get('rule_id', rca['classification'])}",
        rca, f"workflow:{run_id}", pair_id=pair.pair_id,
        target_table=pair.target_name,
        source_table=pair.source_name, confidence=float(rca.get("confidence") or 0),
        origin="AGENT_INFERENCE",
    )
