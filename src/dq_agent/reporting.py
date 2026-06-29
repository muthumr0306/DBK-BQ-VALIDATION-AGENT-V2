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


def _build_failures_with_rca(
    failures: list[dict[str, Any]], rca: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Merge each failure result with its RCA conclusion so readers get cause + evidence in one row."""
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
    grain_results = [row for row in results if row.get("type") == "grain_reconciliation"]
    schema_gaps = [row for row in results if row.get("type") == "schema_gap"]
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
            "Grain Reconciliation": grain_results,
            "Schema Gaps": schema_gaps,
            "Execution Errors": execution_errors,
            "RCA": rca,
        }.items():
            _frame(records).to_excel(writer, index=False, sheet_name=_safe_sheet(name))
    _frame(failures).to_csv(output_dir / "failed_tests.csv", index=False)
    _frame(_build_failures_with_rca(failures, rca)).to_csv(output_dir / "failures_with_rca.csv", index=False)
    _frame(rca).to_csv(output_dir / "rca_report.csv", index=False)
    _frame(mappings).to_csv(output_dir / "mapping_confidence.csv", index=False)
    _frame(freshness).to_csv(output_dir / "freshness.csv", index=False)
    profile_frame = _frame(profiles)
    profile_frame.to_csv(output_dir / "data_profiling.csv", index=False)
    if not profile_frame.empty:
        # Cast object columns to string so pyarrow doesn't choke on mixed-type evidence values
        _pf = profile_frame.astype(
            {c: "string" for c in profile_frame.select_dtypes(include="object").columns}
        )
        _pf.to_parquet(output_dir / "data_profiling.parquet", index=False)
    _frame(human_results).to_csv(output_dir / "human_test_results.csv", index=False)
    _frame(relationship_candidates).to_csv(output_dir / "relationship_candidates.csv", index=False)
    _frame(row_reconciliation).to_csv(output_dir / "row_reconciliation.csv", index=False)
    _frame(measure_results).to_csv(output_dir / "measure_reconciliation.csv", index=False)
    _frame(failed_measures).to_csv(output_dir / "failed_measures.csv", index=False)
    _frame(grain_results).to_csv(output_dir / "grain_reconciliation.csv", index=False)
    _frame(schema_gaps).to_csv(output_dir / "schema_gaps.csv", index=False)
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
