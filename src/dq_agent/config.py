from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$-]*$")
HUMAN_TEST_TYPES = {
    "null_check", "accepted_values", "row_count", "duplicate_check",
    "freshness_check", "aggregate_check", "custom_sql",
}


def _clean(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "enabled"}


def _read_tabular(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path, dtype=object)
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype=object)
    raise ValueError(f"Expected an Excel or CSV file: {path}")


def read_yaml(path: Path, default: Any = None) -> Any:
    if not path.exists():
        if default is not None:
            return default
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


class ConfidenceConfig(BaseModel):
    auto_accept: float = 0.85
    review: float = 0.65

    @model_validator(mode="after")
    def check_order(self) -> "ConfidenceConfig":
        if not 0 <= self.review < self.auto_accept <= 1:
            raise ValueError("Confidence thresholds must satisfy 0 <= review < auto_accept <= 1")
        return self


class QueryLimits(BaseModel):
    max_result_rows: int = 10_000
    small_table_row_limit: int = 10_000
    max_bytes_billed: int = 10_000_000_000
    timeout_seconds: int = 300
    key_hash_buckets: int = 256
    evidence_rows: int = 200
    maximum_group_cardinality: int = 500


class ProfilingConfig(BaseModel):
    numeric_absolute_tolerance: float = 0.01
    numeric_percentage_tolerance: float = 0.5
    distribution_percentage_points: float = 1.0
    low_cardinality_limit: int = 50
    top_values: int = 20


class ContextStoreConfig(BaseModel):
    path: str = "context_layer/context.db"
    top_k: int = 8
    max_context_characters: int = Field(default=24_000, ge=2_000, le=200_000)
    fts_enabled: bool = True

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("context_store.path must be inside the project")
        return value


class ApprovalConfig(BaseModel):
    root: str = "approvals"

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("approvals.root must be inside the project")
        return value


class ProjectConfig(BaseModel):
    project_name: str = "dbx_bq_validation_poc"
    default_mode: Literal["migration", "bigquery_only"] = "migration"
    table_mappings: str = "inputs/table_mappings.xlsx"
    column_mappings: str = "inputs/column_mappings.xlsx"
    runtime_overrides: str = "inputs/runtime_overrides.yaml"
    human_tests: str = "inputs/human_tests.yaml"
    context_tables: str = "context_layer/tables.yaml"
    relationships: str = "context_layer/relationships.yaml"
    measures: str = "context_layer/measures.yaml"
    context_patterns: str = "context_layer/patterns.yaml"
    issue_patterns: str = "context_layer/issue_patterns.yaml"
    learned_context: str = "context_layer/learned_context.yaml"
    outputs_dir: str = "outputs"
    logs_dir: str = "logs"
    confidence: ConfidenceConfig = Field(default_factory=ConfidenceConfig)
    query_limits: QueryLimits = Field(default_factory=QueryLimits)
    profiling: ProfilingConfig = Field(default_factory=ProfilingConfig)
    context_store: ContextStoreConfig = Field(default_factory=ContextStoreConfig)
    approvals: ApprovalConfig = Field(default_factory=ApprovalConfig)
    max_rca_rounds: int = Field(default=3, ge=1, le=20)
    max_llm_calls_per_failure: int = Field(default=4, ge=1, le=50)
    fail_fast: bool = False
    log_level: str = "INFO"

class LLMConfig(BaseModel):
    backend: Literal["ollama", "vertex", "openai", "anthropic"] = "ollama"
    api_family: Literal["openai_compatible", "google_genai", "anthropic"] = "openai_compatible"
    model: str = "gemma3:4b"
    endpoint: str | None = "http://localhost:11434/v1"
    temperature: float = 0.0
    max_output_tokens: int = 8192
    project: str | None = None
    project_env: str = "GCP_PROJECT_ID"
    location: str = "us-central1"
    api_key_env: str | None = None
    base_url: str | None = None  # for OpenAI-compatible endpoints (e.g. Ollama)
    timeout_seconds: int = 90
    max_retries: int = 2
    preflight: bool = True

    @model_validator(mode="after")
    def validate_transport(self) -> "LLMConfig":
        if not self.model.strip():
            raise ValueError("llm.model is required")
        allowed = {
            "ollama": {"openai_compatible"},
            "openai": {"openai_compatible"},
            "anthropic": {"anthropic"},
            "vertex": {"google_genai", "anthropic", "openai_compatible"},
        }
        if self.api_family not in allowed[self.backend]:
            raise ValueError(f"api_family={self.api_family!r} is invalid for backend={self.backend!r}")
        if self.backend == "ollama" and not self.endpoint:
            raise ValueError("Ollama requires llm.endpoint")
        if self.backend == "vertex" and self.api_family == "openai_compatible" and not self.endpoint:
            raise ValueError("Vertex OpenAI-compatible transport requires llm.endpoint")
        return self

    @property
    def provider(self) -> str:
        return f"{self.backend}:{self.api_family}"

    @property
    def enabled(self) -> bool:
        return True

    def resolved_project(self) -> str | None:
        return self.project or os.getenv(self.project_env)


class TablePair(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pair_id: str
    enabled: bool = True
    mode: Literal["migration", "bigquery_only"] = "migration"
    source_catalog: str | None = None
    source_schema: str | None = None
    source_table: str | None = None
    target_project: str
    target_dataset: str
    target_table: str

    @field_validator(
        "pair_id", "source_catalog", "source_schema", "source_table",
        "target_project", "target_dataset", "target_table",
    )
    @classmethod
    def validate_identifiers(cls, value: str | None) -> str | None:
        if value is not None and not IDENTIFIER_RE.fullmatch(value):
            raise ValueError(f"Unsafe identifier: {value!r}")
        return value

    @model_validator(mode="after")
    def validate_source(self) -> "TablePair":
        if self.mode == "migration" and not all(
            [self.source_catalog, self.source_schema, self.source_table]
        ):
            raise ValueError("Migration rows require source_catalog, source_schema, and source_table")
        return self

    @property
    def source_name(self) -> str | None:
        if self.mode == "bigquery_only":
            return None
        return ".".join([self.source_catalog or "", self.source_schema or "", self.source_table or ""])

    @property
    def target_name(self) -> str:
        return ".".join([self.target_project, self.target_dataset, self.target_table])


class ColumnMapping(BaseModel):
    model_config = ConfigDict(extra="ignore")

    pair_id: str
    source_column: str
    target_column: str | None = None  # None/empty = source-only column with no target equivalent
    status: str = "approved"

    @field_validator("source_column")
    @classmethod
    def validate_source_column(cls, value: str) -> str:
        if not IDENTIFIER_RE.fullmatch(value):
            raise ValueError(f"Unsafe column identifier: {value!r}")
        return value

    @field_validator("target_column")
    @classmethod
    def validate_target_column(cls, value: str | None) -> str | None:
        if value and not IDENTIFIER_RE.fullmatch(value):
            raise ValueError(f"Unsafe column identifier: {value!r}")
        return value

    @property
    def source_only(self) -> bool:
        return not self.target_column


class HumanTest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(alias="test_id")
    pair_id: str
    enabled: bool = True
    type: str
    scope: Literal["source", "target", "both", "compare"] = "compare"
    column: str | None = None
    columns: list[str] = Field(default_factory=list)
    source_column: str | None = None
    target_column: str | None = None
    source_columns: list[str] = Field(default_factory=list)
    target_columns: list[str] = Field(default_factory=list)
    values: list[Any] = Field(default_factory=list)
    allowed_nulls: int = 0
    allowed_duplicates: int = 0
    max_delay_minutes: int = 1440
    aggregation: Literal["sum", "avg", "min", "max", "count"] = "sum"
    comparison: Literal["eq", "gte", "lte"] = "eq"
    group_by: list[str] = Field(default_factory=list)
    expected_value: Any = None
    tolerance_absolute: float = 0.0
    tolerance_percentage: float = 0.0
    severity: str = "error"
    source_sql: str | None = None
    target_sql: str | None = None
    description: str | None = None

    @model_validator(mode="after")
    def validate_test_definition(self) -> "HumanTest":
        aliases = {
            "null": "null_check", "accepted-values": "accepted_values",
            "row-count": "row_count", "duplicate": "duplicate_check",
            "freshness": "freshness_check", "aggregate": "aggregate_check",
            "custom-sql": "custom_sql",
            "null_count": "null_check", "domain": "accepted_values",
            "uniqueness": "duplicate_check", "freshness": "freshness_check",
            "aggregate": "aggregate_check",
        }
        self.type = aliases.get(self.type.strip().lower(), self.type.strip().lower())
        if self.type not in HUMAN_TEST_TYPES:
            raise ValueError(f"Unsupported human test type {self.type!r}; expected {sorted(HUMAN_TEST_TYPES)}")
        if self.type == "accepted_values" and not self.values:
            raise ValueError("accepted_values tests require values")
        if self.type == "custom_sql":
            if self.scope in {"source", "both", "compare"} and not self.source_sql:
                raise ValueError("custom_sql requires source_sql for its configured scope")
            if self.scope in {"target", "both", "compare"} and not self.target_sql:
                raise ValueError("custom_sql requires target_sql for its configured scope")
        return self

    @property
    def test_id(self) -> str:
        return self.id

    def columns_for(self, side: str) -> list[str]:
        explicit = self.source_columns if side == "source" else self.target_columns
        scalar = self.source_column if side == "source" else self.target_column
        if explicit:
            return explicit
        if scalar:
            return [scalar]
        if self.columns:
            return self.columns
        return [self.column] if self.column else []

    def rule_type(self) -> str:
        return {
            "null_check": "null_count", "accepted_values": "domain",
            "row_count": "row_count", "duplicate_check": "uniqueness",
            "freshness_check": "freshness", "aggregate_check": "aggregate",
            "custom_sql": "custom_sql",
        }[self.type]

    def rule_parameters(self) -> dict[str, Any]:
        if self.type == "accepted_values":
            return {"values": self.values}
        if self.type == "null_check":
            return {"allowed": self.allowed_nulls}
        if self.type == "duplicate_check":
            return {"allowed": self.allowed_duplicates}
        if self.type == "freshness_check":
            return {"minutes": self.max_delay_minutes}
        if self.type == "aggregate_check":
            return {
                "aggregate": self.aggregation, "comparison": self.comparison,
                "expected_value": self.expected_value, "group_by": self.group_by,
            }
        return {"expected_value": self.expected_value} if self.expected_value is not None else {}

    def rule_tolerance(self) -> dict[str, Any]:
        return {"absolute": self.tolerance_absolute, "percentage": self.tolerance_percentage}


class AppConfig(BaseModel):
    root: Path
    project: ProjectConfig
    llm: LLMConfig

    def path(self, relative: str) -> Path:
        return (self.root / relative).resolve()


def load_app_config(
    root: str | Path = ".",
    project_file: str = "config/project.yaml",
    llm_file: str = "config/llm.yaml",
) -> AppConfig:
    root_path = Path(root).resolve()
    return AppConfig(
        root=root_path,
        project=ProjectConfig.model_validate(read_yaml(root_path / project_file)),
        llm=LLMConfig.model_validate(read_yaml(root_path / llm_file)),
    )


def load_table_pairs(config: AppConfig) -> list[TablePair]:
    frame = _read_tabular(config.path(config.project.table_mappings))
    required = {"pair_id", "target_project", "target_dataset", "target_table"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"table_mappings is missing columns: {sorted(missing)}")
    records: list[TablePair] = []
    for raw in frame.to_dict("records"):
        clean = {key: _clean(value) for key, value in raw.items()}
        clean["enabled"] = _as_bool(clean.get("enabled", True))
        if clean["enabled"]:
            records.append(TablePair.model_validate(clean))
    pair_ids = [pair.pair_id for pair in records]
    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError("Enabled table mappings contain duplicate pair_id values")
    return records


def load_column_mappings(config: AppConfig) -> list[ColumnMapping]:
    path = config.path(config.project.column_mappings)
    if not path.exists():
        return []
    frame = _read_tabular(path)
    records: list[ColumnMapping] = []
    for row in frame.to_dict("records"):
        clean = {key: _clean(value) for key, value in row.items()}
        if not clean.get("pair_id") or not clean.get("source_column"):
            continue
        clean["status"] = clean.get("status") or "approved"
        records.append(ColumnMapping.model_validate(clean))
    return records


def load_business_context(config: AppConfig) -> dict[str, Any]:
    return read_yaml(config.path(config.project.context_tables), default={"tables": {}})


def load_runtime_overrides(config: AppConfig) -> dict[str, Any]:
    return read_yaml(config.path(config.project.runtime_overrides), default={"tables": {}})


def load_human_tests(config: AppConfig) -> list[HumanTest]:
    raw = read_yaml(config.path(config.project.human_tests), default={"tests": []})
    return [HumanTest.model_validate(item) for item in raw.get("tests", []) if item.get("enabled", True)]


def create_input_templates(root: str | Path = ".", overwrite: bool = False) -> list[Path]:
    root_path = Path(root).resolve()
    inputs = root_path / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    tables_path = inputs / "table_mappings.xlsx"
    columns_path = inputs / "column_mappings.xlsx"
    if overwrite or not tables_path.exists():
        pd.DataFrame([{
            "pair_id": "sample_fact_sales", "enabled": False, "mode": "migration",
            "source_catalog": "main", "source_schema": "sales", "source_table": "fact_sales",
            "target_project": "your-gcp-project", "target_dataset": "analytics", "target_table": "fact_sales",
        }]).to_excel(tables_path, index=False)
        created.append(tables_path)
    if overwrite or not columns_path.exists():
        pd.DataFrame(columns=["pair_id", "source_column", "target_column", "status"]).to_excel(columns_path, index=False)
        created.append(columns_path)
    return created


def config_summary(config: AppConfig) -> str:
    payload = {
        "root": str(config.root),
        "project": config.project.model_dump(),
        "llm": {**config.llm.model_dump(), "project": bool(config.llm.resolved_project())},
    }
    return json.dumps(payload, indent=2, default=str)
