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


def _build_failures_with_rca(
    failures: list[dict[str, Any]], rca: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    rca_by_rule = {r["rule_id"]: r for r in rca if r.get("rule_id")}
    merged = []
    for failure in failures:
        row = dict(failure)
        rca_rec = rca_by_rule.get(failure.get("rule_id", ""))
        if rca_rec:
            conclusion = rca_rec.get("conclusion", {})
            if isinstance(conclusion, dict):
                row["rca_conclusion"] = conclusion.get("conclusion")
                row["rca_confidence"] = conclusion.get("confidence")
                row["rca_inconclusive"] = conclusion.get("inconclusive")
                row["rca_evidence_summary"] = json.dumps(
                    conclusion.get("evidence_summary", []), default=json_default
                )
            row["rca_root_cause_group"] = rca_rec.get("root_cause_group")
            row["rca_is_cascade"] = rca_rec.get("is_cascade")
        merged.append(row)
    return merged


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
    failures = [row for row in results if row.get("status") in {"FAIL", "ERROR"}]
    freshness = [row for row in results if row.get("category") == "freshness"]
    profiles = [
        row for row in results
        if row.get("type") in {"column_profile", "null_count", "distinct_count", "value_distribution", "aggregate", "uniqueness"}
        or row.get("category") in {"profiling", "distribution", "completeness"}
    ]
    row_reconciliation = [row for row in results if row.get("type") == "row_reconciliation"]
    human_results = [row for row in results if row.get("origin") == "human"]
    measure_results = [row for row in results if row.get("type") == "measure_reconciliation"]
    failed_measures = [row for row in measure_results if row.get("status") == "FAIL"]
    grain_results = [row for row in results if row.get("type") == "grain_reconciliation"]
    schema_gaps = [row for row in results if row.get("type") == "schema_gap"]
    execution_errors = [row for row in results if row.get("status") == "ERROR"]

    errors = list(execution_errors)
    errors.extend({"scope": "table", **row} for row in summaries if row.get("status") == "ERROR")
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
        "Schema": [row for row in results if row.get("category") == "schema"] + schema_gaps,
        "Row Counts": [row for row in results if row.get("type") in {"row_count", "key_values", "key_buckets", "row_reconciliation"}],
        "Freshness": freshness,
        "Profiles": profiles,
        "Business Rules": [row for row in results if row.get("category") in {"business_rule", "llm_generated", "validity"}],
        "Human Tests": human_results,
        "Relationships": [*relationships, *[row for row in results if row.get("type") == "relationship"]],
        "Measures": measure_results,
        "Grain Reconciliation": grain_results,
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
    if "Run Summary" in workbook.sheetnames and workbook["Run Summary"].max_column >= 12:
        summary_sheet = workbook["Run Summary"]
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

    # Write tracked CSV outputs
    _frame(failures).to_csv(output_dir / "failed_tests.csv", index=False)
    _frame(_build_failures_with_rca(failures, rca)).to_csv(output_dir / "failures_with_rca.csv", index=False)
    _frame(rca).to_csv(output_dir / "rca_report.csv", index=False)
    _frame(mappings).to_csv(output_dir / "mapping_confidence.csv", index=False)
    _frame(freshness).to_csv(output_dir / "freshness.csv", index=False)
    profile_frame = _frame(profiles)
    profile_frame.to_csv(output_dir / "data_profiling.csv", index=False)
    if not profile_frame.empty:
        _pf = profile_frame.astype(
            {c: "string" for c in profile_frame.select_dtypes(include="object").columns}
        )
        _pf.to_parquet(output_dir / "data_profiling.parquet", index=False)
    _frame(human_results).to_csv(output_dir / "human_test_results.csv", index=False)
    _frame(relationships).to_csv(output_dir / "relationship_candidates.csv", index=False)
    _frame(row_reconciliation).to_csv(output_dir / "row_reconciliation.csv", index=False)
    _frame(measure_results).to_csv(output_dir / "measure_reconciliation.csv", index=False)
    _frame(failed_measures).to_csv(output_dir / "failed_measures.csv", index=False)
    _frame(grain_results).to_csv(output_dir / "grain_reconciliation.csv", index=False)
    _frame(schema_gaps).to_csv(output_dir / "schema_gaps.csv", index=False)
    _frame(execution_errors).to_csv(output_dir / "execution_errors.csv", index=False)
    write_json(output_dir / "generated_rules.json", rules)
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


def write_run_summary(
    output_dir: Path,
    manifest: dict[str, Any],
    table_outputs: list[dict[str, Any]],
    pending_reviews: list[dict[str, Any]],
) -> None:
    """Write summary.md — a plain-English narrative of the run readable without opening any other file."""
    _STATUS_ICON = {
        "PASS": "PASS",
        "FAIL": "FAIL",
        "ERROR": "ERROR",
        "WAITING_FOR_REVIEW": "WAITING FOR REVIEW",
        "COMPLETED": "PASS",
        "COMPLETED_WITH_FAILURES": "FAIL",
        "COMPLETED_WITH_ERRORS": "ERROR",
        "COMPLETED_WAITING_FOR_REVIEW": "WAITING FOR REVIEW",
    }

    lines: list[str] = []
    run_status = manifest.get("status", "UNKNOWN")
    lines.append(f"# DQ Run: {manifest.get('run_id', 'unknown')}")
    lines.append(f"**Project**: {manifest.get('project', '')}  ")
    lines.append(f"**Status**: {_STATUS_ICON.get(run_status, run_status)}  ")

    started = manifest.get("started_at", "")
    completed = manifest.get("completed_at", "")
    if started and completed:
        try:
            from datetime import timezone as _tz
            t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(completed.replace("Z", "+00:00"))
            secs = int((t1 - t0).total_seconds())
            lines.append(f"**Duration**: {secs // 60}m {secs % 60}s ({started[:19]} → {completed[11:19]} UTC)  ")
        except Exception:
            pass

    lines += ["", "---", ""]

    rca_by_rule: dict[str, dict[str, Any]] = {}
    output_by_pair: dict[str, dict[str, Any]] = {}
    for item in table_outputs:
        pid = item["summary"]["pair_id"]
        output_by_pair[pid] = item
        for rca_rec in item.get("rca", []):
            if rca_rec.get("rule_id"):
                rca_by_rule[rca_rec["rule_id"]] = rca_rec

    reviews_by_pair: dict[str, list[dict[str, Any]]] = {}
    for review in pending_reviews:
        reviews_by_pair.setdefault(review.get("pair_id", ""), []).append(review)

    for table in manifest.get("tables", []):
        pair_id = table.get("pair_id", "")
        status = table.get("status", "UNKNOWN")
        status_label = _STATUS_ICON.get(status, status)
        lines.append(f"## [{status_label}] {pair_id}")

        rule_count = table.get("rule_count")
        passed = table.get("passed")
        failed = table.get("failed")
        errors = table.get("errors")

        if rule_count is not None:
            parts = []
            if passed is not None:
                parts.append(f"{passed} passed")
            if failed:
                parts.append(f"**{failed} failed**")
            if errors:
                parts.append(f"{errors} errors")
            lines.append(f"{rule_count} rules — " + ", ".join(parts) if parts else f"{rule_count} rules")

        root_cause_count = table.get("root_cause_count")
        cascade_count = table.get("cascade_count", 0)
        if root_cause_count is not None and cascade_count > 0:
            lines.append(f"*{root_cause_count} root cause(s), {cascade_count} cascading signal(s)*")

        if status in {"FAIL", "ERROR"} and failed:
            item = output_by_pair.get(pair_id, {})
            failed_results = [r for r in item.get("results", []) if r.get("status") in {"FAIL", "ERROR"}]
            if failed_results:
                lines.append("")
                lines.append("**Failures:**")
                for result in failed_results:
                    rule_id = result.get("rule_id", "")
                    rca_rec = rca_by_rule.get(rule_id)
                    is_cascade = bool(rca_rec.get("is_cascade")) if rca_rec else False
                    tag = " *(cascade)*" if is_cascade else ""
                    conclusion_text = ""
                    if rca_rec:
                        c = rca_rec.get("conclusion", {})
                        conclusion_text = str(c.get("conclusion") or "" if isinstance(c, dict) else c)[:200]
                    rule_short = rule_id.replace(f"{pair_id}__", "")
                    lines.append(f"- `{rule_short}`{tag}: {conclusion_text}")

        if status == "WAITING_FOR_REVIEW":
            blocking = [r for r in reviews_by_pair.get(pair_id, []) if r.get("blocking")]
            if blocking:
                lines.append("")
                lines.append(f"Rules did not execute — {len(blocking)} pending decision(s) required.")
                lines.append("")
                lines.append("**Pending decisions:**")
                for review in blocking:
                    category = review.get("category", "")
                    subject = review.get("subject", "")
                    confidence = float(review.get("confidence") or 0)
                    try:
                        proposal = json.loads(review.get("proposal", "{}"))
                    except Exception:
                        proposal = {}
                    if category == "column_mapping":
                        src = proposal.get("source_column", subject)
                        tgt = proposal.get("target_column", "?")
                        lines.append(
                            f"- Approve column mapping: **{src}** (source) → **{tgt}** (target)?"
                            f" [confidence: {confidence:.0%}]"
                        )
                    elif category == "primary_key":
                        lines.append(f"- Approve primary key: {json.dumps(proposal)}? [confidence: {confidence:.0%}]")
                    else:
                        lines.append(f"- [{category}] {subject}: confidence {confidence:.0%}")

        lines += ["", "---", ""]

    lines.append("*Key files:*")
    lines.append("- `failures_with_rca.csv` — every failed check with its root cause explanation inline")
    lines.append("- `consolidated_report.xlsx` — full results across all tables")
    lines.append("- `data_profiling.csv` — column-level statistics")
    lines.append("- `approval_proposals.json` — pending decisions blocking rule execution")

    (output_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")
