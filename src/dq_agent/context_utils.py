from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

import pandas as pd
import yaml
from openpyxl import load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from .config import AppConfig
from .reporting import write_json


APPROVAL_COLUMNS = [
    "proposal_id", "item_type", "table_name", "column_name", "action",
    "current_value", "proposed_value", "evidence", "confidence", "explanation",
    "decision", "override_value", "reviewer_comments", "reviewed_by", "reviewed_at",
    "source_file", "content_hash",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_id(*parts: Any, length: int = 32) -> str:
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def workflow_paths(config: AppConfig, run_id: str, workflow: str) -> dict[str, Path]:
    approval_root = config.path(config.project.approvals.root)
    paths = {
        "approval_root": approval_root,
        "pending": approval_root / "pending",
        "processed": approval_root / "processed",
        "approved": approval_root / "approved",
        "rejected": approval_root / "rejected",
        "errors": approval_root / "errors",
        "sample": approval_root / "sample",
        "output": config.path(config.project.outputs_dir) / workflow / run_id,
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    paths["log"] = paths["output"] / "workflow.log"
    paths["checkpoint"] = paths["output"] / "checkpoint.json"
    return paths


def context_workflow_paths(config: AppConfig, run_id: str) -> dict[str, Path]:
    return workflow_paths(config, run_id, "context")


def configure_workflow_logging(log_path: Path, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(f"dq_agent.workflow.{log_path.parent.parent.name}.{log_path.parent.name}")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


@contextmanager
def logged_step(
    logger: logging.Logger,
    checkpoint_path: Path,
    step: str,
    **details: Any,
) -> Iterator[None]:
    started = perf_counter()
    logger.info("%s STARTED | %s", step, details)
    try:
        yield
    except Exception as exc:
        checkpoint = _read_json(checkpoint_path)
        checkpoint.update({
            "failed_step": step,
            "failed_at": utc_now(),
            "error": str(exc),
            "stack_trace": traceback.format_exc(),
        })
        write_json(checkpoint_path, checkpoint)
        logger.exception("%s FAILED | %s", step, details)
        raise
    checkpoint = _read_json(checkpoint_path)
    checkpoint.update({
        "last_completed_step": step,
        "completed_at": utc_now(),
        "failed_step": None,
        "error": None,
        "stack_trace": None,
    })
    write_json(checkpoint_path, checkpoint)
    logger.info("%s COMPLETED | elapsed_seconds=%.3f | %s", step, perf_counter() - started, details)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "enabled"}


def _read_table(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    frame = pd.read_excel(path, dtype=object) if path.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(path, dtype=object)
    return [{key: _clean(value) for key, value in row.items()} for row in frame.to_dict("records")]


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _qualified_target(row: dict[str, Any]) -> str | None:
    parts = [row.get("target_project"), row.get("target_dataset"), row.get("target_table")]
    return ".".join(str(part) for part in parts) if all(parts) else None


def _qualified_source(row: dict[str, Any]) -> str | None:
    parts = [row.get("source_catalog"), row.get("source_schema"), row.get("source_table")]
    return ".".join(str(part) for part in parts) if all(parts) else None


def _normalized_record(
    config: AppConfig,
    run_id: str,
    context_type: str,
    subject_key: str,
    payload: dict[str, Any],
    source_file: str,
    publish: bool,
    pair_id: str | None = None,
    context_id: str | None = None,
    target_table: str | None = None,
    source_table: str | None = None,
    column_name: str | None = None,
    confidence: float = 1.0,
    origin: str = "USER_CONFIG",
) -> dict[str, Any]:
    namespace = config.project.context_store.namespace
    content_hash = payload_hash(payload)
    context_key = stable_id(
        namespace["organization"], namespace["environment"], namespace["domain"],
        context_type, subject_key,
    )
    return {
        **namespace,
        "context_key": context_key,
        "context_type": context_type,
        "subject_key": subject_key,
        "pair_id": pair_id,
        "context_id": context_id,
        "target_table": target_table,
        "source_table": source_table,
        "column_name": column_name,
        "payload": payload,
        "payload_json": canonical_json(payload),
        "content_hash": content_hash,
        "publish_to_context": publish,
        "origin": origin,
        "confidence": float(confidence),
        "source_file": source_file,
        "run_id": run_id,
        "provenance_json": canonical_json({"source_file": source_file, "run_id": run_id}),
    }


def make_context_record(
    config: AppConfig,
    run_id: str,
    context_type: str,
    subject_key: str,
    payload: dict[str, Any],
    source_file: str,
    publish: bool,
    **identity: Any,
) -> dict[str, Any]:
    """Create a context record compatible with the shared approval workflow."""
    return _normalized_record(
        config, run_id, context_type, subject_key, payload, source_file, publish, **identity
    )


def normalize_input_context(config: AppConfig, run_id: str, logger: logging.Logger | None = None) -> pd.DataFrame:
    table_path = config.path(config.project.table_mappings)
    column_path = config.path(config.project.column_mappings)
    business_path = config.path(config.project.business_context)
    tests_path = config.path(config.project.human_tests)
    measures_path = config.path(config.project.measures)
    tables = _read_table(table_path)
    columns = _read_table(column_path) if column_path.exists() else []
    business = _read_yaml(business_path)
    tests = _read_yaml(tests_path)
    measures = _read_yaml(measures_path) if measures_path.exists() else {"measures": []}
    if logger:
        for path in (table_path, column_path, business_path, tests_path, measures_path):
            logger.info("NORMALIZE_CONTEXT reading file=%s", path)

    table_by_pair = {str(row["pair_id"]): row for row in tables if row.get("pair_id")}
    records: list[dict[str, Any]] = []
    for row in tables:
        pair_id = str(row.get("pair_id") or "").strip()
        if not pair_id:
            continue
        publish = _as_bool(row.pop("publish_to_context", False))
        row["enabled"] = _as_bool(row.get("enabled", True))
        records.append(_normalized_record(
            config, run_id, "table_mapping", pair_id, row,
            str(table_path), publish, pair_id=pair_id,
            context_id=str(row.get("context_id") or pair_id),
            target_table=_qualified_target(row), source_table=_qualified_source(row),
        ))

    for row in columns:
        pair_id = str(row.get("pair_id") or "").strip()
        source_column = str(row.get("source_column") or "").strip()
        if not pair_id or not source_column:
            continue
        publish = _as_bool(row.pop("publish_to_context", False))
        pair = table_by_pair.get(pair_id, {})
        records.append(_normalized_record(
            config, run_id, "column_mapping", f"{pair_id}:{source_column.lower()}", row,
            str(column_path), publish, pair_id=pair_id,
            context_id=str(pair.get("context_id") or pair_id), target_table=_qualified_target(pair),
            source_table=_qualified_source(pair), column_name=source_column,
        ))

    for context_id, raw in (business.get("tables") or {}).items():
        payload = dict(raw or {})
        publish = _as_bool(payload.pop("publish_to_context", False))
        pair = next(
            (row for row in tables if str(row.get("context_id") or row.get("pair_id")) == str(context_id)),
            {},
        )
        records.append(_normalized_record(
            config, run_id, "business_context", f"{context_id}:table", payload,
            str(business_path), publish, pair_id=str(pair.get("pair_id") or context_id),
            context_id=str(context_id), target_table=_qualified_target(pair), source_table=_qualified_source(pair),
        ))

    for raw in tests.get("tests") or []:
        payload = dict(raw or {})
        test_id = str(payload.get("test_id") or "").strip()
        pair_id = str(payload.get("pair_id") or "").strip()
        if not test_id or not pair_id:
            continue
        publish = _as_bool(payload.pop("publish_to_context", False))
        pair = table_by_pair.get(pair_id, {})
        records.append(_normalized_record(
            config, run_id, "human_test", test_id, payload,
            str(tests_path), publish, pair_id=pair_id,
            context_id=str(pair.get("context_id") or pair_id), target_table=_qualified_target(pair),
            source_table=_qualified_source(pair),
        ))

    for raw in measures.get("measures") or []:
        payload = dict(raw or {})
        measure_id = str(payload.get("measure_id") or "").strip()
        pair_id = str(payload.get("pair_id") or "").strip()
        if not measure_id or not pair_id:
            continue
        publish = _as_bool(payload.pop("publish_to_context", False))
        pair = table_by_pair.get(pair_id, {})
        context_type = "kpi" if str(payload.get("definition_type", "measure")).lower() == "kpi" else "measure"
        records.append(_normalized_record(
            config, run_id, context_type, f"{pair_id}:{measure_id}", payload,
            str(measures_path), publish, pair_id=pair_id,
            context_id=str(pair.get("context_id") or pair_id),
            target_table=str(payload.get("target_table") or _qualified_target(pair) or "") or None,
            source_table=str(payload.get("source_table") or _qualified_source(pair) or "") or None,
            column_name=str(payload.get("target_expression") or "") or None,
            origin="USER_CONFIG",
        ))
    return pd.DataFrame(records)


def validate_normalized_context(records: pd.DataFrame) -> None:
    if records.empty:
        return
    required = {"context_key", "context_type", "subject_key", "content_hash", "payload"}
    missing = required - set(records.columns)
    if missing:
        raise ValueError(f"Normalized context is missing columns: {sorted(missing)}")
    conflicts = records.groupby("context_key")["content_hash"].nunique()
    conflicting_keys = conflicts[conflicts > 1].index.tolist()
    if conflicting_keys:
        raise ValueError(f"Input contains conflicting definitions for context keys: {conflicting_keys}")


def build_context_proposals(records: pd.DataFrame, trusted: pd.DataFrame, run_id: str) -> pd.DataFrame:
    trusted_by_key = {
        str(row["context_key"]): row
        for row in trusted.to_dict("records")
        if row.get("context_key")
    }
    proposals: list[dict[str, Any]] = []
    for row in records.to_dict("records"):
        current = trusted_by_key.get(str(row["context_key"]))
        if not row.get("publish_to_context"):
            continue
        if current and str(current.get("content_hash")) == str(row["content_hash"]):
            continue
        action = "UPDATE" if current else "CREATE"
        proposal_id = stable_id(row["context_key"], row["content_hash"], action)
        proposals.append({
            "proposal_id": proposal_id,
            "context_key": row["context_key"],
            "context_type": row["context_type"],
            "subject_key": row["subject_key"],
            "organization": row["organization"],
            "environment": row["environment"],
            "domain": row["domain"],
            "pair_id": row.get("pair_id"),
            "context_id": row.get("context_id"),
            "target_table": row.get("target_table"),
            "source_table": row.get("source_table"),
            "column_name": row.get("column_name"),
            "action": action,
            "base_record_id": current.get("record_id") if current else None,
            "base_version": int(current.get("version") or 0) if current else 0,
            "current_payload_json": current.get("payload_json") if current else None,
            "proposed_payload_json": row["payload_json"],
            "content_hash": row["content_hash"],
            "origin": row.get("origin", "USER_CONFIG"),
            "confidence": float(row.get("confidence", 1.0)),
            "evidence_json": canonical_json({"source_file": row.get("source_file")}),
            "explanation": f"{action.title()} {row['context_type']} from explicit input configuration",
            "source_file": row.get("source_file"),
            "run_id": run_id,
            "status": "PENDING",
            "created_at": utc_now(),
        })
    return pd.DataFrame(proposals)


def deep_merge(trusted: Any, current: Any) -> Any:
    if not isinstance(trusted, dict) or not isinstance(current, dict):
        return current
    merged = dict(trusted)
    for key, value in current.items():
        merged[key] = deep_merge(merged[key], value) if key in merged else value
    return merged


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None or value == "":
        return {}
    return json.loads(str(value))


def build_effective_inputs(records: pd.DataFrame, trusted: pd.DataFrame) -> dict[str, Any]:
    local = records.to_dict("records")
    current_pairs = {row["pair_id"]: row["payload"] for row in local if row["context_type"] == "table_mapping"}
    active_pairs = {pair_id for pair_id, row in current_pairs.items() if row.get("enabled", True)}
    pair_context = {
        pair_id: str(row.get("context_id") or pair_id)
        for pair_id, row in current_pairs.items()
    }

    mappings: dict[tuple[str, str], dict[str, Any]] = {}
    tests: dict[str, dict[str, Any]] = {}
    contexts: dict[str, dict[str, Any]] = {}
    trusted_relationships: dict[str, list[dict[str, Any]]] = {}
    trusted_measures: dict[str, list[dict[str, Any]]] = {}
    trusted_rca: dict[str, list[dict[str, Any]]] = {}
    trusted_rows = trusted.to_dict("records") if not trusted.empty else []
    trusted_rows.sort(
        key=lambda row: (
            str(row.get("context_id") or ""),
            0 if str(row.get("subject_key") or "").endswith(":table") else 1,
            str(row.get("created_at") or ""),
        )
    )
    for row in trusted_rows:
        payload = _payload(row.get("payload") or row.get("payload_json"))
        pair_id = str(row.get("pair_id") or payload.get("pair_id") or "")
        context_id = str(row.get("context_id") or pair_context.get(pair_id, pair_id))
        if pair_id and pair_id not in active_pairs:
            continue
        if row.get("context_type") == "column_mapping":
            mappings[(pair_id, str(payload.get("source_column", "")).lower())] = payload
        elif row.get("context_type") == "human_test":
            tests[str(payload.get("test_id") or row.get("subject_key"))] = payload
        elif row.get("context_type") == "business_context" and context_id:
            contexts[context_id] = deep_merge(contexts.get(context_id, {}), payload)
        elif row.get("context_type") == "relationship" and context_id:
            trusted_relationships.setdefault(context_id, []).append(payload)
        elif row.get("context_type") in {"measure", "kpi"} and context_id:
            trusted_measures.setdefault(context_id, []).append({**payload, "origin": "trusted"})
        elif row.get("context_type") == "rca_learning" and context_id:
            trusted_rca.setdefault(context_id, []).append(payload)

    for row in local:
        payload = row["payload"]
        pair_id = str(row.get("pair_id") or payload.get("pair_id") or "")
        context_id = str(row.get("context_id") or pair_context.get(pair_id, pair_id))
        if row["context_type"] == "column_mapping" and pair_id in active_pairs:
            mappings[(pair_id, str(payload.get("source_column", "")).lower())] = payload
        elif row["context_type"] == "human_test" and pair_id in active_pairs:
            tests[str(payload.get("test_id") or row["subject_key"])] = payload
        elif row["context_type"] == "business_context" and context_id in set(pair_context.values()):
            contexts[context_id] = deep_merge(contexts.get(context_id, {}), payload)

    for context_id, relationships in trusted_relationships.items():
        context = contexts.setdefault(context_id, {})
        current_relationships = list(context.get("relationships", []))
        current_keys = {
            tuple(item.get("child_columns", item.get("target_columns", [])))
            for item in current_relationships
        }
        for relationship in relationships:
            key = tuple(relationship.get("child_columns", relationship.get("target_columns", [])))
            if key and key not in current_keys and relationship.get("enabled", True):
                current_relationships.append(relationship)
                current_keys.add(key)
        if current_relationships:
            context["relationships"] = current_relationships

    for context_id, measures in trusted_measures.items():
        context = contexts.setdefault(context_id, {})
        current_measures = list(context.get("measures", []))
        current_ids = {
            str(item.get("measure_id") or item.get("target_column") or item.get("target_expression"))
            for item in current_measures
        }
        for measure in measures:
            measure_id = str(measure.get("measure_id") or measure.get("target_expression") or "")
            if measure_id and measure_id not in current_ids:
                current_measures.append(measure)
                current_ids.add(measure_id)
        if current_measures:
            context["measures"] = current_measures

    for context_id, learnings in trusted_rca.items():
        if learnings:
            contexts.setdefault(context_id, {})["rca_learnings"] = learnings

    return {
        "table_mappings": [row for row in current_pairs.values() if row.get("enabled", True)],
        "column_mappings": list(mappings.values()),
        "business_context": {"tables": contexts},
        "human_tests": {"tests": [row for row in tests.values() if row.get("enabled", True)]},
    }


def _known_columns(payload: dict[str, Any]) -> set[str]:
    columns: set[str] = set()
    for key, value in payload.items():
        if key.endswith("column") and isinstance(value, str):
            columns.add(value.lower())
        elif key.endswith("columns") and isinstance(value, list):
            columns.update(str(item).lower() for item in value)
        elif isinstance(value, dict):
            columns.update(_known_columns(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    columns.update(_known_columns(item))
    return columns


def _tokens(value: Any) -> set[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(value or ""))
    return {item.lower() for item in re.split(r"[^A-Za-z0-9]+", text) if item}


def rank_relevant_context(current: pd.DataFrame, trusted: pd.DataFrame, threshold: float = 0.65, limit: int = 5) -> pd.DataFrame:
    results: list[dict[str, Any]] = []
    if current.empty or trusted.empty:
        return pd.DataFrame(results)
    for local in current.to_dict("records"):
        local_payload = _payload(local.get("payload") or local.get("payload_json"))
        local_columns = _known_columns(local_payload)
        for candidate in trusted.to_dict("records"):
            if candidate.get("context_type") != local.get("context_type"):
                continue
            candidate_payload = _payload(candidate.get("payload") or candidate.get("payload_json"))
            exact = candidate.get("context_key") == local.get("context_key")
            same_table = bool(local.get("target_table") and local.get("target_table") == candidate.get("target_table"))
            if exact or same_table:
                score = 1.0
            else:
                local_name = str(local.get("target_table") or local.get("subject_key") or "").split(".")[-1]
                candidate_name = str(candidate.get("target_table") or candidate.get("subject_key") or "").split(".")[-1]
                name_score = 0.45 * SequenceMatcher(None, local_name.lower(), candidate_name.lower()).ratio()
                candidate_columns = _known_columns(candidate_payload)
                column_score = 0.25 * len(local_columns & candidate_columns) / max(1, len(local_columns | candidate_columns))
                description_score = 0.15 * len(_tokens(local_payload.get("description")) & _tokens(candidate_payload.get("description"))) / max(
                    1, len(_tokens(local_payload.get("description")) | _tokens(candidate_payload.get("description")))
                )
                score = 0.15 + name_score + column_score + description_score
            if score >= threshold:
                results.append({
                    "local_context_key": local.get("context_key"),
                    "candidate_record_id": candidate.get("record_id"),
                    "context_type": local.get("context_type"),
                    "local_subject": local.get("subject_key"),
                    "candidate_subject": candidate.get("subject_key"),
                    "score": round(min(score, 1.0), 4),
                    "automatic": bool(exact or same_table),
                })
    frame = pd.DataFrame(results)
    if frame.empty:
        return frame
    return frame.sort_values(["local_context_key", "score"], ascending=[True, False]).groupby("local_context_key").head(limit).reset_index(drop=True)


def write_approval_workbook(path: Path, proposals: pd.DataFrame) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for proposal in proposals.to_dict("records"):
        rows.append({
            "proposal_id": proposal.get("proposal_id"),
            "item_type": proposal.get("context_type"),
            "table_name": proposal.get("target_table"),
            "column_name": proposal.get("column_name"),
            "action": proposal.get("action"),
            "current_value": proposal.get("current_payload_json"),
            "proposed_value": proposal.get("proposed_payload_json"),
            "evidence": proposal.get("evidence_json"),
            "confidence": proposal.get("confidence"),
            "explanation": proposal.get("explanation"),
            "decision": "PENDING",
            "override_value": "",
            "reviewer_comments": "",
            "reviewed_by": "",
            "reviewed_at": "",
            "source_file": proposal.get("source_file"),
            "content_hash": proposal.get("content_hash"),
        })
    pd.DataFrame(rows, columns=APPROVAL_COLUMNS).to_excel(path, index=False, sheet_name="Context Approvals")
    workbook = load_workbook(path)
    sheet = workbook["Context Approvals"]
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    header_fill = PatternFill("solid", fgColor="1F4E78")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    widths = {
        "A": 24, "B": 20, "C": 34, "D": 22, "E": 12, "F": 40, "G": 50,
        "H": 42, "I": 12, "J": 42, "K": 15, "L": 50, "M": 32, "N": 20,
        "O": 22, "P": 36, "Q": 32,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    validation = DataValidation(type="list", formula1='"PENDING,APPROVE,REJECT,OVERRIDE"', allow_blank=False)
    sheet.add_data_validation(validation)
    validation.add(f"K2:K{max(sheet.max_row, 2)}")
    sheet.conditional_formatting.add(
        f"K2:K{max(sheet.max_row, 2)}",
        FormulaRule(formula=["$K2=\"REJECT\""], fill=PatternFill("solid", fgColor="F4CCCC")),
    )
    workbook.save(path)
    return path


def read_approval_workbook(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Approval workbook not found: {path}")
    frame = pd.read_excel(path, dtype=object)
    frame = frame.where(pd.notna(frame), "")
    missing = set(APPROVAL_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Approval workbook is missing columns: {sorted(missing)}")
    frame["decision"] = frame["decision"].astype(str).str.strip().str.upper()
    invalid = sorted(set(frame["decision"]) - {"PENDING", "APPROVE", "REJECT", "OVERRIDE"})
    if invalid:
        raise ValueError(f"Approval workbook contains invalid decisions: {invalid}")
    return frame


def archive_approval_workbook(source: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    if destination.exists():
        destination = destination_dir / f"{source.stem}_{stable_id(utc_now(), length=8)}{source.suffix}"
    return Path(shutil.move(str(source), str(destination)))


def workflow_reviews_to_records(
    reviews: list[dict[str, Any]],
    effective_inputs: dict[str, Any],
    config: AppConfig,
    run_id: str,
) -> pd.DataFrame:
    contexts = effective_inputs.get("business_context", {}).get("tables", {})
    pair_context = {
        str(row["pair_id"]): str(row.get("context_id") or row["pair_id"])
        for row in effective_inputs.get("table_mappings", [])
    }
    pair_target = {
        str(row["pair_id"]): _qualified_target(row)
        for row in effective_inputs.get("table_mappings", [])
    }
    records: list[dict[str, Any]] = []
    for review in reviews:
        pair_id = str(review.get("pair_id") or "")
        category = str(review.get("category") or "")
        proposal = _payload(review.get("proposal"))
        confidence = float(review.get("confidence") or 0)
        context_id = pair_context.get(pair_id, pair_id)
        if category == "column_mapping":
            source_column = str(proposal.get("source_column") or review.get("subject") or "")
            payload = {
                "pair_id": pair_id,
                "source_column": source_column,
                "target_column": proposal.get("target_column"),
                "status": "approved",
                "comments": "Approved agent inference",
            }
            subject = f"{pair_id}:{source_column.lower()}"
            context_type = "column_mapping"
            column_name = source_column
        elif category == "measure":
            payload = {
                key: value for key, value in proposal.items()
                if key not in {"evidence", "metadata_errors", "approval_status"}
            }
            measure_id = str(payload.get("measure_id") or review.get("subject") or "")
            subject = f"{pair_id}:{measure_id}"
            context_type = "kpi" if str(payload.get("definition_type", "measure")).lower() == "kpi" else "measure"
            column_name = str(payload.get("target_expression") or "") or None
        elif category == "relationship":
            payload = proposal
            subject = (
                f"{pair_id}:{','.join(payload.get('child_columns', []))}:"
                f"{payload.get('parent_table')}:{','.join(payload.get('parent_columns', []))}"
            )
            context_type = "relationship"
            column_name = ",".join(payload.get("child_columns", [])) or None
        elif category == "rca_learning":
            payload = proposal
            subject = f"{pair_id}:{payload.get('measure_id')}:{payload.get('rule_id', payload.get('classification'))}"
            context_type = "rca_learning"
            column_name = None
        else:
            patches = {
                "primary_key": {"primary_key": proposal},
                "audit_column": {"audit_columns": proposal},
                "scd2_current_filter": {"scd2": {"enabled": True, "current_filter": proposal}},
            }
            if category not in patches:
                continue
            payload = patches[category]
            subject = f"{context_id}:{category}"
            context_type = "business_context"
            column_name = None
        record = _normalized_record(
            config, run_id, context_type, subject, payload,
            f"workflow:{run_id}", True, pair_id=pair_id, context_id=context_id,
            target_table=pair_target.get(pair_id), column_name=column_name,
            confidence=confidence, origin="AGENT_INFERENCE",
        )
        record["workflow_evidence"] = review.get("evidence")
        records.append(record)
    return pd.DataFrame(records)
