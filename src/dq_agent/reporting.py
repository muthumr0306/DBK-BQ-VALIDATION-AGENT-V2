from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from openpyxl import load_workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill


def json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return str(value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=json_default), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=json_default) + "\n")


def configure_logging(log_dir: Path, level: str = "INFO") -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"dq_agent.{log_dir.name}")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    if logger.handlers:
        return logger
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"
    )
    file_handler = logging.FileHandler(log_dir / "run.log", encoding="utf-8")
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


def _failure_rows(results: list[dict[str, Any]], rca: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rca_by_rule = {str(row.get("rule_id")): row for row in rca if row.get("rule_id")}
    output: list[dict[str, Any]] = []
    for result in results:
        if result.get("status") not in {"FAIL", "ERROR"}:
            continue
        row = dict(result)
        investigation = rca_by_rule.get(str(result.get("rule_id")))
        if investigation:
            conclusion = investigation.get("conclusion", investigation)
            row["rca_classification"] = conclusion.get("classification") if isinstance(conclusion, dict) else None
            row["rca_conclusion"] = conclusion.get("conclusion") if isinstance(conclusion, dict) else conclusion
            row["rca_confidence"] = conclusion.get("confidence") if isinstance(conclusion, dict) else None
            row["rca_evidence"] = investigation.get("evidence_observed", investigation.get("diagnostics"))
        output.append(row)
    return output


def write_consolidated_reports(
    output_dir: Path,
    table_outputs: list[dict[str, Any]],
    approval_items: list[dict[str, Any]] | None = None,
    run_metadata: dict[str, Any] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    approval_items = approval_items or []
    run_metadata = run_metadata or {}
    summaries = [item["summary"] for item in table_outputs]
    mappings = [row for item in table_outputs for row in item.get("mappings", [])]
    rules = [row for item in table_outputs for row in item.get("rules", [])]
    results = [row for item in table_outputs for row in item.get("results", [])]
    rca = [row for item in table_outputs for row in item.get("rca", [])]
    relationships = [
        row for item in table_outputs for row in item.get("relationship_candidates", [])
    ]
    errors = [row for row in results if row.get("status") == "ERROR"]
    errors.extend(
        {"scope": "table", **row}
        for row in summaries if row.get("status") == "ERROR"
    )
    if run_metadata.get("error"):
        errors.append({"scope": "run", "error": run_metadata.get("error")})
    run_summary = [{
        "run_id": run_metadata.get("run_id"),
        "project": run_metadata.get("project"),
        "status": run_metadata.get("status", "RUNNING"),
        "started_at": run_metadata.get("started_at"),
        "completed_at": run_metadata.get("completed_at"),
        "tables": len(summaries),
        "passed_tables": sum(row.get("status") == "PASS" for row in summaries),
        "failed_tables": sum(row.get("status") == "FAIL" for row in summaries),
        "error_tables": sum(row.get("status") == "ERROR" for row in summaries),
        "rules": len(results),
        "passed_checks": sum(row.get("status") == "PASS" for row in results),
        "failed_checks": sum(row.get("status") == "FAIL" for row in results),
        "errors": sum(row.get("status") == "ERROR" for row in results),
        "approval_items": len(approval_items),
    }]

    sheets: dict[str, list[dict[str, Any]]] = {
        "Run Summary": run_summary,
        "Tables": summaries,
        "Mappings": mappings,
        "Schema": [row for row in results if row.get("category") == "schema"],
        "Row Counts": [row for row in results if row.get("type") in {"row_count", "key_values", "key_buckets", "row_reconciliation"}],
        "Freshness": [row for row in results if row.get("category") == "freshness"],
        "Profiles": [row for row in results if row.get("category") in {"profiling", "distribution", "completeness"} or row.get("type") == "column_profile"],
        "Business Rules": [row for row in results if row.get("category") in {"business_rule", "llm_generated", "validity"}],
        "Human Tests": [row for row in results if row.get("origin") == "human"],
        "Relationships": [*relationships, *[row for row in results if row.get("type") == "relationship"]],
        "Measures": [row for row in results if row.get("type") == "measure_reconciliation"],
        "RCA": rca,
        "Failures": _failure_rows(results, rca),
        "Approvals": approval_items,
        "Errors": errors,
    }
    workbook_path = output_dir / "dq_validation_report.xlsx"
    with pd.ExcelWriter(workbook_path, engine="openpyxl") as writer:
        for name, records in sheets.items():
            frame = _frame(records)
            if frame.empty:
                frame = pd.DataFrame([{"message": "No records for this section"}])
            frame.to_excel(writer, index=False, sheet_name=_safe_sheet(name))

    workbook = load_workbook(workbook_path)
    status_colors = {"PASS": "C6EFCE", "FAIL": "FFC7CE", "ERROR": "F4B183", "SKIP": "D9EAF7", "PENDING": "FFF2CC"}
    for sheet in workbook.worksheets:
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        sheet.sheet_view.showGridLines = False
        for cell in sheet[1]:
            cell.fill = PatternFill("solid", fgColor="1F4E78")
            cell.font = Font(color="FFFFFF", bold=True)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for column_cells in sheet.columns:
            longest = max(len(str(cell.value or "")) for cell in list(column_cells)[:200])
            sheet.column_dimensions[column_cells[0].column_letter].width = min(max(longest + 2, 12), 50)
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
                value = str(cell.value or "").upper()
                if value in status_colors:
                    cell.fill = PatternFill("solid", fgColor=status_colors[value])
    summary_sheet = workbook["Run Summary"]
    if summary_sheet.max_column >= 12:
        chart = BarChart()
        chart.title = "Validation Check Status"
        chart.y_axis.title = "Checks"
        chart.x_axis.title = "Status"
        data = Reference(summary_sheet, min_col=10, max_col=12, min_row=1, max_row=2)
        chart.add_data(data, titles_from_data=True)
        chart.height = 7
        chart.width = 13
        summary_sheet.add_chart(chart, "A5")
    workbook.save(workbook_path)
    return workbook_path


def write_relationship_reports(
    output_dir: Path, candidates: list[dict[str, Any]], results: list[dict[str, Any]]
) -> dict[str, Path]:
    workbook = write_consolidated_reports(output_dir, [{
        "summary": {"pair_id": "relationship_workflow", "status": "COMPLETED"},
        "mappings": [], "rules": [], "results": results, "rca": [],
        "relationship_candidates": candidates,
    }])
    return {"workbook": workbook}


def write_measure_reports(
    output_dir: Path, measures: list[dict[str, Any]], results: list[dict[str, Any]],
    rca: list[dict[str, Any]] | None = None,
) -> dict[str, Path]:
    del measures
    workbook = write_consolidated_reports(output_dir, [{
        "summary": {"pair_id": "measure_workflow", "status": "COMPLETED"},
        "mappings": [], "rules": [], "results": results, "rca": rca or [],
        "relationship_candidates": [],
    }])
    return {"workbook": workbook}
