from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from .config import AppConfig, ContextStoreConfig
from .context_utils import (
    archive_approval_workbook,
    canonical_json,
    payload_hash,
    read_approval_workbook,
    stable_id,
    utc_now,
)
from .reporting import write_json


TABLE_DDL = {
    "context_records": """
        record_id STRING NOT NULL,
        context_key STRING NOT NULL,
        context_type STRING NOT NULL,
        subject_key STRING NOT NULL,
        organization STRING NOT NULL,
        environment STRING NOT NULL,
        domain STRING NOT NULL,
        pair_id STRING,
        context_id STRING,
        target_table STRING,
        source_table STRING,
        column_name STRING,
        payload_json STRING NOT NULL,
        content_hash STRING NOT NULL,
        version INT64 NOT NULL,
        status STRING NOT NULL,
        origin STRING NOT NULL,
        confidence FLOAT64,
        source_file STRING,
        run_id STRING,
        proposal_id STRING,
        approved_by STRING,
        approved_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL,
        provenance_json STRING
    """,
    "context_proposals": """
        proposal_id STRING NOT NULL,
        context_key STRING NOT NULL,
        context_type STRING NOT NULL,
        subject_key STRING NOT NULL,
        organization STRING NOT NULL,
        environment STRING NOT NULL,
        domain STRING NOT NULL,
        pair_id STRING,
        context_id STRING,
        target_table STRING,
        source_table STRING,
        column_name STRING,
        action STRING NOT NULL,
        base_record_id STRING,
        base_version INT64,
        current_payload_json STRING,
        proposed_payload_json STRING NOT NULL,
        content_hash STRING NOT NULL,
        origin STRING NOT NULL,
        confidence FLOAT64,
        evidence_json STRING,
        explanation STRING,
        source_file STRING,
        run_id STRING,
        status STRING NOT NULL,
        created_at TIMESTAMP NOT NULL,
        processed_at TIMESTAMP
    """,
    "approval_decisions": """
        decision_id STRING NOT NULL,
        proposal_id STRING NOT NULL,
        decision STRING NOT NULL,
        override_payload_json STRING,
        reviewer_comments STRING,
        reviewed_by STRING NOT NULL,
        reviewed_at TIMESTAMP NOT NULL,
        imported_at TIMESTAMP NOT NULL,
        resulting_record_id STRING,
        resulting_version INT64,
        content_hash STRING NOT NULL,
        approval_file STRING,
        organization STRING NOT NULL,
        environment STRING NOT NULL,
        domain STRING NOT NULL
    """,
}


def _client(store: ContextStoreConfig) -> Any:
    try:
        from google.cloud import bigquery
    except ImportError as exc:
        raise RuntimeError("Install google-cloud-bigquery to use the context store") from exc
    return bigquery.Client(project=store.project, location=store.location)


def _table(store: ContextStoreConfig, name: str) -> str:
    return f"{store.project}.{store.dataset}.{name}"


def ensure_context_objects(config: AppConfig, logger: logging.Logger | None = None) -> dict[str, str]:
    store = config.project.context_store
    if not store.enabled:
        raise RuntimeError("Context storage is disabled in config/project.yaml")
    from google.cloud import bigquery

    client = _client(store)
    dataset_id = f"{store.project}.{store.dataset}"
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = store.location
    client.create_dataset(dataset, exists_ok=True)
    if logger:
        logger.info("LOAD_CONFIGURATION BigQuery dataset=%s location=%s", dataset_id, store.location)
    for name, columns in TABLE_DDL.items():
        sql = f"CREATE TABLE IF NOT EXISTS `{_table(store, name)}` ({columns})"
        client.query(sql).result()
        if logger:
            logger.info("WRITE_CONTEXT_STAGING ensured BigQuery table=%s", _table(store, name))
    current_view = f"""
        CREATE OR REPLACE VIEW `{_table(store, 'trusted_context_current')}` AS
        SELECT * EXCEPT(row_number)
        FROM (
          SELECT r.*, ROW_NUMBER() OVER (
            PARTITION BY organization, environment, domain, context_key
            ORDER BY version DESC, created_at DESC
          ) AS row_number
          FROM `{_table(store, 'context_records')}` r
        )
        WHERE row_number = 1 AND status = 'TRUSTED'
    """
    pending_view = f"""
        CREATE OR REPLACE VIEW `{_table(store, 'pending_context_proposals')}` AS
        SELECT * FROM `{_table(store, 'context_proposals')}` WHERE status = 'PENDING'
    """
    client.query(current_view).result()
    client.query(pending_view).result()
    if logger:
        logger.info("WRITE_CONTEXT_STAGING ensured BigQuery views dataset=%s", dataset_id)
    return {
        "dataset": dataset_id,
        "context_records": _table(store, "context_records"),
        "context_proposals": _table(store, "context_proposals"),
        "approval_decisions": _table(store, "approval_decisions"),
        "trusted_context_current": _table(store, "trusted_context_current"),
        "pending_context_proposals": _table(store, "pending_context_proposals"),
    }


def _namespace_query(config: AppConfig, source: str) -> tuple[str, list[Any]]:
    from google.cloud import bigquery

    store = config.project.context_store
    sql = f"""
        SELECT * FROM `{_table(store, source)}`
        WHERE organization = @organization
          AND environment = @environment
          AND domain = @domain
    """
    parameters = [
        bigquery.ScalarQueryParameter("organization", "STRING", store.organization),
        bigquery.ScalarQueryParameter("environment", "STRING", store.environment),
        bigquery.ScalarQueryParameter("domain", "STRING", store.domain),
    ]
    return sql, parameters


def read_context(config: AppConfig, source: str = "trusted_context_current", logger: logging.Logger | None = None) -> pd.DataFrame:
    if not config.project.context_store.enabled:
        return pd.DataFrame()
    allowed_sources = {
        "context_records", "context_proposals", "approval_decisions",
        "trusted_context_current", "pending_context_proposals",
    }
    if source not in allowed_sources:
        raise ValueError(f"Unsupported context source: {source}")
    from google.cloud import bigquery

    client = _client(config.project.context_store)
    sql, parameters = _namespace_query(config, source)
    job = client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=parameters))
    rows = [dict(row.items()) for row in job.result()]
    if logger:
        logger.info("RETRIEVE_RELEVANT_CONTEXT table=%s records_read=%s", _table(config.project.context_store, source), len(rows))
    return pd.DataFrame(rows)


def write_context_proposals(config: AppConfig, proposals: pd.DataFrame, logger: logging.Logger | None = None) -> dict[str, int]:
    if proposals.empty:
        return {"accepted": 0, "existing": 0, "rejected": 0}
    store = config.project.context_store
    client = _client(store)
    existing = read_context(config, "context_proposals")
    existing_ids = set(existing.get("proposal_id", pd.Series(dtype=str)).astype(str))
    rows = [
        {key: _json_value(value) for key, value in row.items()}
        for row in proposals.to_dict("records")
        if str(row.get("proposal_id")) not in existing_ids
    ]
    errors = client.insert_rows_json(_table(store, "context_proposals"), rows) if rows else []
    if errors:
        raise RuntimeError(f"BigQuery rejected context proposals: {errors}")
    counts = {"accepted": len(rows), "existing": len(proposals) - len(rows), "rejected": 0}
    if logger:
        logger.info("WRITE_CONTEXT_STAGING table=%s counts=%s", _table(store, "context_proposals"), counts)
    return counts


def _json_value(value: Any) -> Any:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return canonical_json(value)
    if hasattr(value, "item"):
        return value.item()
    return value


def _proposal_by_id(config: AppConfig, proposal_id: str) -> dict[str, Any] | None:
    from google.cloud import bigquery

    store = config.project.context_store
    sql = f"""
        SELECT * FROM `{_table(store, 'context_proposals')}`
        WHERE proposal_id = @proposal_id
          AND organization = @organization
          AND environment = @environment
          AND domain = @domain
        ORDER BY created_at DESC LIMIT 1
    """
    params = [
        bigquery.ScalarQueryParameter("proposal_id", "STRING", proposal_id),
        bigquery.ScalarQueryParameter("organization", "STRING", store.organization),
        bigquery.ScalarQueryParameter("environment", "STRING", store.environment),
        bigquery.ScalarQueryParameter("domain", "STRING", store.domain),
    ]
    rows = list(_client(store).query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result())
    return dict(rows[0].items()) if rows else None


def _existing_decision(config: AppConfig, proposal_id: str) -> dict[str, Any] | None:
    from google.cloud import bigquery

    store = config.project.context_store
    sql = f"""
        SELECT * FROM `{_table(store, 'approval_decisions')}`
        WHERE proposal_id = @proposal_id ORDER BY imported_at DESC LIMIT 1
    """
    params = [bigquery.ScalarQueryParameter("proposal_id", "STRING", proposal_id)]
    rows = list(_client(store).query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result())
    return dict(rows[0].items()) if rows else None


def _current_record(config: AppConfig, context_key: str) -> dict[str, Any] | None:
    from google.cloud import bigquery

    store = config.project.context_store
    sql = f"""
        SELECT * FROM `{_table(store, 'trusted_context_current')}`
        WHERE context_key = @context_key
          AND organization = @organization
          AND environment = @environment
          AND domain = @domain
        LIMIT 1
    """
    params = [
        bigquery.ScalarQueryParameter("context_key", "STRING", context_key),
        bigquery.ScalarQueryParameter("organization", "STRING", store.organization),
        bigquery.ScalarQueryParameter("environment", "STRING", store.environment),
        bigquery.ScalarQueryParameter("domain", "STRING", store.domain),
    ]
    rows = list(_client(store).query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result())
    return dict(rows[0].items()) if rows else None


def _update_proposal_status(config: AppConfig, proposal_id: str, status: str) -> None:
    from google.cloud import bigquery

    store = config.project.context_store
    sql = f"""
        UPDATE `{_table(store, 'context_proposals')}`
        SET status = @status, processed_at = CURRENT_TIMESTAMP()
        WHERE proposal_id = @proposal_id
          AND organization = @organization
          AND environment = @environment
          AND domain = @domain
    """
    params = [
        bigquery.ScalarQueryParameter("status", "STRING", status),
        bigquery.ScalarQueryParameter("proposal_id", "STRING", proposal_id),
        bigquery.ScalarQueryParameter("organization", "STRING", store.organization),
        bigquery.ScalarQueryParameter("environment", "STRING", store.environment),
        bigquery.ScalarQueryParameter("domain", "STRING", store.domain),
    ]
    _client(store).query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()


def import_approval_workbook(
    config: AppConfig,
    workbook_path: Path,
    approval_paths: dict[str, Path],
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    frame = read_approval_workbook(workbook_path)
    if frame.empty:
        raise ValueError("Approval workbook has no decisions")
    pending = frame[frame["decision"] == "PENDING"]
    if not pending.empty:
        raise ValueError(f"Resolve all rows before import; {len(pending)} rows are still PENDING")
    summary = {"approved": 0, "overridden": 0, "rejected": 0, "existing": 0}
    receipts: list[dict[str, Any]] = []
    try:
        for row in frame.to_dict("records"):
            proposal_id = str(row["proposal_id"]).strip()
            decision = str(row["decision"]).strip().upper()
            reviewer = str(row.get("reviewed_by") or "").strip()
            if not reviewer:
                raise ValueError(f"reviewed_by is required for proposal {proposal_id}")
            existing_decision = _existing_decision(config, proposal_id)
            if existing_decision:
                prior_decision = str(existing_decision.get("decision") or "")
                _update_proposal_status(config, proposal_id, "REJECTED" if prior_decision == "REJECT" else "IMPORTED")
                summary["existing"] += 1
                continue
            proposal = _proposal_by_id(config, proposal_id)
            if not proposal:
                raise ValueError(f"Proposal does not exist in BigQuery: {proposal_id}")
            if str(proposal.get("status")) != "PENDING":
                raise ValueError(f"Proposal {proposal_id} is not pending: {proposal.get('status')}")
            if str(row.get("content_hash")) != str(proposal.get("content_hash")):
                raise ValueError(f"Proposal content changed after workbook creation: {proposal_id}")

            current = _current_record(config, str(proposal["context_key"]))
            expected_base = str(proposal.get("base_record_id") or "")
            actual_base = str(current.get("record_id") if current else "")
            already_published = bool(current and str(current.get("proposal_id") or "") == proposal_id)
            if not already_published and expected_base != actual_base:
                raise ValueError(f"Context version changed while proposal was pending: {proposal_id}")

            reviewed_at = str(row.get("reviewed_at") or utc_now())
            resulting_record_id = None
            resulting_version = None
            approved_payload = None
            final_hash = str(proposal["content_hash"])
            if decision in {"APPROVE", "OVERRIDE"}:
                if decision == "OVERRIDE":
                    override = str(row.get("override_value") or "").strip()
                    if not override:
                        raise ValueError(f"override_value is required for proposal {proposal_id}")
                    approved_payload = json.loads(override)
                    if not isinstance(approved_payload, dict):
                        raise ValueError(f"override_value must be a JSON object for proposal {proposal_id}")
                    final_hash = payload_hash(approved_payload)
                else:
                    approved_payload = json.loads(str(proposal["proposed_payload_json"]))
                context_status = str(approved_payload.pop("_context_status", "TRUSTED")).upper()
                if context_status not in {"TRUSTED", "RETIRED"}:
                    raise ValueError(f"Invalid _context_status for proposal {proposal_id}: {context_status}")
                final_hash = payload_hash(approved_payload)
                if already_published:
                    resulting_version = int(current["version"])
                    resulting_record_id = str(current["record_id"])
                else:
                    resulting_version = int(current.get("version") or 0) + 1 if current else 1
                    resulting_record_id = stable_id(proposal["context_key"], resulting_version, final_hash)
                record = {
                    "record_id": resulting_record_id,
                    "context_key": proposal["context_key"],
                    "context_type": proposal["context_type"],
                    "subject_key": proposal["subject_key"],
                    "organization": proposal["organization"],
                    "environment": proposal["environment"],
                    "domain": proposal["domain"],
                    "pair_id": proposal.get("pair_id"),
                    "context_id": proposal.get("context_id"),
                    "target_table": proposal.get("target_table"),
                    "source_table": proposal.get("source_table"),
                    "column_name": proposal.get("column_name"),
                    "payload_json": canonical_json(approved_payload),
                    "content_hash": final_hash,
                    "version": resulting_version,
                    "status": context_status,
                    "origin": f"{proposal.get('origin', 'UNKNOWN')}_HUMAN_APPROVED",
                    "confidence": proposal.get("confidence"),
                    "source_file": proposal.get("source_file"),
                    "run_id": proposal.get("run_id"),
                    "proposal_id": proposal_id,
                    "approved_by": reviewer,
                    "approved_at": reviewed_at,
                    "created_at": utc_now(),
                    "provenance_json": canonical_json({"proposal_id": proposal_id, "approval_file": str(workbook_path)}),
                }
                if not already_published:
                    errors = _client(config.project.context_store).insert_rows_json(
                        _table(config.project.context_store, "context_records"), [record]
                    )
                    if errors:
                        raise RuntimeError(f"BigQuery rejected trusted context record: {errors}")
                summary["approved" if decision == "APPROVE" else "overridden"] += 1
            else:
                summary["rejected"] += 1

            decision_id = stable_id(proposal_id, decision, final_hash)
            decision_row = {
                "decision_id": decision_id,
                "proposal_id": proposal_id,
                "decision": decision,
                "override_payload_json": canonical_json(approved_payload) if decision == "OVERRIDE" else None,
                "reviewer_comments": str(row.get("reviewer_comments") or ""),
                "reviewed_by": reviewer,
                "reviewed_at": reviewed_at,
                "imported_at": utc_now(),
                "resulting_record_id": resulting_record_id,
                "resulting_version": resulting_version,
                "content_hash": final_hash,
                "approval_file": str(workbook_path),
                **config.project.context_store.namespace,
            }
            errors = _client(config.project.context_store).insert_rows_json(
                _table(config.project.context_store, "approval_decisions"), [decision_row]
            )
            if errors:
                raise RuntimeError(f"BigQuery rejected approval decision: {errors}")
            _update_proposal_status(config, proposal_id, "REJECTED" if decision == "REJECT" else "IMPORTED")
            receipt = {**decision_row, "context_type": proposal["context_type"], "subject_key": proposal["subject_key"]}
            receipt_dir = approval_paths["rejected" if decision == "REJECT" else "approved"]
            write_json(receipt_dir / f"{proposal_id}.json", receipt)
            receipts.append(receipt)
            if logger:
                logger.info(
                    "IMPORT_APPROVAL_RESULTS proposal_id=%s decision=%s context_key=%s version=%s",
                    proposal_id, decision, proposal["context_key"], resulting_version,
                )
                if proposal["context_type"] == "relationship" and decision in {"APPROVE", "OVERRIDE"}:
                    logger.info(
                        "PUBLISH_APPROVED_RELATIONSHIP_CONTEXT proposal_id=%s relationship=%s version=%s",
                        proposal_id, proposal["subject_key"], resulting_version,
                    )
    except Exception as exc:
        write_json(approval_paths["errors"] / f"{workbook_path.stem}_error.json", {
            "approval_file": str(workbook_path), "error": str(exc), "failed_at": utc_now(),
        })
        raise
    archived = archive_approval_workbook(workbook_path, approval_paths["processed"])
    if logger:
        logger.info("PUBLISH_APPROVED_CONTEXT counts=%s archived_file=%s", summary, archived)
    return {"summary": summary, "receipts": receipts, "archived_workbook": str(archived)}
