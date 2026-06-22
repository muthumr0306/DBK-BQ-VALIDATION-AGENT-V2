from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator


IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$-]*$")
HUMAN_TEST_TYPES = {
    "aggregate", "custom_sql", "distinct_count", "domain", "freshness",
    "null_count", "predicate", "relationship", "row_count", "uniqueness",
    "value_distribution",
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


class ContextStoreConfig(BaseModel):
    enabled: bool = False
    project: str | None = None
    dataset: str = "dq_agent_context"
    location: str = "US"
    organization: str = "default"
    environment: str = "development"
    domain: str = "default"

    @model_validator(mode="after")
    def validate_enabled_store(self) -> "ContextStoreConfig":
        if self.enabled and not self.project:
            raise ValueError("context_store.project is required when context storage is enabled")
        if self.project and not re.fullmatch(r"[A-Za-z0-9-]+", self.project):
            raise ValueError(f"context_store.project contains unsafe characters: {self.project!r}")
        if not re.fullmatch(r"[A-Za-z0-9_]+", self.dataset):
            raise ValueError(f"context_store.dataset contains unsafe characters: {self.dataset!r}")
        for name in ("location", "organization", "environment", "domain"):
            value = getattr(self, name)
            if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                raise ValueError(f"context_store.{name} contains unsafe characters: {value!r}")
        return self

    @property
    def namespace(self) -> dict[str, str]:
        return {
            "organization": self.organization,
            "environment": self.environment,
            "domain": self.domain,
        }


class ApprovalConfig(BaseModel):
    root: str = "approvals"

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("approvals.root must be a relative path inside the project")
        return value


class ProjectConfig(BaseModel):
    project_name: str = "dbx_bq_validation_poc"
    default_mode: Literal["migration", "bigquery_only"] = "migration"
    table_mappings: str = "inputs/table_mappings.xlsx"
    column_mappings: str = "inputs/column_mappings.xlsx"
    business_context: str = "inputs/business_context.yaml"
    human_tests: str = "inputs/human_tests.yaml"
    relationships: str = "inputs/relationships.yaml"
    measures: str = "inputs/measures.yaml"
    outputs_dir: str = "outputs"
    confidence: ConfidenceConfig = Field(default_factory=ConfidenceConfig)
    query_limits: QueryLimits = Field(default_factory=QueryLimits)
    context_store: ContextStoreConfig = Field(default_factory=ContextStoreConfig)
    approvals: ApprovalConfig = Field(default_factory=ApprovalConfig)
    max_rca_rounds: int = 2
    fail_fast: bool = False
    log_level: str = "INFO"


class LLMConfig(BaseModel):
    enabled: bool = True
    provider: Literal["vertex_gemini", "openai", "anthropic", "disabled"] = "vertex_gemini"
    model: str = "gemini-2.5-pro"
    temperature: float = 0.1
    max_output_tokens: int = 8192
    project: str | None = None
    project_env: str = "GCP_PROJECT_ID"
    location: str = "us-central1"
    api_key_env: str | None = None
    timeout_seconds: int = 90
    max_retries: int = 2

    def resolved_project(self) -> str | None:
        return self.project or os.getenv(self.project_env)


class TablePair(BaseModel):
    pair_id: str
    enabled: bool = True
    mode: Literal["migration", "bigquery_only"] = "migration"
    source_catalog: str | None = None
    source_schema: str | None = None
    source_table: str | None = None
    target_project: str
    target_dataset: str
    target_table: str
    context_id: str | None = None
    criticality: str = "medium"
    publish_to_context: bool = False

    @field_validator(
        "pair_id", "source_catalog", "source_schema", "source_table",
        "target_project", "target_dataset", "target_table"
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
    pair_id: str
    source_column: str
    target_column: str
    status: str = "approved"
    cast_note: str | None = None
    comments: str | None = None
    publish_to_context: bool = False

    @field_validator("source_column", "target_column")
    @classmethod
    def validate_column(cls, value: str) -> str:
        if not IDENTIFIER_RE.fullmatch(value):
            raise ValueError(f"Unsafe column identifier: {value!r}")
        return value


class HumanTest(BaseModel):
    test_id: str
    pair_id: str
    enabled: bool = True
    type: str
    scope: Literal["source", "target", "both", "compare"] = "both"
    source_columns: list[str] = Field(default_factory=list)
    target_columns: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    tolerance: dict[str, Any] = Field(default_factory=dict)
    severity: str = "error"
    source_sql: str | None = None
    target_sql: str | None = None
    description: str | None = None
    publish_to_context: bool = False

    @model_validator(mode="after")
    def validate_test_definition(self) -> "HumanTest":
        self.type = self.type.strip().lower()
        if self.type not in HUMAN_TEST_TYPES:
            raise ValueError(
                f"Unsupported human test type {self.type!r}; "
                f"expected one of {sorted(HUMAN_TEST_TYPES)}"
            )
        if self.type == "predicate" and not isinstance(self.parameters.get("predicate"), dict):
            raise ValueError("predicate tests require parameters.predicate")
        if self.type == "domain" and (
            not isinstance(self.parameters.get("values"), list) or not self.parameters["values"]
        ):
            raise ValueError("domain tests require a non-empty parameters.values list")
        if self.type == "relationship":
            required = {"referenced_table", "referenced_columns"}
            missing = required - set(self.parameters)
            if missing:
                raise ValueError(f"relationship tests are missing parameters: {sorted(missing)}")
        if self.type == "custom_sql":
            needs_source = self.scope in {"source", "both", "compare"}
            needs_target = self.scope in {"target", "both", "compare"}
            if needs_source and not self.source_sql:
                raise ValueError("custom_sql test requires source_sql for its configured scope")
            if needs_target and not self.target_sql:
                raise ValueError("custom_sql test requires target_sql for its configured scope")
        return self


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
        clean["publish_to_context"] = _as_bool(clean.get("publish_to_context", False))
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
    return [
        ColumnMapping.model_validate({
            **{key: _clean(value) for key, value in row.items()},
            "publish_to_context": _as_bool(row.get("publish_to_context", False)),
        })
        for row in frame.to_dict("records")
        if _clean(row.get("pair_id")) and _clean(row.get("source_column"))
    ]


def load_business_context(config: AppConfig) -> dict[str, Any]:
    return read_yaml(config.path(config.project.business_context), default={"tables": {}})


def load_human_tests(config: AppConfig) -> list[HumanTest]:
    raw = read_yaml(config.path(config.project.human_tests), default={"tests": []})
    return [HumanTest.model_validate(item) for item in raw.get("tests", []) if item.get("enabled", True)]


def create_input_templates(root: str | Path = ".", overwrite: bool = False) -> list[Path]:
    """Create editable Excel templates without replacing existing user inputs."""
    root_path = Path(root).resolve()
    inputs = root_path / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    tables_path = inputs / "table_mappings.xlsx"
    columns_path = inputs / "column_mappings.xlsx"
    if overwrite or not tables_path.exists():
        pd.DataFrame(
            [
                {
                    "pair_id": "sample_fact_sales",
                    "enabled": False,
                    "mode": "migration",
                    "source_catalog": "main",
                    "source_schema": "sales",
                    "source_table": "fact_sales",
                    "target_project": "your-gcp-project",
                    "target_dataset": "analytics",
                    "target_table": "fact_sales",
                    "context_id": "sample_fact_sales",
                    "criticality": "high",
                    "publish_to_context": False,
                },
                {
                    "pair_id": "sample_dim_brand_scd2",
                    "enabled": False,
                    "mode": "migration",
                    "source_catalog": "main",
                    "source_schema": "dimensions",
                    "source_table": "dim_brand_legacy",
                    "target_project": "your-gcp-project",
                    "target_dataset": "analytics",
                    "target_table": "dim_brand",
                    "context_id": "sample_dim_brand_scd2",
                    "criticality": "high",
                    "publish_to_context": False,
                },
                {
                    "pair_id": "sample_dim_market",
                    "enabled": False,
                    "mode": "migration",
                    "source_catalog": "main",
                    "source_schema": "dimensions",
                    "source_table": "dim_market",
                    "target_project": "your-gcp-project",
                    "target_dataset": "analytics",
                    "target_table": "dim_market",
                    "context_id": "sample_dim_market",
                    "criticality": "medium",
                    "publish_to_context": False,
                },
                {
                    "pair_id": "sample_bq_only_table",
                    "enabled": False,
                    "mode": "bigquery_only",
                    "source_catalog": None,
                    "source_schema": None,
                    "source_table": None,
                    "target_project": "your-gcp-project",
                    "target_dataset": "analytics",
                    "target_table": "new_bigquery_table",
                    "context_id": "sample_bq_only_table",
                    "criticality": "medium",
                    "publish_to_context": False,
                },
            ]
        ).to_excel(tables_path, index=False)
        created.append(tables_path)
    if overwrite or not columns_path.exists():
        pd.DataFrame(
            columns=[
                "pair_id", "source_column", "target_column", "status", "cast_note",
                "comments", "publish_to_context",
            ]
        ).to_excel(columns_path, index=False)
        created.append(columns_path)
    return created


def config_summary(config: AppConfig) -> str:
    payload = {
        "root": str(config.root),
        "project": config.project.model_dump(),
        "llm": {**config.llm.model_dump(), "project": bool(config.llm.resolved_project())},
    }
    return json.dumps(payload, indent=2, default=str)
