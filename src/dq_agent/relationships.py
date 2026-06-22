from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .config import AppConfig, TablePair
from .context_utils import make_context_record, stable_id
from .query_engine import RuleSpec, SQLCompiler, normalized_type


TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_$-]+\.[A-Za-z0-9_$-]+\.[A-Za-z0-9_$-]+$")


def _description_tokens(value: Any) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", str(value or "").lower()) if len(token) > 2}


class RelationshipSettings(BaseModel):
    auto_inference_enabled: bool = True
    fuzzy_matching_enabled: bool = True
    profile_values: bool = True
    max_candidates_per_table: int = Field(default=20, ge=1, le=100)
    minimum_match_rate: float = Field(default=0.5, ge=0, le=1)


class TableRelationshipOverride(BaseModel):
    auto_inference_enabled: bool


class DimensionKey(BaseModel):
    parent_columns: list[str]
    child_column_sets: list[list[str]]

    @model_validator(mode="after")
    def validate_widths(self) -> "DimensionKey":
        if not self.parent_columns:
            raise ValueError("Dimension keys require at least one parent column")
        for child_columns in self.child_column_sets:
            if len(child_columns) != len(self.parent_columns):
                raise ValueError("Dimension child and parent key widths must match")
        return self


class DimensionDefinition(BaseModel):
    enabled: bool = True
    table: str
    description: str | None = None
    keys: list[DimensionKey]
    parent_filters: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("table")
    @classmethod
    def validate_table(cls, value: str) -> str:
        if not TABLE_NAME_RE.fullmatch(value):
            raise ValueError(f"Expected project.dataset.table, got {value!r}")
        return value


class CustomRelationship(BaseModel):
    id: str
    enabled: bool = True
    child_table: str
    child_columns: list[str]
    parent_table: str
    parent_columns: list[str]
    child_filters: list[dict[str, Any]] = Field(default_factory=list)
    parent_filters: list[dict[str, Any]] = Field(default_factory=list)
    description: str | None = None
    publish_to_context: bool = False

    @model_validator(mode="after")
    def validate_columns(self) -> "CustomRelationship":
        if not self.child_columns or len(self.child_columns) != len(self.parent_columns):
            raise ValueError(f"Custom relationship {self.id} has mismatched key widths")
        if not TABLE_NAME_RE.fullmatch(self.parent_table):
            raise ValueError(f"Custom relationship {self.id} has an unsafe parent table")
        return self


class RelationshipConfiguration(BaseModel):
    settings: RelationshipSettings = Field(default_factory=RelationshipSettings)
    table_overrides: dict[str, TableRelationshipOverride] = Field(default_factory=dict)
    dimensions: dict[str, DimensionDefinition] = Field(default_factory=dict)
    custom_relationships: list[CustomRelationship] = Field(default_factory=list)


def load_relationship_configuration(config: AppConfig) -> RelationshipConfiguration:
    path = config.path(config.project.relationships)
    if not path.exists():
        return RelationshipConfiguration()
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return RelationshipConfiguration.model_validate(raw)


def load_sample_metadata(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def feature_flag_for_pair(
    pair: TablePair,
    relationship_config: RelationshipConfiguration,
    business_context: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    context = business_context or {}
    table_setting = context.get("relationship_inference", {}).get("enabled")
    if table_setting is not None:
        return bool(table_setting), "business_context"
    selectors = [pair.pair_id, pair.context_id, pair.target_name]
    for selector in selectors:
        if selector and selector in relationship_config.table_overrides:
            return relationship_config.table_overrides[selector].auto_inference_enabled, f"table_override:{selector}"
    return relationship_config.settings.auto_inference_enabled, "global"


def metadata_index(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for name, metadata in (raw.get("tables") or {}).items():
        item = dict(metadata or {})
        item["name"] = name
        index[name] = item
        if item.get("table"):
            index[str(item["table"])] = item
    return index


def profile_key(candidate: dict[str, Any], parent: bool = False) -> str:
    if parent:
        return f"{candidate['parent_table']}|{','.join(candidate['parent_columns'])}"
    return (
        f"{candidate['pair_id']}|{','.join(candidate['child_columns'])}|"
        f"{candidate['parent_table']}|{','.join(candidate['parent_columns'])}"
    )


def _candidate(
    pair: TablePair,
    relationship_id: str,
    child_columns: list[str],
    parent_table: str,
    parent_columns: list[str],
    origin: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "candidate_id": stable_id(pair.pair_id, child_columns, parent_table, parent_columns, origin),
        "relationship_id": relationship_id,
        "pair_id": pair.pair_id,
        "context_id": pair.context_id or pair.pair_id,
        "child_table": pair.target_name,
        "child_columns": child_columns,
        "parent_table": parent_table,
        "parent_columns": parent_columns,
        "child_filters": details.pop("child_filters", []),
        "parent_filters": details.pop("parent_filters", []),
        "origin": origin,
        "match_type": details.pop("match_type", origin),
        "description": details.pop("description", None),
        "publish_to_context": bool(details.pop("publish_to_context", False)),
        "metadata_status": "NOT_CHECKED",
        "metadata_errors": [],
        "metadata_warnings": [],
        "ambiguous": False,
        "confidence": 0.0,
        "decision": "PENDING",
        **details,
    }


def discover_relationship_candidates(
    pair: TablePair,
    child_metadata: dict[str, Any],
    relationship_config: RelationshipConfiguration,
    business_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    enabled, flag_source = feature_flag_for_pair(pair, relationship_config, business_context)
    candidates: list[dict[str, Any]] = []

    for custom in relationship_config.custom_relationships:
        if not custom.enabled or custom.child_table not in {pair.pair_id, pair.context_id, pair.target_name}:
            continue
        candidates.append(_candidate(
            pair, custom.id, custom.child_columns, custom.parent_table, custom.parent_columns,
            "custom", child_filters=custom.child_filters, parent_filters=custom.parent_filters,
            description=custom.description, publish_to_context=custom.publish_to_context,
            match_type="explicit",
        ))

    for item in (business_context or {}).get("relationships", []):
        context_origin = "trusted" if item.get("origin") == "trusted" else "custom"
        candidates.append(_candidate(
            pair, item["id"], item.get("child_columns", item.get("target_columns", [])),
            item.get("parent_table", item.get("referenced_table")),
            item.get("parent_columns", item.get("referenced_columns", [])),
            context_origin, child_filters=item.get("child_filters", []),
            parent_filters=item.get("parent_filters", [item["active_filter"]] if item.get("active_filter") else []),
            description=item.get("description"), match_type="business_context",
        ))

    if enabled:
        child_names = {str(item["name"]).lower(): str(item["name"]) for item in child_metadata.get("columns", [])}
        for dimension_name, dimension in relationship_config.dimensions.items():
            if not dimension.enabled or dimension.table == pair.target_name:
                continue
            exact_aliases: set[str] = set()
            for key_index, key in enumerate(dimension.keys):
                for child_set in key.child_column_sets:
                    exact_aliases.update(name.lower() for name in child_set)
                    if all(name.lower() in child_names for name in child_set):
                        actual = [child_names[name.lower()] for name in child_set]
                        candidates.append(_candidate(
                            pair, f"{dimension_name}_{key_index}", actual, dimension.table,
                            key.parent_columns, "registry", parent_filters=dimension.parent_filters,
                            description=dimension.description, match_type="exact", alias_similarity=1.0,
                            dimension=dimension_name,
                        ))
            if relationship_config.settings.fuzzy_matching_enabled:
                for actual_lower, actual in child_names.items():
                    if actual_lower in exact_aliases:
                        continue
                    aliases = [column for key in dimension.keys for child_set in key.child_column_sets for column in child_set if len(child_set) == 1]
                    if not aliases:
                        continue
                    best_alias = max(aliases, key=lambda alias: SequenceMatcher(None, actual_lower, alias.lower()).ratio())
                    similarity = SequenceMatcher(None, actual_lower, best_alias.lower()).ratio()
                    if similarity < 0.75:
                        continue
                    matching_key = next(
                        key for key in dimension.keys
                        if any(len(child_set) == 1 and child_set[0] == best_alias for child_set in key.child_column_sets)
                    )
                    candidates.append(_candidate(
                        pair, f"{dimension_name}_fuzzy_{actual}", [actual], dimension.table,
                        matching_key.parent_columns, "inference", parent_filters=dimension.parent_filters,
                        description=dimension.description, match_type="fuzzy",
                        alias_similarity=round(similarity, 4), dimension=dimension_name,
                    ))

    candidates = apply_relationship_precedence(candidates)
    candidates = candidates[: relationship_config.settings.max_candidates_per_table]
    return candidates, {"enabled": enabled, "source": flag_source}


def trusted_relationship_candidates(pair: TablePair, trusted: pd.DataFrame) -> list[dict[str, Any]]:
    if trusted.empty:
        return []
    output: list[dict[str, Any]] = []
    for row in trusted.to_dict("records"):
        if row.get("context_type") != "relationship" or str(row.get("pair_id") or "") != pair.pair_id:
            continue
        payload = row.get("payload_json") or row.get("payload")
        relationship = payload if isinstance(payload, dict) else json.loads(str(payload))
        output.append(_candidate(
            pair, relationship["id"], relationship["child_columns"], relationship["parent_table"],
            relationship["parent_columns"], "trusted",
            child_filters=relationship.get("child_filters", []),
            parent_filters=relationship.get("parent_filters", []),
            description=relationship.get("description"), match_type="human_approved",
            stored_confidence=float(row.get("confidence") or 1.0),
        ))
    return output


def apply_relationship_precedence(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    priorities = {"custom": 4, "trusted": 3, "registry": 2, "inference": 1}
    grouped: dict[tuple[str, tuple[str, ...]], list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = (candidate["pair_id"], tuple(column.lower() for column in candidate["child_columns"]))
        grouped.setdefault(key, []).append(candidate)
    selected: list[dict[str, Any]] = []
    for group in grouped.values():
        highest = max(priorities.get(item["origin"], 0) for item in group)
        winners = [item for item in group if priorities.get(item["origin"], 0) == highest]
        mappings = {(item["parent_table"], tuple(item["parent_columns"])) for item in winners}
        if len(mappings) > 1:
            for item in winners:
                item["ambiguous"] = True
                item["metadata_warnings"].append("Multiple relationships have equal precedence for the same child columns")
        selected.extend(winners)
    return selected


def _columns(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item["name"]).lower(): item for item in metadata.get("columns", [])}


def _compatible(left: str, right: str) -> bool:
    source = normalized_type(left)
    target = normalized_type(right)
    return source == target or {source, target} <= {"integer", "decimal"} or {source, target} <= {"date", "timestamp"}


def validate_relationship_metadata(
    candidate: dict[str, Any],
    metadata: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    candidate = dict(candidate)
    errors = list(candidate.get("metadata_errors", []))
    warnings = list(candidate.get("metadata_warnings", []))
    child = metadata.get(candidate["child_table"])
    parent = metadata.get(candidate["parent_table"])
    if not child:
        errors.append(f"Child table metadata is unavailable: {candidate['child_table']}")
    if not parent:
        errors.append(f"Parent table metadata is unavailable: {candidate['parent_table']}")
    if candidate["child_table"] == candidate["parent_table"]:
        errors.append("Self-referencing inference is not enabled")
    if len(candidate["child_columns"]) != len(candidate["parent_columns"]):
        errors.append("Child and parent key widths differ")
    type_pairs: list[dict[str, Any]] = []
    description_similarities: list[float] = []
    if child and parent:
        if child.get("location") and parent.get("location") and child["location"] != parent["location"]:
            errors.append(f"BigQuery locations differ: {child['location']} vs {parent['location']}")
        child_columns, parent_columns = _columns(child), _columns(parent)
        for local, remote in zip(candidate["child_columns"], candidate["parent_columns"]):
            local_meta, remote_meta = child_columns.get(local.lower()), parent_columns.get(remote.lower())
            if not local_meta:
                errors.append(f"Child column does not exist: {local}")
                continue
            if not remote_meta:
                errors.append(f"Parent column does not exist: {remote}")
                continue
            compatible = _compatible(str(local_meta["data_type"]), str(remote_meta["data_type"]))
            local_tokens = _description_tokens(local_meta.get("description"))
            remote_tokens = _description_tokens(remote_meta.get("description"))
            description_similarity = (
                len(local_tokens & remote_tokens) / len(local_tokens | remote_tokens)
                if local_tokens or remote_tokens else 0.0
            )
            description_similarities.append(description_similarity)
            type_pairs.append({
                "child_column": local, "child_type": local_meta["data_type"],
                "parent_column": remote, "parent_type": remote_meta["data_type"],
                "compatible": compatible,
                "description_similarity": round(description_similarity, 4),
            })
            if not compatible:
                errors.append(f"Incompatible key types: {local_meta['data_type']} vs {remote_meta['data_type']}")
        for filter_item in candidate.get("child_filters", []):
            if str(filter_item.get("column", "")).lower() not in child_columns:
                errors.append(f"Child filter column does not exist: {filter_item.get('column')}")
        for filter_item in candidate.get("parent_filters", []):
            if str(filter_item.get("column", "")).lower() not in parent_columns:
                errors.append(f"Parent filter column does not exist: {filter_item.get('column')}")
    candidate.update({
        "metadata_errors": errors,
        "metadata_warnings": warnings,
        "metadata_status": "PASS" if not errors else "FAIL",
        "type_pairs": type_pairs,
        "types_compatible": bool(type_pairs) and all(item["compatible"] for item in type_pairs),
        "description_similarity": round(
            sum(description_similarities) / len(description_similarities), 4
        ) if description_similarities else 0.0,
    })
    return candidate


def calculate_relationship_confidence(
    candidate: dict[str, Any],
    parent_profile: dict[str, Any] | None,
    relationship_profile: dict[str, Any] | None,
    auto_accept: float,
    review_threshold: float,
    minimum_match_rate: float,
) -> dict[str, Any]:
    candidate = dict(candidate)
    parent_profile = parent_profile or {}
    relationship_profile = relationship_profile or {}
    parent_rows = int(parent_profile.get("parent_rows") or 0)
    parent_nulls = int(parent_profile.get("parent_null_key_rows") or 0)
    parent_distinct = int(parent_profile.get("parent_distinct_keys") or 0)
    parent_unique = parent_rows > 0 and parent_nulls == 0 and parent_distinct == parent_rows
    checked = int(relationship_profile.get("checked_count") or 0)
    matched = int(relationship_profile.get("matched_count") or 0)
    match_rate = matched / checked if checked else 0.0

    if candidate["origin"] in {"custom", "trusted"}:
        confidence = 1.0 if candidate["metadata_status"] == "PASS" and parent_unique else 0.0
        alias_score = 0.4
    else:
        alias_score = (0.4 if candidate.get("match_type") == "exact" else 0.35) * float(candidate.get("alias_similarity", 0))
        type_score = 0.2 if candidate.get("types_compatible") else 0.0
        uniqueness_score = 0.2 if parent_unique else 0.0
        profile_score = 0.15 * min(match_rate, 1.0)
        description_score = 0.05 * float(candidate.get("description_similarity") or 0)
        confidence = alias_score + type_score + uniqueness_score + profile_score + description_score
    confidence = round(min(confidence, 1.0), 4)

    if candidate["metadata_status"] != "PASS" or not parent_unique:
        decision = "REJECT"
    elif candidate.get("ambiguous"):
        decision = "REVIEW"
    elif candidate["origin"] in {"custom", "trusted"}:
        decision = "AUTO"
    elif candidate.get("match_type") == "fuzzy":
        decision = "REVIEW" if confidence >= review_threshold else "REJECT"
    elif match_rate < minimum_match_rate:
        decision = "REVIEW" if confidence >= review_threshold else "REJECT"
    elif confidence >= auto_accept:
        decision = "AUTO"
    elif confidence >= review_threshold:
        decision = "REVIEW"
    else:
        decision = "REJECT"

    candidate.update({
        "confidence": confidence,
        "decision": decision,
        "parent_unique": parent_unique,
        "match_rate": round(match_rate, 4),
        "parent_profile": parent_profile,
        "relationship_profile": relationship_profile,
        "confidence_evidence": {
            "alias_score": round(alias_score, 4),
            "type_score": 0.2 if candidate.get("types_compatible") else 0.0,
            "parent_uniqueness_score": 0.2 if parent_unique else 0.0,
            "profile_score": round(0.15 * min(match_rate, 1.0), 4),
            "description_score": round(0.05 * float(candidate.get("description_similarity") or 0), 4),
        },
    })
    return candidate


def parent_profile_sql(candidate: dict[str, Any]) -> str:
    compiler = SQLCompiler("bigquery")
    table = compiler.table(candidate["parent_table"])
    columns = [compiler.identifier(column) for column in candidate["parent_columns"]]
    null_condition = " OR ".join(f"{column} IS NULL" for column in columns)
    distinct = columns[0] if len(columns) == 1 else f"TO_JSON_STRING(STRUCT({', '.join(columns)}))"
    where = compiler.where(candidate.get("parent_filters", []))
    return (
        f"SELECT COUNT(*) AS parent_rows, "
        f"SUM(CASE WHEN {null_condition} THEN 1 ELSE 0 END) AS parent_null_key_rows, "
        f"COUNT(DISTINCT {distinct}) AS parent_distinct_keys FROM {table}{where}"
    )


def relationship_rule(candidate: dict[str, Any]) -> RuleSpec:
    return RuleSpec(
        rule_id=f"{candidate['pair_id']}__relationship__{candidate['relationship_id']}",
        pair_id=candidate["pair_id"], type="relationship", scope="target",
        category="referential_integrity", target_columns=candidate["child_columns"],
        parameters={
            "referenced_table": candidate["parent_table"],
            "referenced_columns": candidate["parent_columns"],
            "child_filters": candidate.get("child_filters", []),
            "referenced_filters": candidate.get("parent_filters", []),
            "relationship_origin": candidate["origin"],
            "confidence": candidate.get("confidence"),
            "candidate_id": candidate.get("candidate_id"),
        },
        origin=candidate["origin"], description=candidate.get("description"),
    )


def candidate_to_relationship(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": candidate["relationship_id"],
        "enabled": True,
        "child_columns": candidate["child_columns"],
        "parent_table": candidate["parent_table"],
        "parent_columns": candidate["parent_columns"],
        "child_filters": candidate.get("child_filters", []),
        "parent_filters": candidate.get("parent_filters", []),
        "description": candidate.get("description"),
        "origin": candidate.get("origin"),
        "confidence": candidate.get("confidence"),
        "evidence": {
            "metadata_status": candidate.get("metadata_status"),
            "confidence_evidence": candidate.get("confidence_evidence"),
            "parent_profile": candidate.get("parent_profile"),
            "relationship_profile": candidate.get("relationship_profile"),
        },
    }


def candidates_to_context_records(
    candidates: list[dict[str, Any]],
    config: AppConfig,
    run_id: str,
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for candidate in candidates:
        needs_approval = candidate.get("decision") == "REVIEW" or bool(candidate.get("publish_to_context"))
        if not needs_approval:
            continue
        relationship = candidate_to_relationship(candidate)
        records.append(make_context_record(
            config, run_id, "relationship",
            f"{candidate['pair_id']}:{','.join(candidate['child_columns'])}:{candidate['parent_table']}:{','.join(candidate['parent_columns'])}",
            relationship, config.project.relationships, True,
            pair_id=candidate["pair_id"], context_id=candidate["context_id"],
            target_table=candidate["child_table"], column_name=",".join(candidate["child_columns"]),
            confidence=float(candidate.get("confidence") or 0),
            origin="CONFIGURATION" if candidate.get("origin") == "custom" else "AGENT_INFERENCE",
        ))
    return pd.DataFrame(records)
