from __future__ import annotations

import hashlib
import json
import logging
import re
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

from .config import (
    AppConfig,
    ColumnMapping,
    HumanTest,
    TablePair,
    load_app_config,
    load_business_context,
    load_column_mappings,
    load_human_tests,
    load_table_pairs,
)
from .connectors import ColumnMetadata, WarehouseConnector, make_connectors
from .llm import LLMAdapter, make_llm_adapter
from .measures import (
    DiagnosticRequest,
    compare_reconciliation_results,
    default_diagnostic_requests,
    diagnostic_sql,
    evidence_based_rca,
    infer_measure_candidates,
    load_measure_configuration,
    measure_profile_sql,
    reconciliation_sql,
    resolve_measure_precedence,
    select_reconciliation_groups,
    validate_diagnostic_request,
    validate_measure_metadata,
)
from .query_engine import QueryGuard, RuleSpec, SQLCompiler, allowed_tables_for_rule, normalized_type
from .relationships import (
    calculate_relationship_confidence,
    candidate_to_relationship,
    discover_relationship_candidates,
    load_relationship_configuration,
    parent_profile_sql,
    relationship_rule,
    validate_relationship_metadata,
)
from .reporting import (
    append_jsonl,
    configure_logging,
    write_consolidated_reports,
    write_json,
    write_run_summary,
    write_table_report,
)


STAGES = ["metadata", "inference", "rules", "execution", "rca", "reports"]
AUDIT_PATTERNS = (
    "updatedat", "updatedts", "updatetimestamp", "lastmodified", "loadts",
    "ingestionts", "batchts", "sourceextractts", "createdat", "insertts",
)
KEY_SUFFIXES = ("id", "sid", "key", "code", "number", "num")
CURRENT_FLAG_NAMES = {
    "activeindicator", "isactiveflag", "activeflag", "currentflag", "iscurrent", "currentindicator"
}


class MappingChoice(BaseModel):
    source_column: str
    target_column: str
    confidence: float
    rationale: str


class MappingRerankResponse(BaseModel):
    choices: list[MappingChoice] = Field(default_factory=list)


class RCAResponse(BaseModel):
    conclusion: str
    confidence: float
    evidence_summary: list[str] = Field(default_factory=list)
    inconclusive: bool = False


class DiagnosticPlanResponse(BaseModel):
    requests: list[DiagnosticRequest] = Field(default_factory=list)


class RCAWordingResponse(BaseModel):
    explanation: str
    recommended_action: str


def _name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _tokens(value: str) -> set[str]:
    pieces = re.sub(r"([a-z])([A-Z])", r"\1 \2", value).replace("_", " ").replace("-", " ")
    return {piece.lower() for piece in pieces.split() if piece}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    if value is not None and not isinstance(value, (str, int, float, bool)):
        return str(value)
    return value


class DQWorkflow:
    def __init__(
        self,
        root: str | Path = ".",
        project_file: str = "config/project.yaml",
        llm_file: str = "config/llm.yaml",
        effective_inputs: dict[str, Any] | None = None,
    ):
        self.config: AppConfig = load_app_config(root, project_file, llm_file)
        if effective_inputs is None:
            self.table_pairs = load_table_pairs(self.config)
            self.column_mappings = load_column_mappings(self.config)
            self.business_context = load_business_context(self.config)
            self.human_tests = load_human_tests(self.config)
        else:
            self.table_pairs = [TablePair.model_validate(row) for row in effective_inputs.get("table_mappings", [])]
            self.column_mappings = [ColumnMapping.model_validate(row) for row in effective_inputs.get("column_mappings", [])]
            self.business_context = effective_inputs.get("business_context", {"tables": {}})
            self.human_tests = [
                HumanTest.model_validate(row)
                for row in effective_inputs.get("human_tests", {}).get("tests", [])
                if row.get("enabled", True)
            ]
        self.connectors: dict[str, WarehouseConnector] | None = None
        self._llm: LLMAdapter | None | Literal[False] = False
        self.guard = QueryGuard()
        self.measure_config = load_measure_configuration(self.config)
        self.relationship_config = load_relationship_configuration(self.config)
        self._table_metadata_cache: dict[str, dict[str, Any]] = {}
        self._relationship_profile_cache: dict[str, dict[str, Any]] = {}
        self.output_dir: Path | None = None
        self.logger: logging.Logger | None = None

    @property
    def llm(self) -> LLMAdapter | None:
        if self._llm is False:
            self._llm = make_llm_adapter(self.config.llm)
        return self._llm

    def _connectors(self) -> dict[str, WarehouseConnector]:
        if self.connectors is None:
            project = self.table_pairs[0].target_project if self.table_pairs else None
            self.connectors = make_connectors(self.config.project.query_limits, project)
        return self.connectors

    def _event(self, level: str, event: str, **details: Any) -> None:
        if not self.output_dir or not self.logger:
            return
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level.upper(),
            "event": event,
            **_jsonable(details),
        }
        append_jsonl(self.output_dir / "events.jsonl", payload)
        getattr(self.logger, level.lower(), self.logger.info)(f"{event} | {details}")

    def _checkpoint(self, stage: str, pair_id: str | None = None, status: str = "COMPLETED", **details: Any) -> None:
        if not self.output_dir:
            return
        path = self.output_dir / "checkpoint.json"
        payload: dict[str, Any] = {}
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                payload = {}
        prefix = "last_failed" if status == "ERROR" else (
            "current" if status == "RUNNING" else "last_completed"
        )
        payload.update({
            f"{prefix}_stage": stage,
            f"{prefix}_pair_id": pair_id,
            f"{prefix}_status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **details,
        })
        write_json(path, payload)

    @staticmethod
    def _run_id() -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def run(self, run_id: str | None = None, stop_after: str = "reports") -> dict[str, Any]:
        if stop_after not in STAGES:
            raise ValueError(f"stop_after must be one of {STAGES}")
        run_id = run_id or self._run_id()
        self.output_dir = self.config.path(self.config.project.outputs_dir) / run_id
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = configure_logging(self.output_dir, self.config.project.log_level)
        manifest: dict[str, Any] = {
            "run_id": run_id,
            "project": self.config.project.project_name,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "requested_stop_after": stop_after,
            "status": "RUNNING",
            "tables": [],
        }
        write_json(self.output_dir / "manifest.json", manifest)
        self._checkpoint("RUN", status="RUNNING", run_id=run_id)
        table_outputs: list[dict[str, Any]] = []
        all_reviews: list[dict[str, Any]] = []
        self._event(
            "info", "run_started", run_id=run_id, table_count=len(self.table_pairs),
            llm_provider=self.config.llm.provider, llm_model=self.config.llm.model,
        )
        self._event(
            "info", "inputs_loaded",
            table_mappings=str(self.config.path(self.config.project.table_mappings)),
            column_mappings=str(self.config.path(self.config.project.column_mappings)),
            business_context=str(self.config.path(self.config.project.business_context)),
            human_tests=str(self.config.path(self.config.project.human_tests)),
            relationships=str(self.config.path(self.config.project.relationships)),
            measures=str(self.config.path(self.config.project.measures)),
        )
        try:
            for pair in self.table_pairs:
                result, reviews = self._process_pair(pair, stop_after)
                table_outputs.append(result)
                all_reviews.extend(reviews)
                manifest["tables"].append(result["summary"])
                write_json(self.output_dir / "manifest.json", manifest)
            proposal_path = self.output_dir / "approval_proposals.json"
            write_json(proposal_path, all_reviews)
            manifest["approval_proposals"] = len(all_reviews)
            manifest["approval_proposals_file"] = str(proposal_path)
            self._event("info", "file_written", path=str(proposal_path), rows=len(all_reviews))
            self._event(
                "info", "CREATE_APPROVAL_ITEM", path=str(proposal_path),
                rows=len(all_reviews), item_types=sorted({str(item.get("category")) for item in all_reviews}),
            )
            write_consolidated_reports(self.output_dir, table_outputs)
            write_run_summary(self.output_dir, manifest, table_outputs, all_reviews)
            self._event("info", "reports_written", output_dir=str(self.output_dir))
            table_statuses = [row["summary"].get("status") for row in table_outputs]
            if "ERROR" in table_statuses:
                manifest["status"] = "COMPLETED_WITH_ERRORS"
            elif "FAIL" in table_statuses:
                manifest["status"] = "COMPLETED_WITH_FAILURES"
            elif "WAITING_FOR_REVIEW" in table_statuses:
                manifest["status"] = "COMPLETED_WAITING_FOR_REVIEW"
            elif all_reviews:
                manifest["status"] = "COMPLETED_WITH_REVIEWS"
            else:
                manifest["status"] = "COMPLETED"
            manifest["table_status_counts"] = {
                status: table_statuses.count(status) for status in sorted(set(table_statuses))
            }
        except Exception:
            manifest["status"] = "ERROR"
            manifest["error"] = traceback.format_exc()
            self._event("exception", "run_failed", error=manifest["error"])
            raise
        finally:
            manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
            write_json(self.output_dir / "manifest.json", manifest)
            if self.connectors:
                for connector in self.connectors.values():
                    connector.close()
            self._event("info", "run_finished", status=manifest["status"])
            self._checkpoint("RUN", status=manifest["status"], run_id=run_id)
            if self.logger:
                for handler in list(self.logger.handlers):
                    handler.flush()
                    handler.close()
                    self.logger.removeHandler(handler)
        return manifest

    def _process_pair(
        self, pair: TablePair, stop_after: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        assert self.output_dir is not None
        table_dir = self.output_dir / "tables" / pair.pair_id
        table_dir.mkdir(parents=True, exist_ok=True)
        summary: dict[str, Any] = {
            "pair_id": pair.pair_id,
            "mode": pair.mode,
            "source_table": pair.source_name,
            "target_table": pair.target_name,
            "status": "RUNNING",
            "last_stage": None,
        }
        mappings: list[dict[str, Any]] = []
        rules: list[RuleSpec] = []
        results: list[dict[str, Any]] = []
        rca: list[dict[str, Any]] = []
        reviews: list[dict[str, Any]] = []
        relationship_candidates: list[dict[str, Any]] = []
        resolved_relationships: list[dict[str, Any]] = []
        measures: list[dict[str, Any]] = []
        measure_groupings: dict[str, list[dict[str, Any]]] = {}
        self._event("info", "table_started", pair_id=pair.pair_id, mode=pair.mode)
        try:
            metadata = self._extract_metadata(pair, table_dir)
            summary["last_stage"] = "metadata"
            self._checkpoint("METADATA", pair.pair_id)
            if stop_after == "metadata":
                summary["status"] = "METADATA_COMPLETE"
                return self._finalize_table(table_dir, summary, mappings, rules, results, rca, relationship_candidates), reviews

            context = self._context(pair)
            metadata = self._enrich_metadata_with_context(metadata, context)
            write_json(table_dir / "metadata.json", metadata)
            self._event("info", "inference_started", pair_id=pair.pair_id)
            mappings, mapping_reviews = self._resolve_mappings(pair, metadata)
            reviews.extend(mapping_reviews)
            keys, key_reviews = self._resolve_keys(pair, metadata, mappings, context, table_dir)
            reviews.extend(key_reviews)
            audit, audit_reviews = self._resolve_audit_columns(pair, metadata, mappings, context, table_dir)
            reviews.extend(audit_reviews)
            filters, scd_reviews = self._resolve_scd_and_filters(pair, metadata, mappings, context, table_dir)
            reviews.extend(scd_reviews)
            relationship_candidates, resolved_relationships, relationship_reviews = self._resolve_relationships(
                pair, metadata, context, filters, table_dir
            )
            reviews.extend(relationship_reviews)
            measures, measure_reviews = self._resolve_measures(pair, metadata, mappings, context, table_dir)
            reviews.extend(measure_reviews)
            summary.update({
                "primary_keys": keys, "audit_columns": audit, "filters": filters,
                "measure_count": len(measures),
                "relationship_candidates": len(relationship_candidates),
                "relationships_executable": len(resolved_relationships),
                "review_items": len(reviews),
                "non_blocking_reviews": sum(not item.get("blocking", True) for item in reviews),
            })
            summary["last_stage"] = "inference"
            write_json(table_dir / "inference.json", {
                "mappings": mappings, "primary_keys": keys, "audit_columns": audit,
                "filters": filters, "relationships": relationship_candidates,
                "measures": measures, "reviews": reviews,
            })
            self._event(
                "info", "inference_completed", pair_id=pair.pair_id,
                mappings=len(mappings), approval_proposals=len(reviews),
            )
            self._checkpoint("INFERENCE", pair.pair_id, reviews=len(reviews))
            blocking_reviews = [review for review in reviews if review.get("blocking", True)]
            if blocking_reviews:
                summary["status"] = "WAITING_FOR_REVIEW"
                summary["pending_decisions"] = len(blocking_reviews)
                return self._finalize_table(table_dir, summary, mappings, rules, results, rca, relationship_candidates), reviews
            if stop_after == "inference":
                summary["status"] = "INFERENCE_COMPLETE"
                return self._finalize_table(table_dir, summary, mappings, rules, results, rca, relationship_candidates), reviews

            rules = self._generate_rules(
                pair, metadata, mappings, context, keys, audit, resolved_relationships
            )
            write_json(table_dir / "generated_rules.json", [rule.model_dump() for rule in rules])
            summary["rule_count"] = len(rules)
            summary["last_stage"] = "rules"
            self._event("info", "rules_generated", pair_id=pair.pair_id, rule_count=len(rules))
            self._checkpoint("RULES", pair.pair_id, rule_count=len(rules))
            if stop_after == "rules":
                summary["status"] = "RULES_COMPLETE"
                return self._finalize_table(table_dir, summary, mappings, rules, results, rca, relationship_candidates), reviews

            for rule in rules:
                results.append(self._execute_rule(pair, rule, metadata, filters, table_dir))
            measure_results, measure_groupings = self._execute_measure_reconciliations(
                pair, measures, metadata, mappings, filters, table_dir
            )
            results.extend(measure_results)
            summary["last_stage"] = "execution"
            summary["passed"] = sum(row["status"] == "PASS" for row in results)
            summary["failed"] = sum(row["status"] == "FAIL" for row in results)
            summary["errors"] = sum(row["status"] == "ERROR" for row in results)
            self._event(
                "info", "execution_completed", pair_id=pair.pair_id,
                passed=summary["passed"], failed=summary["failed"], errors=summary["errors"],
            )
            self._checkpoint(
                "EXECUTION", pair.pair_id, passed=summary["passed"],
                failed=summary["failed"], errors=summary["errors"],
            )
            if stop_after == "execution":
                summary["status"] = "EXECUTION_COMPLETE"
                return self._finalize_table(table_dir, summary, mappings, rules, results, rca, relationship_candidates), reviews

            failed = [row for row in results if row["status"] == "FAIL"]
            standard_failures = [row for row in failed if row.get("type") != "measure_reconciliation"]
            measure_failures = [row for row in failed if row.get("type") == "measure_reconciliation"]
            rca = self._perform_rca(pair, standard_failures, rules, audit, filters, table_dir)
            if rca:
                cascade_count = sum(1 for r in rca if r.get("is_cascade"))
                summary["root_cause_count"] = len(rca) - cascade_count
                summary["cascade_count"] = cascade_count
            measure_rca, rca_reviews = self._investigate_measure_failures(
                pair, measure_failures, measures, measure_groupings, filters, context, table_dir
            )
            rca.extend(measure_rca)
            reviews.extend(rca_reviews)
            summary["review_items"] = len(reviews)
            summary["non_blocking_reviews"] = sum(
                not item.get("blocking", True) for item in reviews
            )
            self._event("info", "rca_completed", pair_id=pair.pair_id, failed_rules=len(failed), rca_records=len(rca))
            summary["last_stage"] = "rca"
            summary["status"] = "ERROR" if summary["errors"] else ("FAIL" if failed else "PASS")
            self._checkpoint("RCA", pair.pair_id, rca_records=len(rca))
            return self._finalize_table(table_dir, summary, mappings, rules, results, rca, relationship_candidates), reviews
        except Exception as exc:
            summary.update({"status": "ERROR", "error": str(exc), "stack_trace": traceback.format_exc()})
            self._event("exception", "table_failed", pair_id=pair.pair_id, error=str(exc))
            self._checkpoint("TABLE", pair.pair_id, status="ERROR", error=str(exc))
            if self.config.project.fail_fast:
                raise
            return self._finalize_table(table_dir, summary, mappings, rules, results, rca, relationship_candidates), reviews

    def _finalize_table(
        self,
        table_dir: Path,
        summary: dict[str, Any],
        mappings: list[dict[str, Any]],
        rules: list[RuleSpec],
        results: list[dict[str, Any]],
        rca: list[dict[str, Any]],
        relationship_candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        rule_rows = [rule.model_dump() for rule in rules]
        write_table_report(
            table_dir, summary, mappings, rule_rows, results, rca, relationship_candidates
        )
        self._event("info", "table_finished", pair_id=summary["pair_id"], status=summary["status"])
        return {
            "summary": summary, "mappings": mappings, "rules": rule_rows,
            "results": results, "rca": rca,
            "relationship_candidates": relationship_candidates,
        }

    def _extract_metadata(self, pair: TablePair, table_dir: Path) -> dict[str, Any]:
        connectors = self._connectors()
        target = connectors["target"].get_columns(pair, "target")
        source = connectors["source"].get_columns(pair, "source") if pair.mode == "migration" else []
        target_table = self._get_table_metadata("target", pair.target_name)
        source_table = (
            self._get_table_metadata("source", pair.source_name or "")
            if pair.mode == "migration" else {}
        )
        metadata = {
            "source": [column.as_dict() for column in source],
            "target": [column.as_dict() for column in target],
            "source_table": source_table,
            "target_table": target_table,
        }
        if not target:
            raise RuntimeError(f"No target columns found for {pair.target_name}")
        if pair.mode == "migration" and not source:
            raise RuntimeError(f"No source columns found for {pair.source_name}")
        write_json(table_dir / "metadata.json", metadata)
        self._event(
            "info", "metadata_extracted", pair_id=pair.pair_id,
            source_columns=len(source), target_columns=len(target),
        )
        return metadata

    def _get_table_metadata(self, side: str, qualified_name: str) -> dict[str, Any]:
        if not qualified_name:
            return {}
        cache_key = f"{side}:{qualified_name}"
        if cache_key in self._table_metadata_cache:
            return self._table_metadata_cache[cache_key]
        try:
            result = self._connectors()[side].get_table_metadata(qualified_name)
            self._event(
                "info", "table_metadata_loaded", side=side, table=qualified_name,
                modified_at=result.get("modified_at"),
            )
        except Exception as exc:
            result = {"table": qualified_name, "metadata_error": str(exc)}
            self._event(
                "warning", "table_metadata_unavailable", side=side,
                table=qualified_name, error=str(exc),
            )
        self._table_metadata_cache[cache_key] = result
        return result

    @staticmethod
    def _enrich_metadata_with_context(
        metadata: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        enriched = json.loads(json.dumps(metadata, default=str))
        table_description = context.get("description")
        if table_description and not enriched.get("target_table", {}).get("description"):
            enriched.setdefault("target_table", {})["description"] = table_description
        column_context = context.get("columns", {}) or {}
        if not isinstance(column_context, dict) or any(
            not isinstance(details, dict) for details in column_context.values()
        ):
            raise ValueError("business_context.columns must map column names to objects")
        for column in enriched.get("target", []):
            details = column_context.get(column["name"], column_context.get(column["name"].lower(), {}))
            if details and not column.get("description"):
                column["description"] = details.get("description")
        return enriched

    def _context(self, pair: TablePair) -> dict[str, Any]:
        tables = self.business_context.get("tables", {})
        return tables.get(pair.context_id or pair.pair_id, {}) or {}

    def _inference_id(self, pair_id: str, category: str, subject: str) -> str:
        raw = f"{pair_id}|{category}|{subject}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:20]

    def _review(
        self,
        pair: TablePair,
        category: str,
        subject: str,
        proposal: Any,
        confidence: float,
        evidence: Any,
        blocking: bool = True,
    ) -> dict[str, Any]:
        inference_id = self._inference_id(pair.pair_id, category, subject)
        return {
            "inference_id": inference_id,
            "pair_id": pair.pair_id,
            "category": category,
            "subject": subject,
            "proposal": json.dumps(proposal, default=str),
            "confidence": round(confidence, 4),
            "evidence": json.dumps(evidence, default=str),
            "status": "PENDING",
            "blocking": blocking,
        }

    def _resolve_mappings(
        self, pair: TablePair, metadata: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        targets = [ColumnMetadata(**item) for item in metadata["target"]]
        if pair.mode == "bigquery_only":
            return [
                {
                    "pair_id": pair.pair_id, "source_column": column.name,
                    "target_column": column.name, "confidence": 1.0, "status": "SELF",
                    "rationale": "BigQuery-only mode",
                }
                for column in targets
            ], []
        sources = [ColumnMetadata(**item) for item in metadata["source"]]
        selected_manual = [
            mapping for mapping in self.column_mappings
            if mapping.pair_id == pair.pair_id
            and mapping.status.lower() in {"approved", "confirmed", "manual"}
        ]
        source_names = {column.name.lower() for column in sources}
        target_names = {column.name.lower() for column in targets}
        missing_sources = [item.source_column for item in selected_manual if item.source_column.lower() not in source_names]
        missing_targets = [item.target_column for item in selected_manual if item.target_column.lower() not in target_names]
        manual_targets = [item.target_column.lower() for item in selected_manual]
        if missing_sources or missing_targets or len(manual_targets) != len(set(manual_targets)):
            raise ValueError(
                "Invalid manual column mappings; "
                f"missing_source={missing_sources}, missing_target={missing_targets}, "
                f"duplicate_target={len(manual_targets) != len(set(manual_targets))}"
            )
        manual = {mapping.source_column.lower(): mapping for mapping in selected_manual}
        target_by_lower = {column.name.lower(): column for column in targets}
        used: set[str] = set()
        resolved: list[dict[str, Any]] = []
        reviews: list[dict[str, Any]] = []
        for source in sources:
            if source.name.lower() in manual:
                mapping = manual[source.name.lower()]
                if mapping.target_column.lower() not in target_by_lower:
                    raise ValueError(f"Manual mapping target does not exist: {mapping.target_column}")
                if mapping.target_column.lower() in used:
                    raise ValueError(f"Manual mappings reuse target column: {mapping.target_column}")
                used.add(mapping.target_column.lower())
                resolved.append({
                    "pair_id": pair.pair_id, "source_column": source.name,
                    "target_column": mapping.target_column, "confidence": 1.0,
                    "status": "MANUAL", "rationale": mapping.comments or "Approved manual mapping",
                })
                continue
            candidates = [target for target in targets if target.name.lower() not in used]
            ranked = sorted(
                ((self._mapping_score(source, target), target) for target in candidates),
                key=lambda item: item[0], reverse=True,
            )
            if not ranked:
                continue
            confidence, target = ranked[0]
            if confidence < self.config.project.confidence.auto_accept and self.config.llm.enabled:
                try:
                    adapter = self.llm
                    if adapter:
                        response = adapter.complete_structured(
                            "Choose the best one-to-one target column for the source column.",
                            {
                                "source": source.as_dict(),
                                "candidates": [item[1].as_dict() | {"deterministic_score": item[0]} for item in ranked[:5]],
                            },
                            MappingRerankResponse,
                        )
                        valid = {candidate.name.lower(): candidate for candidate in candidates}
                        choice = next(
                            (item for item in response.choices if item.source_column.lower() == source.name.lower()
                             and item.target_column.lower() in valid),
                            None,
                        )
                        if choice:
                            target = valid[choice.target_column.lower()]
                            source_group = normalized_type(source.data_type)
                            target_group = normalized_type(target.data_type)
                            compatible = source_group == target_group or {
                                source_group, target_group
                            } <= {"integer", "decimal"} or {
                                source_group, target_group
                            } <= {"date", "timestamp"}
                            llm_confidence = min(choice.confidence, 0.95 if compatible else 0.64)
                            confidence = max(confidence, llm_confidence)
                except Exception as exc:
                    self._event("warning", "llm_mapping_unavailable", pair_id=pair.pair_id, column=source.name, error=str(exc))
            status = "AUTO" if confidence >= self.config.project.confidence.auto_accept else "PENDING"
            row = {
                "pair_id": pair.pair_id, "source_column": source.name,
                "target_column": target.name, "confidence": round(confidence, 4),
                "status": status,
                "rationale": f"name/type/description score; second_best={ranked[1][0]:.3f}" if len(ranked) > 1 else "only candidate",
            }
            if status == "AUTO":
                used.add(target.name.lower())
                resolved.append(row)
                continue
            review = self._review(
                pair, "column_mapping", source.name,
                {"source_column": source.name, "target_column": target.name}, confidence,
                [{"target": item[1].name, "score": round(item[0], 4)} for item in ranked[:3]],
            )
            reviews.append(review)
        return resolved, reviews

    @staticmethod
    def _mapping_score(source: ColumnMetadata, target: ColumnMetadata) -> float:
        source_name, target_name = _name(source.name), _name(target.name)
        name_score = 0.5 if source_name == target_name else 0.42 * SequenceMatcher(None, source_name, target_name).ratio()
        source_type, target_type = normalized_type(source.data_type), normalized_type(target.data_type)
        if source_type == target_type:
            type_score = 0.3
        elif {source_type, target_type} <= {"integer", "decimal"} or {source_type, target_type} <= {"date", "timestamp"}:
            type_score = 0.18
        else:
            type_score = 0.0
        description_score = 0.0
        if source.description and target.description:
            left, right = _tokens(source.description), _tokens(target.description)
            description_score = 0.15 * len(left & right) / max(1, len(left | right))
        token_score = 0.05 if _tokens(source.name) & _tokens(target.name) else 0.0
        return min(1.0, name_score + type_score + description_score + token_score)

    def _resolve_keys(
        self,
        pair: TablePair,
        metadata: dict[str, Any],
        mappings: list[dict[str, Any]],
        context: dict[str, Any],
        table_dir: Path,
    ) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
        configured = context.get("primary_key", {})
        if configured.get("target"):
            target = list(configured["target"])
            source = list(configured.get("source", target if pair.mode == "migration" else []))
            target_names = {item["name"].lower() for item in metadata["target"]}
            source_names = {item["name"].lower() for item in metadata["source"]}
            missing_target = [name for name in target if name.lower() not in target_names]
            missing_source = [name for name in source if name.lower() not in source_names]
            if missing_target or (pair.mode == "migration" and missing_source):
                raise ValueError(
                    f"Configured primary key columns are missing; "
                    f"source={missing_source}, target={missing_target}"
                )
            if pair.mode == "migration" and len(source) != len(target):
                raise ValueError("Configured source and target primary keys must have equal width")
            return {"source": source, "target": target}, []
        target_columns = [ColumnMetadata(**item) for item in metadata["target"]]
        key_roles = {
            str(name).lower() for name, details in (context.get("columns", {}) or {}).items()
            if str((details or {}).get("role", "")).lower() in {"primary_key", "key", "identifier"}
        }
        candidates = [
            column for column in target_columns
            if (column.name.lower() in key_roles or any(
                _name(column.name).endswith(suffix) for suffix in KEY_SUFFIXES
            ))
            and normalized_type(column.data_type) in {"integer", "string"}
        ][:5]
        if not candidates:
            review = self._review(pair, "primary_key", "table", {}, 0.0, "No plausible key columns")
            return {"source": [], "target": []}, [review]
        profiles: list[tuple[float, list[ColumnMetadata], dict[str, Any]]] = []
        for column in candidates:
            profile = self._profile_key(pair, [column.name], table_dir)
            rows = int(profile.get("row_count") or 0)
            distinct = int(profile.get("distinct_count") or 0)
            nulls = int(profile.get("null_count") or 0)
            uniqueness = distinct / rows if rows else 0.0
            confidence = 0.55 + (0.25 if uniqueness == 1 else 0.2 * uniqueness) + (0.1 if nulls == 0 else 0)
            profiles.append((min(confidence, 0.95), [column], profile))
        if not any(item[2].get("row_count") == item[2].get("distinct_count") and not item[2].get("null_count") for item in profiles):
            for size in (2, 3):
                for group in combinations(candidates, size):
                    profile = self._profile_key(pair, [column.name for column in group], table_dir)
                    rows = int(profile.get("row_count") or 0)
                    distinct = int(profile.get("distinct_count") or 0)
                    nulls = int(profile.get("null_count") or 0)
                    uniqueness = distinct / rows if rows else 0.0
                    confidence = 0.52 + (0.27 if uniqueness == 1 else 0.2 * uniqueness) + (0.09 if nulls == 0 else 0)
                    profiles.append((min(confidence, 0.9), list(group), profile))
                    if uniqueness == 1 and nulls == 0:
                        break
                if any(len(item[1]) == size and item[2].get("row_count") == item[2].get("distinct_count") for item in profiles):
                    break
        confidence, selected, evidence = max(profiles, key=lambda item: item[0])
        mapping_by_target = {row["target_column"].lower(): row["source_column"] for row in mappings}
        source_keys = [mapping_by_target.get(column.name.lower()) for column in selected] if pair.mode == "migration" else []
        proposal = {
            "source": [column for column in source_keys if column],
            "target": [column.name for column in selected],
        }
        if confidence >= self.config.project.confidence.auto_accept and (
            pair.mode != "migration" or len(proposal["source"]) == len(proposal["target"])
        ):
            return proposal, []
        review = self._review(pair, "primary_key", "table", proposal, confidence, evidence)
        return proposal, [review]

    def _profile_key(self, pair: TablePair, columns: list[str], table_dir: Path) -> dict[str, Any]:
        compiler = SQLCompiler("bigquery")
        table = compiler.table(pair.target_name)
        rendered = [compiler.identifier(column) for column in columns]
        if len(rendered) == 1:
            distinct_expression = rendered[0]
        else:
            fields = ", ".join(rendered)
            distinct_expression = f"TO_JSON_STRING(STRUCT({fields}))"
        null_expression = " OR ".join(f"{column} IS NULL" for column in rendered)
        sql = (
            f"SELECT COUNT(*) AS row_count, COUNT(DISTINCT {distinct_expression}) AS distinct_count, "
            f"SUM(CASE WHEN {null_expression} THEN 1 ELSE 0 END) AS null_count FROM {table}"
        )
        label = "profile_key_" + "_".join(columns)
        result = self._controlled_execute("target", sql, {pair.target_name}, table_dir, label)
        return self._first_row(result)

    def _resolve_audit_columns(
        self,
        pair: TablePair,
        metadata: dict[str, Any],
        mappings: list[dict[str, Any]],
        context: dict[str, Any],
        table_dir: Path,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        # ── Step 1: manual configuration ────────────────────────────────
        configured = context.get("audit_columns", {})
        if configured.get("target"):
            target_index = {item["name"].lower(): item for item in metadata["target"]}
            source_index = {item["name"].lower(): item for item in metadata["source"]}
            target_name = str(configured["target"])
            source_name = str(configured.get("source") or "")
            if target_name.lower() not in target_index:
                raise ValueError(f"Configured target audit column does not exist: {target_name}")
            if normalized_type(target_index[target_name.lower()]["data_type"]) not in {"date", "timestamp"}:
                raise ValueError(f"Configured target audit column is not date/timestamp: {target_name}")
            if pair.mode == "migration":
                if not source_name or source_name.lower() not in source_index:
                    raise ValueError(f"Configured source audit column does not exist: {source_name or None}")
                if normalized_type(source_index[source_name.lower()]["data_type"]) not in {"date", "timestamp"}:
                    raise ValueError(f"Configured source audit column is not date/timestamp: {source_name}")
            self._event(
                "info", "audit_column_manual",
                pair_id=pair.pair_id,
                source=configured.get("source"),
                target=configured["target"],
            )
            return {
                "source": configured.get("source"),
                "target": configured["target"],
                "method": "configured_column",
            }, []

        # ── Step 2: filter date / timestamp columns ─────────────────────
        target_columns = [ColumnMetadata(**item) for item in metadata["target"]]
        date_columns = [
            column for column in target_columns
            if normalized_type(column.data_type) in {"date", "timestamp"}
        ]
        if not date_columns:
            self._event(
                "info", "audit_column_no_date_columns",
                pair_id=pair.pair_id,
            )
            metadata_freshness = self._metadata_freshness_available(pair, metadata)
            if metadata_freshness:
                return {"source": None, "target": None, "method": "table_metadata"}, []
            review = self._review(pair, "audit_column", "table", {}, 0.0, "No date/timestamp columns or table refresh metadata")
            return {"source": None, "target": None, "method": "unresolved"}, [review]

        date_column_names_lower = {column.name.lower() for column in date_columns}
        date_column_by_lower = {column.name.lower(): column for column in date_columns}

        # ── Step 3-7: query trusted context layer ────────────────────────
        context_selected = self._resolve_audit_from_context(
            pair, date_columns, date_column_names_lower, date_column_by_lower,
        )
        if context_selected is not None:
            selected = context_selected
            # Build the confidence from the context ranking score (high trust)
            confidence = 0.92
            self._event(
                "info", "audit_column_context_selected",
                pair_id=pair.pair_id,
                selected=selected.name,
                confidence=confidence,
            )
            mapping_by_target = {row["target_column"].lower(): row["source_column"] for row in mappings}
            source = mapping_by_target.get(selected.name.lower()) if pair.mode == "migration" else None
            proposal = {"source": source, "target": selected.name, "method": "trusted_context"}
            if confidence >= self.config.project.confidence.auto_accept and (pair.mode != "migration" or source):
                return proposal, []
            review = self._review(pair, "audit_column", "table", proposal, confidence, {
                "method": "context_layer",
                "candidates": [{"column": selected.name, "score": round(confidence, 4)}],
            })
            return proposal, [review]

        # ── Step 8: fallback to static AUDIT_PATTERNS ────────────────────
        self._event(
            "info", "audit_column_fallback_patterns",
            pair_id=pair.pair_id,
            reason="no_context_match",
        )
        candidates: list[tuple[float, ColumnMetadata]] = []
        audit_roles = {
            str(name).lower() for name, details in (context.get("columns", {}) or {}).items()
            if str((details or {}).get("role", "")).lower() in {"audit", "freshness", "updated_at"}
        }
        for column in date_columns:
            normalized = _name(column.name)
            pattern_score = max(
                (SequenceMatcher(None, normalized, item).ratio() for item in AUDIT_PATTERNS),
                default=0,
            )
            description_tokens = _tokens(column.description or "")
            description_score = 0.08 if description_tokens & self._FRESHNESS_TOKENS else 0.0
            role_score = 0.12 if column.name.lower() in audit_roles else 0.0
            profile = self._profile_audit_candidate(pair, column.name, table_dir)
            rows = float(profile.get("row_count") or 0)
            nulls = float(profile.get("null_count") or 0)
            profile_score = 0.08 * max(0.0, 1.0 - (nulls / rows)) if rows else 0.0
            if profile.get("max_timestamp") is not None:
                profile_score += 0.04
            confidence = min(0.98, 0.48 + 0.28 * pattern_score + description_score + role_score + profile_score)
            candidates.append((confidence, column))

        confidence, selected = max(candidates, key=lambda item: item[0])
        mapping_by_target = {row["target_column"].lower(): row["source_column"] for row in mappings}
        source = mapping_by_target.get(selected.name.lower()) if pair.mode == "migration" else None
        proposal = {"source": source, "target": selected.name, "method": "inferred_column"}
        if confidence >= self.config.project.confidence.auto_accept and (pair.mode != "migration" or source):
            return proposal, []

        # ── Step 9: unresolved → human review ────────────────────────────
        self._event(
            "info", "audit_column_unresolved",
            pair_id=pair.pair_id,
            best_candidate=selected.name,
            confidence=round(confidence, 4),
        )
        if self._metadata_freshness_available(pair, metadata):
            self._event(
                "info", "audit_column_using_table_metadata", pair_id=pair.pair_id,
                rejected_candidate=selected.name, confidence=round(confidence, 4),
            )
            return {"source": None, "target": None, "method": "table_metadata"}, []
        review = self._review(pair, "audit_column", "table", proposal, confidence, {
            "candidates": [{"column": item[1].name, "score": round(item[0], 4)} for item in sorted(candidates, reverse=True)[:5]]
        })
        return proposal, [review]

    def _profile_audit_candidate(
        self, pair: TablePair, column: str, table_dir: Path
    ) -> dict[str, Any]:
        compiler = SQLCompiler("bigquery")
        rendered = compiler.identifier(column)
        sql = (
            f"SELECT COUNT(*) AS row_count, "
            f"SUM(CASE WHEN {rendered} IS NULL THEN 1 ELSE 0 END) AS null_count, "
            f"MAX({rendered}) AS max_timestamp FROM {compiler.table(pair.target_name)}"
        )
        try:
            frame = self._controlled_execute(
                "target", sql, {pair.target_name}, table_dir, f"profile_audit_{column}"
            )
            return self._first_row(frame)
        except Exception as exc:
            self._event(
                "warning", "audit_column_profile_failed", pair_id=pair.pair_id,
                column=column, error=str(exc),
            )
            return {}

    @staticmethod
    def _metadata_freshness_available(pair: TablePair, metadata: dict[str, Any]) -> bool:
        target = metadata.get("target_table", {}).get("modified_at")
        source = metadata.get("source_table", {}).get("modified_at")
        return bool(target and (pair.mode != "migration" or source))

    # ------------------------------------------------------------------
    # Context-layer audit-column resolution helpers
    # ------------------------------------------------------------------

    # Tokens that suggest a column tracks *updates / ingestion* rather
    # than *creation*.  These are preferred for freshness comparisons.
    _FRESHNESS_TOKENS: frozenset[str] = frozenset({
        "update", "updated", "modify", "modified", "load", "loaded",
        "ingest", "ingestion", "ingested", "extract", "extracted",
        "refresh", "refreshed", "batch", "etl", "sync", "synced",
    })

    def _resolve_audit_from_context(
        self,
        pair: TablePair,
        date_columns: list[ColumnMetadata],
        date_column_names_lower: set[str],
        date_column_by_lower: dict[str, ColumnMetadata],
    ) -> ColumnMetadata | None:
        """Try to resolve the audit column using the trusted context layer.

        Returns the best-matching ``ColumnMetadata`` from *date_columns*,
        or ``None`` when no suitable context-derived match is found.
        """
        # Determine which side we are resolving for
        side = "target"  # audit columns are resolved on target for the pair

        # ── Retrieve trusted context records ─────────────────────────────
        try:
            from .context_store import read_context

            if not self.config.project.context_store.enabled:
                self._event(
                    "info", "audit_column_context_disabled",
                    pair_id=pair.pair_id,
                )
                return None

            trusted = read_context(self.config, "trusted_context_current")
        except Exception as exc:
            self._event(
                "warning", "audit_column_context_unavailable",
                pair_id=pair.pair_id,
                error=str(exc),
            )
            return None

        if trusted.empty:
            self._event(
                "info", "audit_column_context_empty",
                pair_id=pair.pair_id,
            )
            return None

        # ── Extract approved audit columns per side ──────────────────────
        #   Look in business_context records whose payload contains
        #   ``audit_columns`` with a ``source`` or ``target`` value.
        source_audit_names: list[str] = []
        target_audit_names: list[str] = []

        for row in trusted.to_dict("records"):
            # Only business_context records carry audit_columns
            if str(row.get("context_type", "")) != "business_context":
                continue
            # Only TRUSTED + active (the view already filters status=TRUSTED,
            # but be defensive)
            if str(row.get("status", "")).upper() not in {"TRUSTED", ""}:
                continue

            payload_raw = row.get("payload_json") or row.get("payload") or "{}"
            try:
                payload = json.loads(payload_raw) if isinstance(payload_raw, str) else (payload_raw or {})
            except (json.JSONDecodeError, TypeError):
                continue

            audit = payload.get("audit_columns")
            if not isinstance(audit, dict):
                continue

            if audit.get("source"):
                source_audit_names.append(str(audit["source"]).strip())
            if audit.get("target"):
                target_audit_names.append(str(audit["target"]).strip())

        # ── Pick the right side's list ───────────────────────────────────
        # For a *target* table → use target-side approved names.
        # (source-side context would only be used if we were resolving the
        #  source audit column on migration pairs, which is derived from the
        #  mapping once the target column is chosen.)
        side_names = target_audit_names
        side_label = "target"
        # Optionally also consult source-side names when the table itself is
        # a source-only table (pair.mode == "bigquery_only" never has source
        # tables, so this only matters for migration pairs — and even then we
        # pick the target audit column first).

        self._event(
            "info", "audit_column_context_candidates",
            pair_id=pair.pair_id,
            side=side_label,
            source_context_count=len(source_audit_names),
            target_context_count=len(target_audit_names),
            candidates=list(set(side_names))[:20],
        )

        if not side_names:
            self._event(
                "info", "audit_column_context_no_candidates",
                pair_id=pair.pair_id,
                side=side_label,
            )
            return None

        # ── Rank the candidates ──────────────────────────────────────────
        ranked = self._rank_context_audit_candidates(
            pair, side_names, date_columns, date_column_names_lower, date_column_by_lower,
        )
        if not ranked:
            self._event(
                "info", "audit_column_context_no_compatible_match",
                pair_id=pair.pair_id,
                side=side_label,
            )
            return None

        # Return the top-ranked column
        best_score, best_column = ranked[0]
        self._event(
            "info", "audit_column_context_ranked",
            pair_id=pair.pair_id,
            side=side_label,
            ranking=[
                {"column": col.name, "score": round(sc, 4)}
                for sc, col in ranked[:5]
            ],
            selected=best_column.name,
            selected_score=round(best_score, 4),
        )
        return best_column

    def _rank_context_audit_candidates(
        self,
        pair: TablePair,
        context_names: list[str],
        date_columns: list[ColumnMetadata],
        date_column_names_lower: set[str],
        date_column_by_lower: dict[str, ColumnMetadata],
    ) -> list[tuple[float, ColumnMetadata]]:
        """Rank date/timestamp columns against context-derived audit names.

        Ranking criteria (deterministic, highest wins):
          1. Exact column-name match                          → +0.40
          2. Frequency among previously approved tables       → +0.25 × (freq / max_freq)
          3. Similarity to approved audit-column names        → +0.15 × best_ratio
          4. Column-description relevance                     → +0.10
          5. Freshness suitability (update > create)          → +0.10

        Only columns that score above a minimum threshold (> 0) are returned.
        """
        # ── Pre-compute frequency map ────────────────────────────────────
        freq: dict[str, int] = {}
        for name in context_names:
            freq[name.lower()] = freq.get(name.lower(), 0) + 1
        max_freq = max(freq.values()) if freq else 1

        scored: list[tuple[float, ColumnMetadata]] = []
        for column in date_columns:
            col_lower = column.name.lower()
            col_normalized = _name(column.name)
            score = 0.0

            # 1. Exact column-name match
            exact_match = col_lower in freq
            if exact_match:
                score += 0.40

            # 2. Frequency of use
            if exact_match:
                score += 0.25 * (freq[col_lower] / max_freq)
            else:
                # Check if any context name is similar enough for a partial
                # frequency credit
                best_freq_name = None
                best_freq_ratio = 0.0
                for ctx_name_lower in freq:
                    ratio = SequenceMatcher(None, col_normalized, _name(ctx_name_lower)).ratio()
                    if ratio > best_freq_ratio:
                        best_freq_ratio = ratio
                        best_freq_name = ctx_name_lower
                if best_freq_ratio >= 0.80 and best_freq_name is not None:
                    score += 0.15 * best_freq_ratio * (freq[best_freq_name] / max_freq)

            # 3. Similarity to approved audit-column aliases
            if not exact_match:
                best_sim = max(
                    (SequenceMatcher(None, col_normalized, _name(ctx)).ratio() for ctx in freq),
                    default=0,
                )
                score += 0.15 * best_sim

            # 4. Column-description relevance
            if column.description:
                desc_tokens = _tokens(column.description)
                audit_keywords = {"audit", "freshness", "timestamp", "update", "load",
                                  "ingestion", "modified", "etl", "batch", "extract"}
                overlap = len(desc_tokens & audit_keywords) / max(1, len(audit_keywords))
                score += 0.10 * min(overlap * 3, 1.0)  # cap at 0.10

            # 5. Freshness suitability
            col_tokens = _tokens(column.name)
            if col_tokens & self._FRESHNESS_TOKENS:
                score += 0.10
            elif col_tokens & {"create", "created", "creation", "insert", "inserted"}:
                score += 0.03  # creation timestamps are less preferred

            if score > 0:
                scored.append((round(score, 6), column))

        # Sort descending by score, then alphabetically for determinism
        scored.sort(key=lambda item: (-item[0], item[1].name))
        return scored

    def _resolve_scd_and_filters(
        self,
        pair: TablePair,
        metadata: dict[str, Any],
        mappings: list[dict[str, Any]],
        context: dict[str, Any],
        table_dir: Path,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        configured_filters = context.get("filters", {})
        filters = {
            "source": list(configured_filters.get("source", [])),
            "target": list(configured_filters.get("target", [])),
        }
        self._validate_filters(pair, metadata, filters)
        scd = context.get("scd2", {})
        if scd.get("enabled") is False:
            return filters, []
        if scd.get("enabled") is True and scd.get("current_filter"):
            current = {
                "source": list(scd["current_filter"].get("source", [])),
                "target": list(scd["current_filter"].get("target", [])),
            }
            self._validate_filters(pair, metadata, current)
            filters["source"].extend(current["source"])
            filters["target"].extend(current["target"])
            return filters, []
        target_columns = [ColumnMetadata(**item) for item in metadata["target"]]
        table_text = " ".join(filter(None, [
            str(context.get("description") or ""),
            str(metadata.get("target_table", {}).get("description") or ""),
        ])).lower()
        scd_evidence = "scd" in table_text or "slowly changing" in table_text
        end_names = {"effectiveenddate", "validto", "expirydate", "expirationdate", "enddate"}
        start_names = {"effectivestartdate", "validfrom", "startdate"}
        has_date_range = any(_name(column.name) in end_names for column in target_columns) and any(
            _name(column.name) in start_names for column in target_columns
        )
        flag_candidates: list[tuple[float, ColumnMetadata]] = []
        for column in target_columns:
            name_match = _name(column.name) in CURRENT_FLAG_NAMES
            description = _tokens(column.description or "")
            description_match = bool(description & {"active", "current"}) and bool(
                description & {"flag", "indicator", "record", "row"}
            )
            if name_match or description_match:
                flag_candidates.append(((0.7 if name_match else 0.55) + (0.1 if description_match else 0), column))
        flag = max(flag_candidates, default=(0.0, None), key=lambda item: item[0])[1]
        if not flag:
            end_column = next(
                (
                    column for column in target_columns
                    if _name(column.name) in end_names
                    and normalized_type(column.data_type) in {"date", "timestamp"}
                ),
                None,
            )
            if not end_column:
                return filters, []
            mapping_by_target = {row["target_column"].lower(): row["source_column"] for row in mappings}
            source_end = mapping_by_target.get(end_column.name.lower())
            profile = self._profile_scd_end_date(pair, end_column.name, table_dir)
            target_filter: dict[str, Any]
            reason: str
            if int(profile.get("null_count") or 0) > 0:
                target_filter = {"column": end_column.name, "operator": "is_null"}
                reason = "Null end date represents current records"
            else:
                maximum_value = profile.get("max_value")
                year_match = re.match(r"^(\d{4})", str(maximum_value or ""))
                maximum_year = int(year_match.group(1)) if year_match else 0
                if maximum_year < 2999:
                    return filters, []
                sentinel = str(maximum_value)
                if normalized_type(end_column.data_type) == "date":
                    sentinel = sentinel[:10]
                target_filter = {"column": end_column.name, "operator": "eq", "value": sentinel}
                reason = "High-date sentinel represents current records"
            proposal = {
                "source": [{**target_filter, "column": source_end}] if source_end else [],
                "target": [target_filter],
            }
            confidence = 0.88 if (scd_evidence or has_date_range) else 0.76
            evidence = {"reason": reason, "column": end_column.as_dict(), "profile": profile}
            if confidence >= self.config.project.confidence.auto_accept and (pair.mode != "migration" or source_end):
                filters["source"].extend(proposal["source"])
                filters["target"].extend(proposal["target"])
                return filters, []
            return filters, [self._review(
                pair, "scd2_current_filter", "table", proposal, confidence, evidence,
            )]
        mapping_by_target = {row["target_column"].lower(): row["source_column"] for row in mappings}
        source_flag = mapping_by_target.get(flag.name.lower())
        profile = self._profile_scd_flag(pair, flag.name, table_dir)
        active_value = self._active_flag_value(flag, profile)
        if active_value is None:
            return filters, []
        proposal = {
            "source": [{"column": source_flag, "operator": "eq", "value": active_value}] if source_flag else [],
            "target": [{"column": flag.name, "operator": "eq", "value": active_value}],
        }
        confidence = 0.75 + (0.12 if scd_evidence or has_date_range else 0) + (
            0.08 if normalized_type(flag.data_type) == "boolean" else 0.04
        )
        confidence = min(confidence, 0.95)
        if confidence >= self.config.project.confidence.auto_accept and (pair.mode != "migration" or source_flag):
            filters["source"].extend(proposal["source"])
            filters["target"].extend(proposal["target"])
            return filters, []
        review = self._review(
            pair, "scd2_current_filter", "table", proposal, confidence,
            {"column": flag.as_dict(), "profile": profile, "active_value": active_value},
        )
        return filters, [review]

    @staticmethod
    def _validate_filters(
        pair: TablePair, metadata: dict[str, Any], filters: dict[str, list[dict[str, Any]]]
    ) -> None:
        for side in ("source", "target"):
            if side == "source" and pair.mode != "migration":
                continue
            available = {item["name"].lower() for item in metadata[side]}
            missing = [
                str(item.get("column")) for item in filters.get(side, [])
                if str(item.get("column", "")).lower() not in available
            ]
            if missing:
                raise ValueError(f"Configured {side} filter columns do not exist: {missing}")
            invalid_operators = [
                str(item.get("operator")) for item in filters.get(side, [])
                if str(item.get("operator", "eq")).lower() not in SQLCompiler.OPERATORS
            ]
            if invalid_operators:
                raise ValueError(f"Configured {side} filters use invalid operators: {invalid_operators}")
            for item in filters.get(side, []):
                if str(item.get("operator", "eq")).lower() in {"in", "not_in"} and (
                    not isinstance(item.get("value"), list) or not item["value"]
                ):
                    raise ValueError(f"Configured {side} IN filter requires a non-empty list: {item}")

    def _profile_scd_flag(self, pair: TablePair, column: str, table_dir: Path) -> dict[str, Any]:
        compiler = SQLCompiler("bigquery")
        rendered = compiler.identifier(column)
        sql = (
            f"SELECT {rendered} AS profile_value, COUNT(*) AS value_count "
            f"FROM {compiler.table(pair.target_name)} GROUP BY profile_value "
            f"ORDER BY value_count DESC LIMIT 10"
        )
        try:
            frame = self._controlled_execute(
                "target", sql, {pair.target_name}, table_dir, f"profile_scd_flag_{column}"
            )
            return {"values": frame.to_dict("records")}
        except Exception as exc:
            self._event("warning", "scd_flag_profile_failed", pair_id=pair.pair_id, column=column, error=str(exc))
            return {}

    def _profile_scd_end_date(self, pair: TablePair, column: str, table_dir: Path) -> dict[str, Any]:
        compiler = SQLCompiler("bigquery")
        rendered = compiler.identifier(column)
        sql = (
            f"SELECT COUNT(*) AS row_count, "
            f"SUM(CASE WHEN {rendered} IS NULL THEN 1 ELSE 0 END) AS null_count, "
            f"MAX({rendered}) AS max_value FROM {compiler.table(pair.target_name)}"
        )
        try:
            frame = self._controlled_execute(
                "target", sql, {pair.target_name}, table_dir, f"profile_scd_end_{column}"
            )
            return self._first_row(frame)
        except Exception as exc:
            self._event("warning", "scd_end_profile_failed", pair_id=pair.pair_id, column=column, error=str(exc))
            return {}

    @staticmethod
    def _active_flag_value(column: ColumnMetadata, profile: dict[str, Any]) -> Any:
        values = [row.get("profile_value") for row in profile.get("values", [])]
        group = normalized_type(column.data_type)
        if group == "boolean":
            return True if True in values or not values else None
        if group in {"integer", "decimal"}:
            return 1 if any(str(value) in {"1", "1.0"} for value in values) else None
        active_words = {"1", "active", "current", "true", "y", "yes"}
        return next((value for value in values if str(value).strip().lower() in active_words), None)

    def _resolve_relationships(
        self,
        pair: TablePair,
        metadata: dict[str, Any],
        context: dict[str, Any],
        filters: dict[str, list[dict[str, Any]]],
        table_dir: Path,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        self._event("info", "relationship_discovery_started", pair_id=pair.pair_id)
        child_metadata = {
            **metadata.get("target_table", {}),
            "table": pair.target_name,
            "columns": metadata["target"],
        }
        candidates, feature = discover_relationship_candidates(
            pair, child_metadata, self.relationship_config, context
        )
        if not candidates:
            self._event(
                "info", "relationship_discovery_completed", pair_id=pair.pair_id,
                candidates=0, executable=0, feature=feature,
            )
            write_json(table_dir / "relationship_candidates.json", [])
            return [], [], []

        metadata_by_table = {pair.target_name: child_metadata}
        for parent_table in sorted({str(item["parent_table"]) for item in candidates}):
            parent = self._get_table_metadata("target", parent_table)
            metadata_by_table[parent_table] = parent

        scored: list[dict[str, Any]] = []
        resolved: list[dict[str, Any]] = []
        reviews: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate = validate_relationship_metadata(candidate, metadata_by_table)
            parent_profile: dict[str, Any] = {}
            match_profile: dict[str, Any] = {}
            if candidate["metadata_status"] == "PASS":
                parent_key = (
                    f"parent:{candidate['parent_table']}:"
                    f"{','.join(candidate['parent_columns'])}:"
                    f"{json.dumps(candidate.get('parent_filters', []), sort_keys=True)}"
                )
                try:
                    if parent_key not in self._relationship_profile_cache:
                        frame = self._controlled_execute(
                            "target", parent_profile_sql(candidate),
                            {candidate["parent_table"]}, table_dir,
                            f"relationship_parent_{candidate['candidate_id']}",
                        )
                        self._relationship_profile_cache[parent_key] = self._first_row(frame)
                    parent_profile = self._relationship_profile_cache[parent_key]

                    match_key = (
                        f"match:{pair.target_name}:{','.join(candidate['child_columns'])}:"
                        f"{candidate['parent_table']}:{','.join(candidate['parent_columns'])}:"
                        f"{json.dumps(filters.get('target', []), sort_keys=True)}:"
                        f"{json.dumps(candidate.get('child_filters', []), sort_keys=True)}:"
                        f"{json.dumps(candidate.get('parent_filters', []), sort_keys=True)}"
                    )
                    if match_key not in self._relationship_profile_cache:
                        rule = relationship_rule(candidate)
                        sql = SQLCompiler("bigquery").compile_rule(
                            rule, pair, "target", filters.get("target", [])
                        )
                        frame = self._controlled_execute(
                            "target", sql, {pair.target_name, candidate["parent_table"]},
                            table_dir, f"relationship_match_{candidate['candidate_id']}",
                        )
                        self._relationship_profile_cache[match_key] = self._first_row(frame)
                    match_profile = self._relationship_profile_cache[match_key]
                except Exception as exc:
                    candidate["metadata_errors"].append(f"Profiling failed: {exc}")
                    candidate["metadata_status"] = "FAIL"
                    self._event(
                        "warning", "relationship_profile_failed", pair_id=pair.pair_id,
                        candidate_id=candidate["candidate_id"], error=str(exc),
                    )
            candidate = calculate_relationship_confidence(
                candidate, parent_profile, match_profile,
                self.config.project.confidence.auto_accept,
                self.config.project.confidence.review,
                self.relationship_config.settings.minimum_match_rate,
            )
            scored.append(candidate)
            self._event(
                "info", "relationship_candidate_decided", pair_id=pair.pair_id,
                candidate_id=candidate["candidate_id"], origin=candidate["origin"],
                decision=candidate["decision"], confidence=candidate["confidence"],
            )
            if candidate["decision"] == "AUTO":
                resolved.append(candidate_to_relationship(candidate))
            elif candidate["decision"] == "REVIEW":
                proposal = candidate_to_relationship(candidate)
                reviews.append(self._review(
                    pair, "relationship", str(proposal["id"]), proposal,
                    float(candidate["confidence"]), candidate, blocking=False,
                ))
        write_json(table_dir / "relationship_candidates.json", scored)
        self._event(
            "info", "relationship_discovery_completed", pair_id=pair.pair_id,
            candidates=len(scored), executable=len(resolved), reviews=len(reviews), feature=feature,
        )
        self._checkpoint(
            "RELATIONSHIPS", pair.pair_id, candidates=len(scored),
            executable=len(resolved), reviews=len(reviews),
        )
        return _jsonable(scored), _jsonable(resolved), reviews

    def _resolve_measures(
        self,
        pair: TablePair,
        metadata: dict[str, Any],
        mappings: list[dict[str, Any]],
        context: dict[str, Any],
        table_dir: Path,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        self._event("info", "LOAD_MEASURE_CONFIGURATION", pair_id=pair.pair_id)
        profiles: dict[str, dict[str, Any]] = {}
        inferred: list[dict[str, Any]] = []
        settings = self.measure_config.settings
        if settings.inference_enabled:
            preliminary = infer_measure_candidates(
                pair, metadata, mappings, {}, settings.max_inferred_per_table
            )
            if settings.profile_values:
                for candidate in preliminary:
                    column = str(candidate["target_expression"])
                    try:
                        sql = measure_profile_sql(pair.target_name, column)
                        frame = self._controlled_execute(
                            "target", sql, {pair.target_name}, table_dir,
                            f"measure_profile_{column}",
                        )
                        profiles[column] = self._first_row(frame)
                        self._event(
                            "info", "INFER_MEASURES", pair_id=pair.pair_id,
                            column=column, profile=profiles[column],
                        )
                    except Exception as exc:
                        self._event(
                            "warning", "INFER_MEASURES_PROFILE_FAILED",
                            pair_id=pair.pair_id, column=column, error=str(exc),
                        )
            inferred = infer_measure_candidates(
                pair, metadata, mappings, profiles, settings.max_inferred_per_table
            )

        resolved = resolve_measure_precedence(pair, self.measure_config, context, inferred)
        reviews: list[dict[str, Any]] = []
        usable: list[dict[str, Any]] = []
        for measure in resolved:
            measure = dict(measure)
            measure.setdefault("pair_id", pair.pair_id)
            measure.setdefault("source_table", pair.source_name)
            measure.setdefault("target_table", pair.target_name)
            measure.setdefault("source_filters", [])
            measure.setdefault("target_filters", [])
            measure.setdefault("group_by", {})
            measure.setdefault("date_column", {})
            measure.setdefault("severity", "error")
            measure.setdefault("confidence", 1.0)
            errors = validate_measure_metadata(measure, pair, metadata)
            measure["metadata_errors"] = errors
            self._event(
                "info", "RESOLVE_MEASURE_PRECEDENCE", pair_id=pair.pair_id,
                measure_id=measure["measure_id"], origin=measure.get("origin"),
                confidence=measure.get("confidence"), metadata_errors=errors,
            )
            if errors:
                reviews.append(self._review(
                    pair, "measure", str(measure["measure_id"]), measure,
                    0.0, {"metadata_errors": errors}, blocking=True,
                ))
                continue
            if measure.get("approval_status") == "pending":
                reviews.append(self._review(
                    pair, "measure", str(measure["measure_id"]), measure,
                    float(measure.get("confidence") or 0), measure.get("evidence", {}), blocking=True,
                ))
                continue
            if measure.get("origin") == "inference":
                confidence = float(measure.get("confidence") or 0)
                blocking = confidence < self.config.project.confidence.auto_accept
                if blocking or settings.propose_inferred_for_reuse:
                    reviews.append(self._review(
                        pair, "measure", str(measure["measure_id"]), measure,
                        confidence, measure.get("evidence", {}), blocking=blocking,
                    ))
                if blocking:
                    continue
            usable.append(measure)
        write_json(table_dir / "resolved_measures.json", usable)
        self._event(
            "info", "INFER_MEASURES_COMPLETED", pair_id=pair.pair_id,
            inferred=len(inferred), resolved=len(usable), approvals=len(reviews),
        )
        return usable, reviews

    def _execute_measure_reconciliations(
        self,
        pair: TablePair,
        measures: list[dict[str, Any]],
        metadata: dict[str, Any],
        mappings: list[dict[str, Any]],
        filters: dict[str, list[dict[str, Any]]],
        table_dir: Path,
    ) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        output: list[dict[str, Any]] = []
        groupings_by_measure: dict[str, list[dict[str, Any]]] = {}
        settings = self.measure_config.settings
        for measure in measures:
            measure_id = str(measure["measure_id"])
            groupings = select_reconciliation_groups(
                measure, metadata, mappings, settings.max_groupings_per_measure
            )
            groupings_by_measure[measure_id] = groupings
            self._event(
                "info", "SELECT_RECONCILIATION_GROUPS", pair_id=pair.pair_id,
                measure_id=measure_id, groupings=groupings,
            )
            if pair.mode != "migration":
                output.append({
                    "pair_id": pair.pair_id, "rule_id": f"{pair.pair_id}__measure__{measure_id}",
                    "measure_id": measure_id, "type": "measure_reconciliation",
                    "category": "measure_reconciliation", "status": "SKIP",
                    "reason": "Source-to-target reconciliation requires migration mode",
                    "origin": measure.get("origin"), "confidence": measure.get("confidence"),
                })
                continue
            for grouping in groupings:
                rule_id = f"{pair.pair_id}__measure__{measure_id}__{grouping['grouping_id']}"
                started_at = datetime.now(timezone.utc).isoformat()
                try:
                    source_sql = reconciliation_sql(
                        measure, pair, "source", grouping, filters.get("source", []),
                        settings.maximum_group_cardinality,
                    )
                    target_sql = reconciliation_sql(
                        measure, pair, "target", grouping, filters.get("target", []),
                        settings.maximum_group_cardinality,
                    )
                    self._event(
                        "info", "GENERATE_RECONCILIATION_QUERY", pair_id=pair.pair_id,
                        measure_id=measure_id, grouping=grouping["grouping_id"],
                        source_expression=measure.get("source_expression"),
                        target_expression=measure.get("target_expression"),
                    )
                    source_label = f"{rule_id}_source"
                    target_label = f"{rule_id}_target"
                    self._event(
                        "info", "EXECUTE_RECONCILIATION", pair_id=pair.pair_id,
                        measure_id=measure_id, grouping=grouping["grouping_id"], side="source",
                    )
                    source_frame = self._controlled_execute(
                        "source", source_sql, {pair.source_name or ""}, table_dir, source_label
                    )
                    self._event(
                        "info", "EXECUTE_RECONCILIATION", pair_id=pair.pair_id,
                        measure_id=measure_id, grouping=grouping["grouping_id"], side="target",
                    )
                    target_frame = self._controlled_execute(
                        "target", target_sql, {pair.target_name}, table_dir, target_label
                    )
                    if grouping["grouping_id"] != "overall" and (
                        len(source_frame) > settings.maximum_group_cardinality
                        or len(target_frame) > settings.maximum_group_cardinality
                    ):
                        raise ValueError(
                            f"Grouping cardinality exceeds {settings.maximum_group_cardinality} rows"
                        )
                    compared = compare_reconciliation_results(measure, grouping, source_frame, target_frame)
                    for row in compared:
                        output.append({
                            **row, "pair_id": pair.pair_id, "rule_id": rule_id,
                            "type": "measure_reconciliation", "category": "measure_reconciliation",
                            "severity": measure.get("severity", "error"),
                            "origin": measure.get("origin"), "confidence": measure.get("confidence"),
                            "source_table": pair.source_name, "target_table": pair.target_name,
                            "source_expression": measure.get("source_expression"),
                            "target_expression": measure.get("target_expression"),
                            "aggregation": measure.get("aggregation"),
                            "source_filters": [*filters.get("source", []), *measure.get("source_filters", [])],
                            "target_filters": [*filters.get("target", []), *measure.get("target_filters", [])],
                            "source_grouping": grouping.get("source", []),
                            "target_grouping": grouping.get("target", []),
                            "started_at": started_at,
                            "completed_at": datetime.now(timezone.utc).isoformat(),
                            "query_references": {
                                "source": str(table_dir / "sql" / f"{source_label}.sql"),
                                "target": str(table_dir / "sql" / f"{target_label}.sql"),
                            },
                        })
                except Exception as exc:
                    output.append({
                        "pair_id": pair.pair_id, "rule_id": rule_id, "measure_id": measure_id,
                        "grouping_id": grouping["grouping_id"], "type": "measure_reconciliation",
                        "category": "measure_reconciliation", "status": "ERROR",
                        "origin": measure.get("origin"), "confidence": measure.get("confidence"),
                        "error": str(exc), "stack_trace": traceback.format_exc(),
                        "started_at": started_at, "completed_at": datetime.now(timezone.utc).isoformat(),
                    })
                    self._event(
                        "exception", "EXECUTE_RECONCILIATION_FAILED", pair_id=pair.pair_id,
                        measure_id=measure_id, grouping=grouping["grouping_id"], error=str(exc),
                    )
        write_json(table_dir / "measure_reconciliation_results.json", output)
        pd.json_normalize(output, sep=".").to_csv(
            table_dir / "measure_reconciliation_results.csv", index=False
        )
        return _jsonable(output), groupings_by_measure

    def _plan_measure_diagnostics(
        self,
        failure: dict[str, Any],
        measure: dict[str, Any],
        groupings: list[dict[str, Any]],
        approved_learnings: list[dict[str, Any]],
    ) -> list[DiagnosticRequest]:
        settings = self.measure_config.diagnostics
        planned = default_diagnostic_requests(measure, groupings, settings)
        learned_types = [
            query_type
            for learning in approved_learnings
            if str(learning.get("measure_id")) == str(measure["measure_id"])
            for query_type in learning.get("investigated_hypotheses", [])
            if query_type in settings.allowed_types
        ]
        if learned_types:
            planned.sort(key=lambda request: (request.query_type not in learned_types, request.query_type))
        if self.config.llm.enabled and settings.maximum_llm_calls_per_failure > 0:
            try:
                adapter = self.llm
                if adapter:
                    response = adapter.complete_structured(
                        "Select useful diagnostic intents from the allowlist. Do not produce SQL or identifiers.",
                        {
                            "failure": failure,
                            "measure_id": measure["measure_id"],
                            "allowed_types": settings.allowed_types,
                            "approved_grouping_ids": [item["grouping_id"] for item in groupings],
                            "has_approved_date_columns": bool((measure.get("date_column") or {}).get("source")),
                            "maximum_steps": settings.maximum_steps_per_failure,
                            "approved_reusable_learnings": approved_learnings,
                        },
                        DiagnosticPlanResponse,
                    )
                    valid: list[DiagnosticRequest] = []
                    for request in response.requests[: settings.maximum_steps_per_failure]:
                        validate_diagnostic_request(request, measure, groupings, settings)
                        valid.append(request)
                    if valid:
                        planned = valid
            except Exception as exc:
                self._event(
                    "warning", "PLAN_RCA_DIAGNOSTIC_LLM_UNAVAILABLE",
                    pair_id=failure.get("pair_id"), measure_id=measure["measure_id"], error=str(exc),
                )
        return planned[: settings.maximum_steps_per_failure]

    def _investigate_measure_failures(
        self,
        pair: TablePair,
        failures: list[dict[str, Any]],
        measures: list[dict[str, Any]],
        groupings_by_measure: dict[str, list[dict[str, Any]]],
        filters: dict[str, list[dict[str, Any]]],
        context: dict[str, Any],
        table_dir: Path,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        output: list[dict[str, Any]] = []
        reviews: list[dict[str, Any]] = []
        measure_by_id = {str(item["measure_id"]): item for item in measures}
        seen: set[str] = set()
        settings = self.measure_config.diagnostics
        for failure in failures[: settings.maximum_failed_samples]:
            rule_id = str(failure["rule_id"])
            if rule_id in seen:
                continue
            seen.add(rule_id)
            measure = measure_by_id.get(str(failure.get("measure_id")))
            if not measure:
                continue
            groupings = groupings_by_measure.get(str(measure["measure_id"]), [])
            requests = self._plan_measure_diagnostics(
                failure, measure, groupings, list(context.get("rca_learnings", []))
            )
            self._event(
                "info", "PLAN_RCA_DIAGNOSTIC", pair_id=pair.pair_id,
                measure_id=measure["measure_id"], requests=[item.model_dump() for item in requests],
            )
            evidence: list[dict[str, Any]] = []
            for index, request in enumerate(requests):
                try:
                    validate_diagnostic_request(request, measure, groupings, settings)
                    self._event(
                        "info", "VALIDATE_DIAGNOSTIC_REQUEST", pair_id=pair.pair_id,
                        measure_id=measure["measure_id"], request=request.model_dump(),
                    )
                    frames: dict[str, pd.DataFrame] = {}
                    references: dict[str, str] = {}
                    for side in ("source", "target"):
                        sql = diagnostic_sql(
                            request, measure, pair, side, groupings, filters.get(side, []),
                            self.measure_config.settings.maximum_group_cardinality,
                        )
                        label = f"{rule_id}__diagnostic_{index}_{request.query_type}_{side}"
                        allowed = {pair.source_name or ""} if side == "source" else {pair.target_name}
                        self._event(
                            "info", "EXECUTE_DIAGNOSTIC_QUERY", pair_id=pair.pair_id,
                            measure_id=measure["measure_id"], query_type=request.query_type, side=side,
                        )
                        frames[side] = self._controlled_execute(side, sql, allowed, table_dir, label)
                        references[side] = str(table_dir / "sql" / f"{label}.sql")
                    evidence.append({
                        "request": request.model_dump(),
                        "source": frames["source"].head(settings.maximum_failed_samples).to_dict("records"),
                        "target": frames["target"].head(settings.maximum_failed_samples).to_dict("records"),
                        "query_references": references,
                    })
                except Exception as exc:
                    evidence.append({"request": request.model_dump(), "error": str(exc)})
                    self._event(
                        "warning", "EXECUTE_DIAGNOSTIC_QUERY_FAILED", pair_id=pair.pair_id,
                        measure_id=measure["measure_id"], query_type=request.query_type, error=str(exc),
                    )
            rca = evidence_based_rca(failure, evidence)
            rca.update({"pair_id": pair.pair_id, "rule_id": rule_id, "diagnostics": evidence})
            output.append(rca)
            self._event(
                "info", "GENERATE_EVIDENCE_BASED_RCA", pair_id=pair.pair_id,
                measure_id=measure["measure_id"], classification=rca["classification"],
                confidence=rca["confidence"], evidence=rca["evidence_observed"],
            )
            if rca.get("reusable_candidate"):
                reviews.append(self._review(
                    pair, "rca_learning", str(measure["measure_id"]), rca,
                    float(rca["confidence"]), evidence, blocking=False,
                ))
        write_json(table_dir / "measure_diagnostics.json", output)
        return _jsonable(output), reviews

    def _generate_rules(
        self,
        pair: TablePair,
        metadata: dict[str, Any],
        mappings: list[dict[str, Any]],
        context: dict[str, Any],
        keys: dict[str, list[str]],
        audit: dict[str, Any],
        relationships: list[dict[str, Any]],
    ) -> list[RuleSpec]:
        rules: list[RuleSpec] = []
        target_types = {item["name"].lower(): normalized_type(item["data_type"]) for item in metadata["target"]}
        configured_columns = {str(name).lower() for name in (context.get("columns", {}) or {})}
        missing_context_columns = configured_columns - set(target_types)
        missing_domain_columns = {
            str(item.get("target_column", "")).lower() for item in context.get("domains", [])
        } - set(target_types)
        missing_domain_columns.discard("")
        invalid_domains = [
            item.get("target_column") for item in context.get("domains", [])
            if not isinstance(item.get("values"), list) or not item["values"]
        ]
        invalid_column_domains = [
            name for name, details in (context.get("columns", {}) or {}).items()
            if "accepted_values" in (details or {}) and (
                not isinstance(details.get("accepted_values"), list) or not details["accepted_values"]
            )
        ]
        if missing_context_columns or missing_domain_columns:
            raise ValueError(
                "Business context references missing target columns; "
                f"columns={sorted(missing_context_columns)}, "
                f"domains={sorted(missing_domain_columns)}"
            )
        if invalid_domains or invalid_column_domains:
            raise ValueError(
                "Business context domains require non-empty value lists; "
                f"domains={invalid_domains}, columns={invalid_column_domains}"
            )
        if pair.mode == "migration":
            rules.append(RuleSpec(
                rule_id=f"{pair.pair_id}__row_count", pair_id=pair.pair_id,
                type="row_count", scope="compare", category="reconciliation",
                tolerance=context.get("row_count_tolerance", {}),
                description="Filtered source and target row counts match",
            ))
        for mapping in mappings:
            source, target = mapping["source_column"], mapping["target_column"]
            if pair.mode == "migration":
                rules.extend([
                    RuleSpec(
                        rule_id=f"{pair.pair_id}__schema__{target}", pair_id=pair.pair_id,
                        type="schema", scope="compare", category="schema",
                        source_columns=[source], target_columns=[target],
                    ),
                    RuleSpec(
                        rule_id=f"{pair.pair_id}__null__{target}", pair_id=pair.pair_id,
                        type="null_count", scope="compare", category="completeness",
                        source_columns=[source], target_columns=[target],
                    ),
                    RuleSpec(
                        rule_id=f"{pair.pair_id}__distinct__{target}", pair_id=pair.pair_id,
                        type="distinct_count", scope="compare", category="distribution",
                        source_columns=[source], target_columns=[target],
                    ),
                ])
                if target_types.get(target.lower()) in {"string", "boolean"}:
                    rules.append(RuleSpec(
                        rule_id=f"{pair.pair_id}__distribution__{target}", pair_id=pair.pair_id,
                        type="value_distribution", scope="compare", category="distribution",
                        source_columns=[source], target_columns=[target], parameters={"limit": 20},
                        tolerance=context.get("distribution_tolerance", {}),
                    ))
        if keys.get("target"):
            rules.append(RuleSpec(
                rule_id=f"{pair.pair_id}__target_key_unique", pair_id=pair.pair_id,
                type="uniqueness", scope="target", category="uniqueness",
                target_columns=keys["target"],
            ))
            if pair.mode == "migration" and keys.get("source"):
                rules.append(RuleSpec(
                    rule_id=f"{pair.pair_id}__source_key_unique", pair_id=pair.pair_id,
                    type="uniqueness", scope="source", category="uniqueness",
                    source_columns=keys["source"],
                ))
                rules.append(RuleSpec(
                    rule_id=f"{pair.pair_id}__key_buckets", pair_id=pair.pair_id,
                    type="key_buckets", scope="compare", category="reconciliation",
                    source_columns=keys["source"], target_columns=keys["target"],
                    parameters={"buckets": self.config.project.query_limits.key_hash_buckets},
                ))
                rules.append(RuleSpec(
                    rule_id=f"{pair.pair_id}__exact_keys_when_small", pair_id=pair.pair_id,
                    type="key_values", scope="compare", category="reconciliation",
                    source_columns=keys["source"], target_columns=keys["target"],
                    parameters={
                        "limit": min(
                            self.config.project.query_limits.small_table_row_limit,
                            self.config.project.query_limits.max_result_rows,
                        )
                    },
                    description="Exact hashed key comparison when both filtered tables fit within the configured limit",
                ))
                if mappings:
                    rules.append(RuleSpec(
                        rule_id=f"{pair.pair_id}__mapped_rows_when_small", pair_id=pair.pair_id,
                        type="row_reconciliation", scope="compare", category="reconciliation",
                        source_columns=[item["source_column"] for item in mappings],
                        target_columns=[item["target_column"] for item in mappings],
                        parameters={
                            "source_keys": keys["source"], "target_keys": keys["target"],
                            "limit": min(
                                self.config.project.query_limits.small_table_row_limit,
                                self.config.project.query_limits.max_result_rows,
                            ),
                        },
                        description="Bounded mapped-row comparison; output contains hashes only",
                    ))
            for key_column in keys["target"]:
                rules.append(RuleSpec(
                    rule_id=f"{pair.pair_id}__target_key_not_null__{key_column}", pair_id=pair.pair_id,
                    type="null_count", scope="target", category="completeness",
                    target_columns=[key_column],
                    description="Every primary-key component must not be null",
                ))
            if pair.mode == "migration":
                for key_column in keys.get("source", []):
                    rules.append(RuleSpec(
                        rule_id=f"{pair.pair_id}__source_key_not_null__{key_column}", pair_id=pair.pair_id,
                        type="null_count", scope="source", category="completeness",
                        source_columns=[key_column],
                        description="Every source primary-key component must not be null",
                    ))
        if pair.mode == "bigquery_only":
            key_names = {name.lower() for name in keys.get("target", [])}
            for item in metadata["target"]:
                type_group = target_types.get(item["name"].lower(), "other")
                if type_group in {"integer", "decimal", "string", "boolean", "date", "timestamp", "binary"}:
                    rules.append(RuleSpec(
                        rule_id=f"{pair.pair_id}__profile__{item['name']}", pair_id=pair.pair_id,
                        type="column_profile", scope="target", category="profiling",
                        target_columns=[item["name"]],
                        parameters={"informational": True, "include_min_max": type_group in {
                            "integer", "decimal", "string", "date", "timestamp"
                        }},
                        description="Informational BigQuery column profile",
                    ))
                    if type_group in {"string", "boolean"}:
                        rules.append(RuleSpec(
                            rule_id=f"{pair.pair_id}__distribution__{item['name']}", pair_id=pair.pair_id,
                            type="value_distribution", scope="target", category="profiling",
                            target_columns=[item["name"]], parameters={"limit": 20, "informational": True},
                        ))
                if not item.get("nullable") and item["name"].lower() not in key_names:
                    rules.append(RuleSpec(
                        rule_id=f"{pair.pair_id}__required_not_null__{item['name']}",
                        pair_id=pair.pair_id, type="null_count", scope="target",
                        category="completeness", target_columns=[item["name"]],
                    ))
        if audit.get("target"):
            rules.append(RuleSpec(
                rule_id=f"{pair.pair_id}__freshness", pair_id=pair.pair_id,
                type="freshness", scope="compare" if pair.mode == "migration" else "target",
                category="freshness",
                source_columns=[audit["source"]] if audit.get("source") else [],
                target_columns=[audit["target"]],
                tolerance={"minutes": context.get("freshness_sla_minutes", 1440)},
            ))
        elif audit.get("method") == "table_metadata":
            rules.append(RuleSpec(
                rule_id=f"{pair.pair_id}__table_freshness", pair_id=pair.pair_id,
                type="table_freshness", scope="compare" if pair.mode == "migration" else "target",
                category="freshness",
                parameters={
                    "source_timestamp": metadata.get("source_table", {}).get("modified_at"),
                    "target_timestamp": metadata.get("target_table", {}).get("modified_at"),
                },
                tolerance={"minutes": context.get("freshness_sla_minutes", 1440)},
                description="Freshness based on warehouse table modification metadata",
            ))
        for domain in context.get("domains", []):
            rules.append(RuleSpec(
                rule_id=f"{pair.pair_id}__domain__{domain['target_column']}", pair_id=pair.pair_id,
                type="domain", scope="target", category="validity",
                target_columns=[domain["target_column"]], parameters={"values": domain["values"]},
            ))
        for column_name, details in (context.get("columns", {}) or {}).items():
            details = details or {}
            if details.get("required"):
                rules.append(RuleSpec(
                    rule_id=f"{pair.pair_id}__context_required__{column_name}", pair_id=pair.pair_id,
                    type="null_count", scope="target", category="business_rule",
                    target_columns=[column_name], description=details.get("description"),
                ))
            if isinstance(details.get("accepted_values"), list):
                rules.append(RuleSpec(
                    rule_id=f"{pair.pair_id}__context_domain__{column_name}", pair_id=pair.pair_id,
                    type="domain", scope="target", category="business_rule",
                    target_columns=[column_name], parameters={"values": details["accepted_values"]},
                ))
            for boundary, operator in (("minimum", "gte"), ("maximum", "lte")):
                if details.get(boundary) is not None:
                    rules.append(RuleSpec(
                        rule_id=f"{pair.pair_id}__context_{boundary}__{column_name}", pair_id=pair.pair_id,
                        type="predicate", scope="target", category="business_rule",
                        target_columns=[column_name],
                        parameters={"predicate": {
                            "column": column_name, "operator": operator, "value": details[boundary]
                        }},
                    ))
        for relationship in relationships:
            if not relationship.get("enabled", True):
                continue
            child_columns = relationship.get("child_columns", relationship.get("target_columns", []))
            parent_table = relationship.get("parent_table", relationship.get("referenced_table"))
            parent_columns = relationship.get("parent_columns", relationship.get("referenced_columns", []))
            parent_filters = relationship.get("parent_filters", [])
            if relationship.get("active_filter") and not parent_filters:
                parent_filters = [relationship["active_filter"]]
            rules.append(RuleSpec(
                rule_id=f"{pair.pair_id}__relationship__{relationship['id']}", pair_id=pair.pair_id,
                type="relationship", scope="target", category="referential_integrity",
                target_columns=child_columns,
                parameters={
                    "referenced_table": parent_table,
                    "referenced_columns": parent_columns,
                    "child_filters": relationship.get("child_filters", []),
                    "referenced_filters": parent_filters,
                    "relationship_origin": relationship.get("origin", "configured"),
                    "confidence": relationship.get("confidence", 1.0),
                },
                origin=relationship.get("origin", "configured"),
                description=relationship.get("description"),
            ))
        for test in self.human_tests:
            if test.pair_id == pair.pair_id:
                errors = self._validate_human_test(pair, test, metadata)
                if errors:
                    rules.append(RuleSpec(
                        rule_id=test.test_id, pair_id=test.pair_id, type="configuration_error",
                        scope=test.scope, category="human", origin="human",
                        parameters={"errors": errors}, description=test.description,
                    ))
                else:
                    rules.append(self._human_rule(test))
        return rules

    def _validate_human_test(
        self, pair: TablePair, test: HumanTest, metadata: dict[str, Any]
    ) -> list[str]:
        errors: list[str] = []
        if pair.mode == "bigquery_only" and test.scope in {"source", "both", "compare"}:
            errors.append("BigQuery-only tables cannot execute source-scoped tests")
        for side, columns in (("source", test.source_columns), ("target", test.target_columns)):
            if side == "source" and pair.mode != "migration":
                continue
            available = {item["name"].lower() for item in metadata[side]}
            missing = [name for name in columns if name.lower() not in available]
            if missing:
                errors.append(f"{side} columns do not exist: {missing}")
        requires_column = test.type in {
            "aggregate", "distinct_count", "domain", "freshness", "null_count",
            "predicate", "uniqueness", "value_distribution",
        }
        if requires_column:
            if test.scope in {"source", "both", "compare"} and pair.mode == "migration" and not test.source_columns:
                errors.append("source_columns is required for the configured scope")
            if test.scope in {"target", "both", "compare"} and not test.target_columns:
                errors.append("target_columns is required for the configured scope")
        if test.type == "predicate":
            predicate_column = str(test.parameters.get("predicate", {}).get("column") or "")
            for side in ("source", "target"):
                if side == "source" and (pair.mode != "migration" or test.scope not in {"source", "both", "compare"}):
                    continue
                if side == "target" and test.scope not in {"target", "both", "compare"}:
                    continue
                available = {item["name"].lower() for item in metadata[side]}
                if predicate_column.lower() not in available:
                    errors.append(f"predicate column does not exist on {side}: {predicate_column}")
        if test.type == "freshness":
            for side, columns in (("source", test.source_columns), ("target", test.target_columns)):
                if side == "source" and pair.mode != "migration":
                    continue
                index = {item["name"].lower(): item for item in metadata[side]}
                for name in columns:
                    if name.lower() in index and normalized_type(index[name.lower()]["data_type"]) not in {"date", "timestamp"}:
                        errors.append(f"freshness column is not date/timestamp on {side}: {name}")
        if test.type == "relationship":
            parent_table = str(test.parameters.get("referenced_table") or "")
            parent = self._get_table_metadata("target", parent_table) if parent_table else {}
            parent_columns = {str(item.get("name", "")).lower() for item in parent.get("columns", [])}
            missing_parent = [
                name for name in test.parameters.get("referenced_columns", [])
                if str(name).lower() not in parent_columns
            ]
            if missing_parent:
                errors.append(f"referenced relationship columns do not exist: {missing_parent}")
        return errors

    @staticmethod
    def _human_rule(test: HumanTest) -> RuleSpec:
        return RuleSpec(
            rule_id=test.test_id, pair_id=test.pair_id, type=test.type, scope=test.scope,
            category="human", source_columns=test.source_columns,
            target_columns=test.target_columns, parameters=test.parameters,
            tolerance=test.tolerance, severity=test.severity, origin="human",
            source_sql=test.source_sql, target_sql=test.target_sql, description=test.description,
        )

    def _execute_rule(
        self,
        pair: TablePair,
        rule: RuleSpec,
        metadata: dict[str, Any],
        filters: dict[str, list[dict[str, Any]]],
        table_dir: Path,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "pair_id": pair.pair_id, "rule_id": rule.rule_id, "type": rule.type,
            "category": rule.category, "severity": rule.severity, "origin": rule.origin,
            "description": rule.description, "status": "RUNNING",
        }
        if rule.type == "relationship":
            row.update({
                "relationship_origin": rule.parameters.get("relationship_origin", rule.origin),
                "confidence": rule.parameters.get("confidence"),
                "parent_table": rule.parameters.get("referenced_table"),
                "child_columns": rule.target_columns,
                "parent_columns": rule.parameters.get("referenced_columns", []),
                "child_filters": rule.parameters.get("child_filters", []),
                "parent_filters": rule.parameters.get("referenced_filters", []),
            })
        self._event("info", "rule_started", pair_id=pair.pair_id, rule_id=rule.rule_id)
        try:
            evidence: dict[str, Any] = {}
            if rule.type == "configuration_error":
                row.update({"status": "ERROR", "error": "; ".join(rule.parameters.get("errors", []))})
                self._event("error", "rule_configuration_invalid", pair_id=pair.pair_id, rule_id=rule.rule_id, errors=rule.parameters.get("errors", []))
                self._event("info", "rule_finished", pair_id=pair.pair_id, rule_id=rule.rule_id, status=row["status"])
                return row
            if rule.type == "row_reconciliation":
                row.update(self._execute_row_reconciliation(pair, rule, filters, table_dir))
                self._event("info", "rule_finished", pair_id=pair.pair_id, rule_id=rule.rule_id, status=row["status"])
                return _jsonable(row)
            if rule.type == "table_freshness":
                evidence = {
                    "source": {"max_timestamp": rule.parameters.get("source_timestamp")},
                    "target": {"max_timestamp": rule.parameters.get("target_timestamp")},
                }
                if pair.mode == "bigquery_only":
                    evidence.pop("source")
                status, comparison = self._compare(rule, pair, evidence)
                row.update({"status": status, "evidence": evidence, "comparison": comparison})
                self._event("info", "rule_finished", pair_id=pair.pair_id, rule_id=rule.rule_id, status=row["status"])
                return _jsonable(row)
            if rule.type == "schema":
                source_meta = {item["name"].lower(): item for item in metadata["source"]}
                target_meta = {item["name"].lower(): item for item in metadata["target"]}
                source_type = source_meta[rule.source_columns[0].lower()]["data_type"]
                target_type = target_meta[rule.target_columns[0].lower()]["data_type"]
                compatible = normalized_type(source_type) == normalized_type(target_type) or {
                    normalized_type(source_type), normalized_type(target_type)
                } <= {"integer", "decimal"} or {
                    normalized_type(source_type), normalized_type(target_type)
                } <= {"date", "timestamp"}
                row.update({
                    "status": "PASS" if compatible else "FAIL",
                    "evidence": {"source_type": source_type, "target_type": target_type},
                    "comparison": {"compatible": compatible},
                })
                self._event("info", "rule_finished", pair_id=pair.pair_id, rule_id=rule.rule_id, status=row["status"])
                return row
            sides: list[str] = []
            if rule.scope in {"source", "both", "compare"} and pair.mode == "migration":
                sides.append("source")
            if rule.scope in {"target", "both", "compare"} or pair.mode == "bigquery_only":
                sides.append("target")
            for side in sides:
                dialect = "databricks" if side == "source" else "bigquery"
                sql = SQLCompiler(dialect).compile_rule(rule, pair, side, filters.get(side, []))
                result = self._controlled_execute(
                    side, sql, allowed_tables_for_rule(rule, pair, side), table_dir,
                    f"{rule.rule_id}_{side}",
                )
                if rule.type in {"value_distribution", "key_buckets", "key_values"}:
                    evidence[side] = [_jsonable(item) for item in result.to_dict("records")]
                else:
                    evidence[side] = self._first_row(result)
            status, comparison = self._compare(rule, pair, evidence)
            output_evidence = evidence
            if rule.type == "key_values":
                output_evidence = {
                    side: {
                        "returned_rows": len(records),
                        "total_rows": records[0].get("total_rows") if records else 0,
                        "values_masked": True,
                    }
                    for side, records in evidence.items()
                }
            row.update({"status": status, "evidence": output_evidence, "comparison": comparison})
        except Exception as exc:
            row.update({"status": "ERROR", "error": str(exc), "stack_trace": traceback.format_exc()})
            self._event("exception", "rule_failed", pair_id=pair.pair_id, rule_id=rule.rule_id, error=str(exc))
        self._event("info", "rule_finished", pair_id=pair.pair_id, rule_id=rule.rule_id, status=row["status"])
        return _jsonable(row)

    def _execute_row_reconciliation(
        self,
        pair: TablePair,
        rule: RuleSpec,
        filters: dict[str, list[dict[str, Any]]],
        table_dir: Path,
    ) -> dict[str, Any]:
        limit = int(rule.parameters["limit"])
        frames: dict[str, pd.DataFrame] = {}
        for side in ("source", "target"):
            compiler = SQLCompiler("databricks" if side == "source" else "bigquery")
            table = pair.source_name if side == "source" else pair.target_name
            keys = rule.parameters[f"{side}_keys"]
            values = rule.source_columns if side == "source" else rule.target_columns
            sql = compiler.row_values(table or "", keys, values, filters.get(side, []), limit)
            frames[side] = self._controlled_execute(
                side, sql, {table or ""}, table_dir,
                f"{rule.rule_id}_{side}", persist_evidence=False,
            )
        totals = {
            side: int(frame.iloc[0].get("total_rows") or 0) if not frame.empty else 0
            for side, frame in frames.items()
        }
        if any(total > limit for total in totals.values()):
            return {
                "status": "SKIP",
                "evidence": {"values_masked": True, "total_rows": totals},
                "comparison": {
                    "reason": "Filtered table exceeds bounded row reconciliation limit",
                    "limit": limit,
                },
            }
        maps: dict[str, dict[str, str]] = {}
        duplicate_hashes: dict[str, list[str]] = {}
        for side, frame in frames.items():
            key_columns = sorted(
                [name for name in frame.columns if name.startswith("key_")],
                key=lambda name: int(name.split("_")[1]),
            )
            value_columns = sorted(
                [name for name in frame.columns if name.startswith("value_")],
                key=lambda name: int(name.split("_")[1]),
            )
            row_map: dict[str, str] = {}
            duplicates: list[str] = []
            for record in frame.to_dict("records"):
                key_hash = self._masked_hash([record.get(name) for name in key_columns])
                row_hash = self._masked_hash([record.get(name) for name in value_columns])
                if key_hash in row_map:
                    duplicates.append(key_hash)
                row_map[key_hash] = row_hash
            maps[side] = row_map
            duplicate_hashes[side] = sorted(set(duplicates))[:50]
        source_keys, target_keys = set(maps["source"]), set(maps["target"])
        common = source_keys & target_keys
        missing = sorted(source_keys - target_keys)
        extra = sorted(target_keys - source_keys)
        changed = sorted(key for key in common if maps["source"][key] != maps["target"][key])
        passed = not missing and not extra and not changed and not any(duplicate_hashes.values())
        return {
            "status": "PASS" if passed else "FAIL",
            "evidence": {
                "values_masked": True, "total_rows": totals,
                "source_key_count": len(source_keys), "target_key_count": len(target_keys),
            },
            "comparison": {
                "missing_key_hashes": missing[:50], "extra_key_hashes": extra[:50],
                "changed_key_hashes": changed[:50], "duplicate_key_hashes": duplicate_hashes,
                "missing_count": len(missing), "extra_count": len(extra),
                "changed_count": len(changed), "limit": limit,
            },
        }

    @classmethod
    def _masked_hash(cls, values: list[Any]) -> str:
        canonical = [cls._canonical_value(value) for value in values]
        payload = json.dumps(canonical, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _canonical_value(value: Any) -> Any:
        if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, Decimal):
            normalized = value.normalize()
            return format(normalized, "f")
        if isinstance(value, float):
            return format(value, ".15g")
        if isinstance(value, (datetime, pd.Timestamp)):
            timestamp = pd.Timestamp(value)
            if timestamp.tzinfo is not None:
                timestamp = timestamp.tz_convert("UTC")
            return timestamp.isoformat()
        if isinstance(value, bytes):
            return value.hex()
        if hasattr(value, "item"):
            return DQWorkflow._canonical_value(value.item())
        return value

    def _controlled_execute(
        self,
        side: str,
        sql: str,
        allowed_tables: set[str],
        table_dir: Path,
        label: str,
        persist_evidence: bool = True,
    ) -> pd.DataFrame:
        connector = self._connectors()[side]
        validated = self.guard.validate(sql, connector.dialect, allowed_tables)
        sql_dir = table_dir / "sql"
        sql_dir.mkdir(parents=True, exist_ok=True)
        safe_label = re.sub(r"[^A-Za-z0-9_.-]", "_", label)
        (sql_dir / f"{safe_label}.sql").write_text(validated + "\n", encoding="utf-8")
        result = connector.execute(validated)
        if persist_evidence:
            evidence_dir = table_dir / "evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            result.frame.to_csv(evidence_dir / f"{safe_label}.csv", index=False)
        self._event(
            "info", "query_completed", side=side, label=label, query_id=result.query_id,
            bytes_processed=result.bytes_processed, rows=len(result.frame),
            evidence_persisted=persist_evidence,
        )
        return result.frame

    @staticmethod
    def _first_row(frame: pd.DataFrame) -> dict[str, Any]:
        if frame.empty:
            return {}
        return {key: _jsonable(value) for key, value in frame.iloc[0].to_dict().items()}

    def _compare(
        self, rule: RuleSpec, pair: TablePair, evidence: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        if rule.type in {"uniqueness", "domain", "predicate", "relationship"}:
            fields = {"uniqueness": "duplicate_groups", "relationship": "orphan_count"}
            field = fields.get(rule.type, "invalid_count")
            values = {side: float(data.get(field, 0) or 0) for side, data in evidence.items()}
            return ("PASS" if all(value == 0 for value in values.values()) else "FAIL"), {"violations": values}
        if rule.type == "key_values":
            limit = int(rule.parameters.get("limit", 10_000))
            totals = {
                side: int(records[0].get("total_rows", 0)) if records else 0
                for side, records in evidence.items()
            }
            if any(total > limit for total in totals.values()):
                return "SKIP", {
                    "reason": "Filtered table exceeds exact key comparison limit; use key bucket result",
                    "limit": limit, "total_rows": totals,
                }
            source = {str(row["key_hash"]) for row in evidence.get("source", [])}
            target = {str(row["key_hash"]) for row in evidence.get("target", [])}
            missing = sorted(source - target)
            extra = sorted(target - source)
            return ("PASS" if not missing and not extra else "FAIL"), {
                "source_key_count": len(source), "target_key_count": len(target),
                "missing_key_hashes": missing[:50], "extra_key_hashes": extra[:50],
                "values_masked": True,
            }
        if rule.type in {"value_distribution", "key_buckets"}:
            key_field = "value_hash" if rule.type == "value_distribution" else "key_bucket"
            count_field = "value_count" if rule.type == "value_distribution" else "row_count"
            source_rows = evidence.get("source", [])
            target_rows = evidence.get("target", [])
            if len(evidence) == 1 and rule.parameters.get("informational"):
                side = next(iter(evidence))
                return "PASS", {
                    "informational": True, "side": side,
                    "returned_values": len(evidence[side]), "values_masked": True,
                }
            source_map = {str(row[key_field]): int(row[count_field]) for row in source_rows}
            target_map = {str(row[key_field]): int(row[count_field]) for row in target_rows}
            keys = set(source_map) | set(target_map)
            mismatches = {
                key: {"source": source_map.get(key, 0), "target": target_map.get(key, 0)}
                for key in keys if source_map.get(key, 0) != target_map.get(key, 0)
            }
            return ("PASS" if not mismatches else "FAIL"), {
                "mismatch_count": len(mismatches),
                "mismatches": dict(list(sorted(mismatches.items()))[:50]),
            }
        if rule.type in {"freshness", "table_freshness"}:
            target = pd.to_datetime(evidence.get("target", {}).get("max_timestamp"), utc=True, errors="coerce")
            if pd.isna(target):
                return "FAIL", {"reason": "Target freshness timestamp is null"}
            source_value: pd.Timestamp | None = None
            reference = pd.Timestamp(datetime.now(timezone.utc))
            if pair.mode == "migration":
                source = pd.to_datetime(evidence.get("source", {}).get("max_timestamp"), utc=True, errors="coerce")
                if pd.isna(source):
                    return "FAIL", {"reason": "Source freshness timestamp is null"}
                source_value = source
                signed_lag = (target - source).total_seconds() / 60
                lagging_side = "target" if signed_lag < 0 else ("source" if signed_lag > 0 else "neither")
                reference = source
            else:
                signed_lag = (target - reference).total_seconds() / 60
                lagging_side = "target" if signed_lag < 0 else ("neither" if signed_lag == 0 else "source")
            lag = abs(signed_lag)
            allowed = float(rule.tolerance.get("minutes", 0))
            return ("PASS" if lag <= allowed else "FAIL"), {
                "source_timestamp": source_value.isoformat() if source_value is not None else None,
                "target_timestamp": target.isoformat(),
                "signed_lag_minutes": round(signed_lag, 3),
                "absolute_lag_minutes": round(lag, 3),
                "allowed_minutes": allowed, "lagging_side": lagging_side,
                "reference_timestamp": reference.isoformat(),
            }
        if rule.type == "column_profile":
            return "PASS", {"informational": True, "profile": evidence}
        if "expected_value" in rule.parameters:
            comparisons: dict[str, Any] = {}
            passed = True
            for side, data in evidence.items():
                actual = next(iter(data.values()), None)
                side_passed, detail = self._within_tolerance(
                    actual, rule.parameters["expected_value"], rule.tolerance
                )
                comparisons[side] = detail
                passed = passed and side_passed
            return ("PASS" if passed else "FAIL"), {"expected_comparisons": comparisons}
        if "source" in evidence and "target" in evidence:
            source_value = next(iter(evidence["source"].values()), None)
            target_value = next(iter(evidence["target"].values()), None)
            passed, detail = self._within_tolerance(source_value, target_value, rule.tolerance)
            return ("PASS" if passed else "FAIL"), detail
        side = next(iter(evidence), "target")
        side_data = evidence.get(side, {})
        if rule.type == "null_count":
            value = float(side_data.get("null_count", 0) or 0)
            allowed = float(rule.parameters.get("allowed", 0) or 0)
            return ("PASS" if value <= allowed else "FAIL"), {
                "side": side, "null_count": value, "allowed": allowed,
            }
        return "PASS", {"informational": True, "side": side}

    @staticmethod
    def _within_tolerance(source: Any, target: Any, tolerance: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        if source is None or target is None:
            return source is target, {"source": source, "target": target, "reason": "null comparison"}
        try:
            source_num, target_num = float(source), float(target)
            difference = abs(source_num - target_num)
            absolute = float(tolerance.get("absolute", 0))
            percentage = float(tolerance.get("percentage", 0))
            allowed = max(absolute, abs(source_num) * percentage)
            return difference <= allowed, {
                "source": source_num, "target": target_num,
                "difference": difference, "allowed_difference": allowed,
            }
        except (TypeError, ValueError):
            return source == target, {"source": source, "target": target, "exact": True}

    def _perform_rca(
        self,
        pair: TablePair,
        failures: list[dict[str, Any]],
        rules: list[RuleSpec],
        audit: dict[str, str | None],
        filters: dict[str, list[dict[str, Any]]],
        table_dir: Path,
    ) -> list[dict[str, Any]]:
        rules_by_id = {rule.rule_id: rule for rule in rules}
        output: list[dict[str, Any]] = []
        for failure in failures:
            rule = rules_by_id[failure["rule_id"]]
            diagnostics: list[dict[str, Any]] = []
            if rule.category in {"reconciliation", "freshness"} and audit.get("target"):
                diagnostic = RuleSpec(
                    rule_id=f"{rule.rule_id}__rca_date_coverage", pair_id=pair.pair_id,
                    type="date_coverage", scope="compare" if pair.mode == "migration" else "target",
                    category="rca", source_columns=[audit["source"]] if audit.get("source") else [],
                    target_columns=[audit["target"]], parameters={},
                )
                result = self._execute_rule(pair, diagnostic, {}, filters, table_dir)
                diagnostics.append(result)
            if rule.type == "uniqueness":
                side = "source" if rule.scope == "source" else "target"
                diagnostic = rule.model_copy(update={
                    "rule_id": f"{rule.rule_id}__rca_top_duplicates",
                    "type": "top_duplicates", "category": "rca", "parameters": {"limit": 200},
                })
                try:
                    dialect = "databricks" if side == "source" else "bigquery"
                    sql = SQLCompiler(dialect).compile_rule(diagnostic, pair, side, filters.get(side, []))
                    frame = self._controlled_execute(
                        side, sql, allowed_tables_for_rule(diagnostic, pair, side), table_dir,
                        f"{diagnostic.rule_id}_{side}",
                    )
                    diagnostics.append({"side": side, "sample": frame.head(20).to_dict("records")})
                except Exception as exc:
                    diagnostics.append({"error": str(exc)})
            if rule.type == "key_buckets":
                mismatches = list(failure.get("comparison", {}).get("mismatches", {}))
                selected_buckets = mismatches[:10]
                if selected_buckets:
                    key_sets: dict[str, set[str]] = {}
                    for side in ["source", "target"]:
                        try:
                            dialect = "databricks" if side == "source" else "bigquery"
                            qualified = pair.source_name if side == "source" else pair.target_name
                            columns = rule.source_columns if side == "source" else rule.target_columns
                            sql = SQLCompiler(dialect).key_hashes_for_buckets(
                                qualified or "", columns, filters.get(side, []), selected_buckets,
                                self.config.project.query_limits.evidence_rows,
                            )
                            frame = self._controlled_execute(
                                side, sql, {qualified} if qualified else set(), table_dir,
                                f"{rule.rule_id}__rca_mismatched_buckets_{side}",
                            )
                            key_sets[side] = set(frame.get("key_hash", pd.Series(dtype=str)).astype(str))
                        except Exception as exc:
                            diagnostics.append({"side": side, "error": str(exc)})
                    if "source" in key_sets and "target" in key_sets:
                        diagnostics.append({
                            "mismatched_buckets": selected_buckets,
                            "missing_key_hashes": sorted(key_sets["source"] - key_sets["target"])[:50],
                            "extra_key_hashes": sorted(key_sets["target"] - key_sets["source"])[:50],
                            "values_masked": True,
                        })
            conclusion = self._deterministic_rca(rule, failure, diagnostics)
            if self.config.llm.enabled:
                try:
                    adapter = self.llm
                    if adapter:
                        response = adapter.complete_structured(
                            "Explain the failed data-quality check using only the evidence.",
                            {"rule": rule.model_dump(), "failure": failure, "diagnostics": diagnostics},
                            RCAResponse,
                        )
                        conclusion = response.model_dump()
                except Exception as exc:
                    self._event("warning", "llm_rca_unavailable", pair_id=pair.pair_id, rule_id=rule.rule_id, error=str(exc))
            output.append({
                "pair_id": pair.pair_id, "rule_id": rule.rule_id,
                "conclusion": conclusion, "diagnostics": diagnostics,
            })
        self._tag_cascade_groups(output, failures)
        return _jsonable(output)

    @staticmethod
    def _deterministic_rca(
        rule: RuleSpec, failure: dict[str, Any], diagnostics: list[dict[str, Any]]
    ) -> dict[str, Any]:
        evidence = failure.get("evidence", {})
        if rule.type == "row_count":
            text = "Filtered source and target row counts differ. Review date coverage and applied filters."
        elif rule.type == "freshness":
            text = "The configured audit timestamps exceed the allowed freshness lag."
        elif rule.type == "uniqueness":
            text = "Duplicate groups exist for the configured or inferred key."
        elif rule.type == "relationship":
            text = "Target rows reference missing or inactive dimension records."
        elif rule.type in {"null_count", "distinct_count", "aggregate"}:
            text = "The mapped source and target metrics differ after configured filters and tolerances."
        else:
            text = "The rule failed; available evidence does not establish a unique root cause."
        return {
            "conclusion": text,
            "confidence": 0.7 if diagnostics else 0.45,
            "evidence_summary": [json.dumps(evidence, default=str), json.dumps(diagnostics, default=str)],
            "inconclusive": not bool(diagnostics),
        }

    @staticmethod
    def _tag_cascade_groups(
        rca_records: list[dict[str, Any]],
        failure_results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Tag each RCA record with root_cause_group and is_cascade.

        When multiple failures for the same table share a consistent directional
        delta (target has more rows than source, or vice versa), column-level
        signals like distinct_count and value_distribution are marked as
        cascading from that common root cause.
        """
        if len(rca_records) <= 1:
            return rca_records
        failure_by_rule = {r["rule_id"]: r for r in failure_results}
        direction_votes: dict[str, int] = {"target_extra": 0, "source_extra": 0}
        for rca_rec in rca_records:
            comp = failure_by_rule.get(rca_rec.get("rule_id", ""), {}).get("comparison", {})
            try:
                delta = float(comp["target"]) - float(comp["source"])
                if delta > 0:
                    direction_votes["target_extra"] += 1
                elif delta < 0:
                    direction_votes["source_extra"] += 1
            except (KeyError, TypeError, ValueError):
                pass
        dominant = max(direction_votes, key=direction_votes.get)
        if direction_votes[dominant] < 2:
            return rca_records
        # Types that directly identify row/key mismatches — these are root causes
        PRIMARY_TYPES = {"key_values", "key_buckets", "row_reconciliation", "row_count"}
        # Column-level types whose failures cascade from a row-count root cause
        CASCADE_TYPES = {"distinct_count", "value_distribution"}
        has_primary = any(
            failure_by_rule.get(r.get("rule_id", ""), {}).get("type") in PRIMARY_TYPES
            for r in rca_records
        )
        if not has_primary:
            return rca_records
        for rca_rec in rca_records:
            failure = failure_by_rule.get(rca_rec.get("rule_id", ""), {})
            comp = failure.get("comparison", {})
            is_cascade = False
            if failure.get("type") in CASCADE_TYPES:
                try:
                    delta = float(comp["target"]) - float(comp["source"])
                    if (dominant == "target_extra" and delta > 0) or (dominant == "source_extra" and delta < 0):
                        is_cascade = True
                except (KeyError, TypeError, ValueError):
                    pass
            rca_rec["root_cause_group"] = dominant
            rca_rec["is_cascade"] = is_cascade
        return rca_records
