from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    if pd.isna(value):
        return None
    return str(value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=json_default), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=json_default) + "\n")


def configure_logging(output_dir: Path, level: str = "INFO") -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"dq_agent.{output_dir.name}")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    if logger.handlers:
        return logger
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
    )
    file_handler = logging.FileHandler(output_dir / "run.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger


def _frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame()
    frame = pd.json_normalize(records, sep=".")
    for column in frame.columns:
        frame[column] = frame[column].map(
            lambda value: json.dumps(value, default=json_default)
            if isinstance(value, (dict, list, tuple, set)) else value
        )
    return frame


def _safe_sheet(name: str) -> str:
    return re.sub(r"[\\/*?:\[\]]", "_", name)[:31] or "Sheet"


def write_table_report(
    table_dir: Path,
    summary: dict[str, Any],
    mappings: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    results: list[dict[str, Any]],
    rca: list[dict[str, Any]],
    relationship_candidates: list[dict[str, Any]],
) -> None:
    table_dir.mkdir(parents=True, exist_ok=True)
    workbook = table_dir / "validation_report.xlsx"
    profiles = [
        row for row in results
        if row.get("type") in {"column_profile", "null_count", "distinct_count", "value_distribution", "aggregate", "uniqueness"}
    ]
    row_reconciliation = [row for row in results if row.get("type") == "row_reconciliation"]
    human_results = [row for row in results if row.get("origin") == "human"]
    measure_results = [row for row in results if row.get("type") == "measure_reconciliation"]
    failed_measures = [row for row in measure_results if row.get("status") == "FAIL"]
    execution_errors = [row for row in results if row.get("status") == "ERROR"]
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        for name, records in {
            "Summary": [summary],
            "Mappings": mappings,
            "Rules": rules,
            "Results": results,
            "Profiles": profiles,
            "Relationship Candidates": relationship_candidates,
            "Row Reconciliation": row_reconciliation,
            "Human Tests": human_results,
            "Measure Reconciliation": measure_results,
            "Failed Measures": failed_measures,
            "Execution Errors": execution_errors,
            "RCA": rca,
        }.items():
            _frame(records).to_excel(writer, index=False, sheet_name=_safe_sheet(name))
    write_json(table_dir / "table_result.json", {
        "summary": summary,
        "mappings": mappings,
        "rules": rules,
        "results": results,
        "rca": rca,
        "relationship_candidates": relationship_candidates,
    })


def write_consolidated_reports(output_dir: Path, table_outputs: list[dict[str, Any]]) -> None:
    summaries = [item["summary"] for item in table_outputs]
    mappings = [row for item in table_outputs for row in item.get("mappings", [])]
    rules = [row for item in table_outputs for row in item.get("rules", [])]
    results = [row for item in table_outputs for row in item.get("results", [])]
    rca = [row for item in table_outputs for row in item.get("rca", [])]
    relationship_candidates = [
        row for item in table_outputs for row in item.get("relationship_candidates", [])
    ]
    failures = [row for row in results if row.get("status") in {"FAIL", "ERROR"}]
    freshness = [row for row in results if row.get("category") == "freshness"]
    profiles = [
        row for row in results
        if row.get("type") in {"column_profile", "null_count", "distinct_count", "value_distribution", "aggregate", "uniqueness"}
    ]
    row_reconciliation = [row for row in results if row.get("type") == "row_reconciliation"]
    human_results = [row for row in results if row.get("origin") == "human"]
    measure_results = [row for row in results if row.get("type") == "measure_reconciliation"]
    failed_measures = [row for row in measure_results if row.get("status") == "FAIL"]
    execution_errors = [row for row in results if row.get("status") == "ERROR"]
    workbook = output_dir / "consolidated_report.xlsx"
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        for name, records in {
            "Summary": summaries,
            "Failures": failures,
            "Freshness": freshness,
            "Mappings": mappings,
            "Rules": rules,
            "Profiles": profiles,
            "Relationship Candidates": relationship_candidates,
            "Row Reconciliation": row_reconciliation,
            "Human Tests": human_results,
            "Measure Reconciliation": measure_results,
            "Failed Measures": failed_measures,
            "Execution Errors": execution_errors,
            "RCA": rca,
        }.items():
            _frame(records).to_excel(writer, index=False, sheet_name=_safe_sheet(name))
    _frame(failures).to_csv(output_dir / "failed_tests.csv", index=False)
    _frame(rca).to_csv(output_dir / "rca_report.csv", index=False)
    _frame(mappings).to_csv(output_dir / "mapping_confidence.csv", index=False)
    _frame(freshness).to_csv(output_dir / "freshness.csv", index=False)
    profile_frame = _frame(profiles)
    profile_frame.to_csv(output_dir / "data_profiling.csv", index=False)
    if not profile_frame.empty:
        profile_frame.to_parquet(output_dir / "data_profiling.parquet", index=False)
    _frame(human_results).to_csv(output_dir / "human_test_results.csv", index=False)
    _frame(relationship_candidates).to_csv(output_dir / "relationship_candidates.csv", index=False)
    _frame(row_reconciliation).to_csv(output_dir / "row_reconciliation.csv", index=False)
    _frame(measure_results).to_csv(output_dir / "measure_reconciliation.csv", index=False)
    _frame(failed_measures).to_csv(output_dir / "failed_measures.csv", index=False)
    _frame(execution_errors).to_csv(output_dir / "execution_errors.csv", index=False)
    write_json(output_dir / "generated_rules.json", rules)


def write_relationship_reports(
    output_dir: Path,
    candidates: list[dict[str, Any]],
    results: list[dict[str, Any]],
) -> dict[str, Path]:
    """Write compact table-level and consolidated relationship reports."""
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_frame = _frame(candidates)
    result_frame = _frame(results)
    summary = [{
        "candidate_count": len(candidates),
        "executed_count": len(results),
        "passed": sum(row.get("status") == "PASS" for row in results),
        "failed": sum(row.get("status") == "FAIL" for row in results),
        "errors": sum(row.get("status") == "ERROR" for row in results),
        "orphan_count": sum(int(row.get("orphan_count") or 0) for row in results),
    }]
    workbook = output_dir / "referential_integrity_report.xlsx"
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        _frame(summary).to_excel(writer, index=False, sheet_name="Summary")
        candidate_frame.to_excel(writer, index=False, sheet_name="Candidates")
        result_frame.to_excel(writer, index=False, sheet_name="Results")
    candidate_frame.to_csv(output_dir / "relationship_candidates.csv", index=False)
    result_frame.to_csv(output_dir / "referential_integrity_results.csv", index=False)
    write_json(output_dir / "referential_integrity_results.json", results)

    for pair_id in sorted({str(row.get("pair_id")) for row in [*candidates, *results] if row.get("pair_id")}):
        table_dir = output_dir / "tables" / pair_id
        table_dir.mkdir(parents=True, exist_ok=True)
        table_candidates = [row for row in candidates if str(row.get("pair_id")) == pair_id]
        table_results = [row for row in results if str(row.get("pair_id")) == pair_id]
        with pd.ExcelWriter(table_dir / "relationship_report.xlsx", engine="openpyxl") as writer:
            _frame(table_candidates).to_excel(writer, index=False, sheet_name="Candidates")
            _frame(table_results).to_excel(writer, index=False, sheet_name="Results")
        write_json(table_dir / "relationship_results.json", table_results)
    return {
        "workbook": workbook,
        "candidate_csv": output_dir / "relationship_candidates.csv",
        "result_csv": output_dir / "referential_integrity_results.csv",
    }


def write_measure_reports(
    output_dir: Path,
    measures: list[dict[str, Any]],
    results: list[dict[str, Any]],
    rca: list[dict[str, Any]] | None = None,
) -> dict[str, Path]:
    """Write inspectable measure, reconciliation, failure, and RCA reports."""
    output_dir.mkdir(parents=True, exist_ok=True)
    rca = rca or []
    measure_frame = _frame(measures)
    result_frame = _frame(results)
    failed = [row for row in results if row.get("status") == "FAIL"]
    errors = [row for row in results if row.get("status") == "ERROR"]
    summary = [{
        "measure_count": len(measures),
        "reconciliation_rows": len(results),
        "passed": sum(row.get("status") == "PASS" for row in results),
        "failed": len(failed),
        "errors": len(errors),
        "rca_records": len(rca),
    }]
    workbook = output_dir / "measure_reconciliation_report.xlsx"
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        _frame(summary).to_excel(writer, index=False, sheet_name="Summary")
        measure_frame.to_excel(writer, index=False, sheet_name="Measures")
        result_frame.to_excel(writer, index=False, sheet_name="Reconciliation")
        _frame(failed).to_excel(writer, index=False, sheet_name="Failed Measures")
        _frame(errors).to_excel(writer, index=False, sheet_name="Execution Errors")
        _frame(rca).to_excel(writer, index=False, sheet_name="RCA")
    measure_frame.to_csv(output_dir / "resolved_measures.csv", index=False)
    result_frame.to_csv(output_dir / "measure_reconciliation.csv", index=False)
    _frame(failed).to_csv(output_dir / "failed_measures.csv", index=False)
    _frame(errors).to_csv(output_dir / "execution_errors.csv", index=False)
    _frame(rca).to_csv(output_dir / "measure_rca.csv", index=False)
    write_json(output_dir / "measure_reconciliation.json", results)
    write_json(output_dir / "measure_rca.json", rca)
    for pair_id in sorted({str(row.get("pair_id")) for row in [*measures, *results] if row.get("pair_id")}):
        table_dir = output_dir / "tables" / pair_id
        table_dir.mkdir(parents=True, exist_ok=True)
        write_json(table_dir / "measures.json", [row for row in measures if str(row.get("pair_id")) == pair_id])
        write_json(table_dir / "reconciliation.json", [row for row in results if str(row.get("pair_id")) == pair_id])
    return {
        "workbook": workbook,
        "result_csv": output_dir / "measure_reconciliation.csv",
        "failed_csv": output_dir / "failed_measures.csv",
        "rca_csv": output_dir / "measure_rca.csv",
    }
