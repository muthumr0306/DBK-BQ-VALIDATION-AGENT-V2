"""
Tiered reporting layer — converts engine check results into three CSV grains
(check / table / run) and one standalone HTML report.

Severity and verdict are computed deterministically from config thresholds.
No LLM calls are made in this module.

Entry point: write_tiered_reports()
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Sequence

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

import yaml

# ─────────────────────────────────────────── config ──────────────────────────

_STRUCTURAL_DEFAULTS = [
    "pk_in_source_not_target",
    "pk_in_target_not_source",
    "duplicate_keys",
    "grain_breakage",
    "target_key_unique",
]


@dataclass
class ReportingConfig:
    critical_pct_rows: float = 0.01
    critical_value_at_risk: float = 100_000.0
    structural_check_types: list[str] = field(default_factory=lambda: list(_STRUCTURAL_DEFAULTS))
    example_failing_keys_limit: int = 5


def load_reporting_config(path: Path | None = None) -> ReportingConfig:
    if path is None:
        candidates = [
            Path(__file__).resolve().parent.parent.parent / "config" / "reporting.yaml",
            Path("config/reporting.yaml"),
        ]
        path = next((p for p in candidates if p.exists()), None)

    if path is None or not path.exists():
        return ReportingConfig()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    v = raw.get("verdict", {}) or {}
    return ReportingConfig(
        critical_pct_rows=float(v.get("critical_pct_rows", 0.01)),
        critical_value_at_risk=float(v.get("critical_value_at_risk", 100_000)),
        structural_check_types=list(v.get("structural_check_types", _STRUCTURAL_DEFAULTS)),
        example_failing_keys_limit=int(raw.get("example_failing_keys_limit", 5)),
    )


# ─────────────────────────────────────────── data classes ────────────────────

@dataclass
class CheckRow:
    run_id: str
    table_pair_id: str
    source_table: str
    target_table: str
    check_id: str
    check_family: str       # reconciliation | business_rule | llm_generated
    check_type: str
    check_description: str
    column_scope: str
    status: str             # PASS | FAIL | ERROR | PENDING_APPROVAL
    severity: str           # critical | warning | info
    is_structural: bool
    rows_affected: int
    rows_total: int
    pct_rows_affected: float
    value_at_risk: float
    value_metric: str
    impact_summary: str
    rca_summary: str
    is_llm_generated: bool
    llm_test_approved: bool
    affects_verdict: bool
    example_failing_keys: str
    sql_source_path: str
    sql_target_path: str


@dataclass
class TableVerdict:
    run_id: str
    table_pair_id: str
    source_table: str
    target_table: str
    verdict: str            # PASS | WARN | FAIL
    verdict_reason: str
    rows_total: int
    total_rows_affected: int
    pct_rows_affected: float
    total_value_at_risk: float
    checks_total: int
    checks_passed: int
    checks_failed: int
    checks_error: int
    checks_pending_approval: int
    critical_count: int
    warning_count: int
    info_count: int
    structural_failure_count: int
    recon_failed: int
    business_rule_failed: int
    llm_failed: int
    worst_check_id: str


@dataclass
class RunSummary:
    run_id: str
    run_timestamp: str
    overall_verdict: str
    tables_total: int
    tables_go: int
    tables_caveats: int
    tables_nogo: int
    total_rows_affected: int
    total_value_at_risk: float
    total_critical: int
    total_warning: int
    total_pending_approval: int
    headline: str


# ─────────────────────────────── evidence extraction ─────────────────────────

def _safe_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(v: Any) -> float:
    try:
        f = float(v or 0)
        return 0.0 if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return 0.0


def _extract_metrics(
    result: dict[str, Any],
) -> tuple[int, int, float, str, list[str]]:
    """Return (rows_affected, rows_total, value_at_risk, value_metric, key_examples)."""
    evidence = result.get("evidence") or {}
    comparison = result.get("comparison") or {}
    rtype = result.get("type", "")

    src_ev: dict[str, Any] = evidence.get("source") or {}
    tgt_ev: dict[str, Any] = evidence.get("target") or {}

    rows_affected = 0
    rows_total = 0
    value_at_risk = 0.0
    value_metric = ""
    key_examples: list[str] = []

    if rtype == "row_count":
        src_n = _safe_int(src_ev.get("count") or src_ev.get("row_count")
                          or comparison.get("source_count") or evidence.get("source_count"))
        tgt_n = _safe_int(tgt_ev.get("count") or tgt_ev.get("row_count")
                          or comparison.get("target_count") or evidence.get("target_count"))
        rows_affected = abs(src_n - tgt_n)
        rows_total = max(src_n, tgt_n)

    elif rtype in ("null_count", "not_null", "context_required"):
        side = tgt_ev or src_ev or evidence
        rows_affected = _safe_int(side.get("null_count") or side.get("count"))
        rows_total = _safe_int(side.get("row_count") or side.get("total_count")
                               or side.get("total_rows"))

    elif rtype == "distinct_count":
        src_d = _safe_int(src_ev.get("distinct_count"))
        tgt_d = _safe_int(tgt_ev.get("distinct_count"))
        rows_affected = abs(src_d - tgt_d)
        rows_total = max(
            _safe_int(src_ev.get("row_count")),
            _safe_int(tgt_ev.get("row_count")),
        )

    elif rtype in ("row_reconciliation", "exact_keys_when_small", "key_buckets",
                   "mapped_rows_when_small"):
        rows_affected = _safe_int(
            comparison.get("source_only_count") or comparison.get("missing_count")
            or evidence.get("source_only_count") or evidence.get("missing_count")
        )
        rows_total = _safe_int(
            comparison.get("source_total") or comparison.get("total_source")
            or evidence.get("source_count") or evidence.get("total_source")
        )
        so = comparison.get("source_only") or evidence.get("source_only") or []
        if isinstance(so, list):
            key_examples = [str(k) for k in so[:5]]

    elif rtype == "target_key_unique":
        rows_affected = _safe_int(
            comparison.get("duplicate_count") or evidence.get("duplicate_count")
            or (tgt_ev or {}).get("duplicate_count")
        )
        rows_total = _safe_int(
            comparison.get("total") or evidence.get("row_count")
            or (tgt_ev or {}).get("row_count")
        )

    elif rtype == "measure_reconciliation":
        src_val = _safe_float((src_ev or {}).get("total") or (src_ev or {}).get("value"))
        tgt_val = _safe_float((tgt_ev or {}).get("total") or (tgt_ev or {}).get("value"))
        value_at_risk = abs(src_val - tgt_val)
        value_metric = result.get("measure_id", "")

    elif rtype in ("predicate", "domain", "context_domain", "context_minimum",
                   "context_maximum", "aggregate", "custom_sql"):
        side = tgt_ev or src_ev or evidence
        rows_affected = _safe_int(
            side.get("invalid_count") or side.get("violation_count")
            or side.get("count") or comparison.get("invalid_count")
        )
        rows_total = _safe_int(
            side.get("total_count") or side.get("row_count") or comparison.get("total_count")
        )

    elif rtype == "freshness":
        if result.get("status") == "FAIL":
            rows_affected = 1
            rows_total = 1

    elif rtype == "distribution":
        rows_affected = _safe_int(
            comparison.get("mismatched_buckets") or comparison.get("rows_affected")
            or evidence.get("mismatched")
        )
        rows_total = _safe_int(comparison.get("total_buckets") or evidence.get("total"))

    # Generic fallback for failed checks with no matched type
    if rows_affected == 0 and result.get("status") == "FAIL":
        for container in (comparison, evidence):
            if isinstance(container, dict):
                for key in ("rows_affected", "affected_count", "affected", "count"):
                    v = container.get(key)
                    if v:
                        rows_affected = _safe_int(v)
                        break
                if rows_affected:
                    break

    if rows_total == 0:
        for container in (comparison, evidence):
            if isinstance(container, dict):
                for key in ("rows_total", "total_count", "total_rows", "row_count"):
                    v = container.get(key)
                    if v:
                        rows_total = _safe_int(v)
                        break
                if rows_total:
                    break

    # Key examples fallback
    if not key_examples:
        for container in (evidence, comparison):
            if isinstance(container, dict):
                for key in ("key_examples", "example_keys", "failing_keys"):
                    ex = container.get(key)
                    if isinstance(ex, list) and ex:
                        key_examples = [str(k) for k in ex[:5]]
                        break
                if key_examples:
                    break

    return rows_affected, rows_total, value_at_risk, value_metric, key_examples


# ─────────────────────────────── field mapping helpers ───────────────────────

_BUSINESS_RULE_TYPES = {
    "context_domain", "context_minimum", "context_maximum", "context_required",
    "predicate", "domain", "aggregate", "custom_sql",
}
_SKIP_TYPES = {
    "column_profile", "value_distribution", "key_profile",
    "date_coverage", "rca_date_coverage", "rca_mismatched_buckets",
    "top_duplicates", "null_distribution",
}


def _map_check_family(result: dict[str, Any]) -> str:
    origin = result.get("origin", "")
    category = result.get("category", "")
    rtype = result.get("type", "")

    if origin == "llm_generated":
        return "llm_generated"
    if origin == "human" or category in ("human",):
        return "business_rule"
    if rtype in _BUSINESS_RULE_TYPES:
        return "business_rule"
    return "reconciliation"


def _column_scope(result: dict[str, Any]) -> str:
    cols = list(result.get("target_columns") or result.get("source_columns") or [])
    return ",".join(cols)


def _build_impact_summary(
    result: dict[str, Any],
    rows_affected: int,
    rows_total: int,
    value_at_risk: float,
    rca_summary: str,
) -> str:
    status = result.get("status", "")
    rtype = result.get("type", "")
    desc = result.get("description") or ""

    if status == "PASS":
        if rtype == "row_count":
            n = f"{rows_total:,}" if rows_total else "?"
            return f"Row counts match ({n} = {n})."
        if rtype == "freshness":
            return "Data is fresh."
        return "Check passed."

    if status in ("FAIL", "ERROR"):
        if rows_affected > 0 and rows_total > 0:
            pct_str = f"{rows_affected / rows_total:.1%}"
            val_str = f" (~${value_at_risk:,.0f} at risk)" if value_at_risk > 0 else ""
            return f"{rows_affected:,} rows ({pct_str}) affected{val_str}."
        if rows_affected > 0:
            val_str = f" (~${value_at_risk:,.0f} at risk)" if value_at_risk > 0 else ""
            return f"{rows_affected:,} rows affected{val_str}."
        if rca_summary:
            return rca_summary
        return desc or "Check failed."

    if status == "PENDING_APPROVAL":
        return "AI-suggested check — pending human review before result counts toward verdict."

    return desc or "No impact data."


# ─────────────────────────────── severity / verdict ──────────────────────────

def compute_is_structural(check_type: str, config: ReportingConfig) -> bool:
    return check_type in config.structural_check_types


def compute_check_severity(
    is_structural: bool,
    status: str,
    pct_rows: float,
    value_at_risk: float,
    config: ReportingConfig,
) -> str:
    if status in ("PASS", "PENDING_APPROVAL"):
        return "info"
    if status == "ERROR":
        return "critical"
    # status == FAIL
    if is_structural:
        return "critical"
    if pct_rows >= config.critical_pct_rows or value_at_risk >= config.critical_value_at_risk:
        return "critical"
    return "warning"


# ─────────────────────────────── engine → check ──────────────────────────────

def engine_result_to_check(
    result: dict[str, Any],
    run_id: str,
    pair_metadata: dict[str, str],
    rca_map: dict[str, str],
    pending_llm_ids: set[str],
    approved_llm_ids: set[str],
    config: ReportingConfig,
) -> CheckRow | None:
    """Convert one engine result dict to a CheckRow. Returns None for profiling rows."""
    rtype = result.get("type", "")
    if rtype in _SKIP_TYPES or result.get("informational"):
        return None

    rows_affected, rows_total, value_at_risk, value_metric, key_examples = _extract_metrics(result)
    pct = rows_affected / rows_total if rows_total > 0 else 0.0

    rule_id = result.get("rule_id", "")
    origin = result.get("origin", "")
    is_llm = origin == "llm_generated" or rule_id in pending_llm_ids
    llm_approved = origin == "llm_generated" or rule_id in approved_llm_ids

    status = result.get("status", "PASS")
    if is_llm and not llm_approved and rule_id in pending_llm_ids:
        status = "PENDING_APPROVAL"

    is_structural = compute_is_structural(rtype, config)
    severity = compute_check_severity(is_structural, status, pct, value_at_risk, config)
    check_family = _map_check_family(result)
    rca_summary = rca_map.get(rule_id, "")
    impact = _build_impact_summary(result, rows_affected, rows_total, value_at_risk, rca_summary)
    keys_str = "; ".join(
        str(k) for k in key_examples[:config.example_failing_keys_limit]
    )

    check = CheckRow(
        run_id=run_id,
        table_pair_id=result.get("pair_id", ""),
        source_table=pair_metadata.get("source_table", ""),
        target_table=pair_metadata.get("target_table", ""),
        check_id=rule_id,
        check_family=check_family,
        check_type=rtype,
        check_description=result.get("description") or "",
        column_scope=_column_scope(result),
        status=status,
        severity=severity,
        is_structural=is_structural,
        rows_affected=rows_affected,
        rows_total=rows_total,
        pct_rows_affected=pct,
        value_at_risk=value_at_risk,
        value_metric=value_metric,
        impact_summary=impact,
        rca_summary=rca_summary,
        is_llm_generated=is_llm,
        llm_test_approved=llm_approved,
        affects_verdict=status not in ("PENDING_APPROVAL",),
        example_failing_keys=keys_str,
        sql_source_path=result.get("source_sql") or "",
        sql_target_path=result.get("target_sql") or "",
    )
    return check


def pending_proposal_to_check(
    proposal: dict[str, Any],
    run_id: str,
    pair_metadata: dict[str, str],
    config: ReportingConfig,
) -> CheckRow | None:
    """Stub CheckRow for a non-blocking pending LLM business_rule proposal."""
    if proposal.get("category") != "business_rule":
        return None
    if proposal.get("status", "PENDING") not in ("PENDING", "REVIEW"):
        return None

    pair_id = proposal.get("pair_id", "")
    subject = proposal.get("subject", "")
    try:
        body = json.loads(proposal.get("proposal", "{}"))
    except (json.JSONDecodeError, TypeError):
        body = {}

    check_type = body.get("type", "predicate")
    cols = body.get("target_columns") or []

    return CheckRow(
        run_id=run_id,
        table_pair_id=pair_id,
        source_table=pair_metadata.get("source_table", ""),
        target_table=pair_metadata.get("target_table", ""),
        check_id=f"{pair_id}__{subject}",
        check_family="llm_generated",
        check_type=check_type,
        check_description=body.get("description") or "",
        column_scope=",".join(cols),
        status="PENDING_APPROVAL",
        severity="info",
        is_structural=False,
        rows_affected=0,
        rows_total=0,
        pct_rows_affected=0.0,
        value_at_risk=0.0,
        value_metric="",
        impact_summary="AI-suggested check — pending human review before result counts toward verdict.",
        rca_summary="",
        is_llm_generated=True,
        llm_test_approved=False,
        affects_verdict=False,
        example_failing_keys="",
        sql_source_path="",
        sql_target_path="",
    )


# ─────────────────────────────── aggregation ─────────────────────────────────

def checks_to_table_verdict(
    checks: Sequence[CheckRow],
    run_id: str,
    table_pair_id: str,
    source_table: str,
    target_table: str,
    config: ReportingConfig,
) -> TableVerdict:
    n_pass = sum(1 for c in checks if c.status == "PASS")
    n_fail = sum(1 for c in checks if c.status == "FAIL")
    n_error = sum(1 for c in checks if c.status == "ERROR")
    n_pending = sum(1 for c in checks if c.status == "PENDING_APPROVAL")

    n_critical = sum(1 for c in checks if c.severity == "critical")
    n_warning = sum(1 for c in checks if c.severity == "warning")
    n_info = sum(1 for c in checks if c.severity == "info")

    verdict_checks = [c for c in checks if c.affects_verdict]
    struct_failures = [
        c for c in verdict_checks
        if c.is_structural and c.status in ("FAIL", "ERROR")
    ]
    n_structural = len(struct_failures)

    recon_failed = sum(
        1 for c in verdict_checks
        if c.status in ("FAIL", "ERROR") and c.check_family == "reconciliation"
    )
    br_failed = sum(
        1 for c in verdict_checks
        if c.status in ("FAIL", "ERROR") and c.check_family == "business_rule"
    )
    llm_failed = sum(
        1 for c in verdict_checks
        if c.status in ("FAIL", "ERROR") and c.check_family == "llm_generated"
    )

    rows_total = max((c.rows_total for c in checks if c.rows_total > 0), default=0)
    failed_checks = [c for c in verdict_checks if c.status in ("FAIL", "ERROR")]
    total_rows_affected = sum(c.rows_affected for c in failed_checks)
    total_value_at_risk = sum(c.value_at_risk for c in failed_checks)
    pct_affected = total_rows_affected / rows_total if rows_total > 0 else 0.0

    has_critical = any(
        c.severity == "critical" and c.affects_verdict and c.status in ("FAIL", "ERROR")
        for c in checks
    )

    if n_structural > 0 or has_critical:
        verdict = "FAIL"
    elif n_fail > 0 or n_error > 0:
        verdict = "WARN"
    else:
        verdict = "PASS"

    if verdict == "FAIL":
        if struct_failures:
            worst = struct_failures[0]
            ra = f"{worst.rows_affected:,}" if worst.rows_affected else "some"
            verdict_reason = (
                f"Structural {worst.check_type} failure: {ra} rows affected "
                f"(always FAIL regardless of %)."
            )
        else:
            crit = next(
                (c for c in checks if c.severity == "critical" and c.affects_verdict), None
            )
            if crit:
                pct_str = f"{crit.pct_rows_affected:.1%}" if crit.pct_rows_affected else ""
                verdict_reason = (
                    f"Critical failure on {crit.check_id}"
                    + (f" ({pct_str} rows affected)" if pct_str else "") + "."
                )
            else:
                verdict_reason = "Critical failure detected."
    elif verdict == "WARN":
        pct_str = f"{pct_affected:.1%}"
        below = " (below 1% FAIL threshold)." if pct_affected < config.critical_pct_rows else "."
        verdict_reason = f"No structural failures; {pct_str} rows affected{below}"
    else:
        verdict_reason = "All tests passed."

    priority = {"critical": 0, "warning": 1, "info": 2}
    failing = [c for c in checks if c.status in ("FAIL", "ERROR") and c.affects_verdict]
    failing.sort(key=lambda c: (priority.get(c.severity, 3), c.check_id))
    worst_check_id = failing[0].check_id if failing else ""

    return TableVerdict(
        run_id=run_id,
        table_pair_id=table_pair_id,
        source_table=source_table,
        target_table=target_table,
        verdict=verdict,
        verdict_reason=verdict_reason,
        rows_total=rows_total,
        total_rows_affected=total_rows_affected,
        pct_rows_affected=pct_affected,
        total_value_at_risk=total_value_at_risk,
        checks_total=len(checks),
        checks_passed=n_pass,
        checks_failed=n_fail,
        checks_error=n_error,
        checks_pending_approval=n_pending,
        critical_count=n_critical,
        warning_count=n_warning,
        info_count=n_info,
        structural_failure_count=n_structural,
        recon_failed=recon_failed,
        business_rule_failed=br_failed,
        llm_failed=llm_failed,
        worst_check_id=worst_check_id,
    )


def table_verdicts_to_run_summary(
    run_id: str,
    run_timestamp: str,
    verdicts: Sequence[TableVerdict],
) -> RunSummary:
    n_total = len(verdicts)
    n_go = sum(1 for v in verdicts if v.verdict == "PASS")
    n_caveats = sum(1 for v in verdicts if v.verdict == "WARN")
    n_nogo = sum(1 for v in verdicts if v.verdict == "FAIL")

    total_affected = sum(v.total_rows_affected for v in verdicts)
    total_value = sum(v.total_value_at_risk for v in verdicts)
    total_critical = sum(v.critical_count for v in verdicts)
    total_warning = sum(v.warning_count for v in verdicts)
    total_pending = sum(v.checks_pending_approval for v in verdicts)

    if n_nogo > 0:
        overall = "FAIL"
    elif n_caveats > 0:
        overall = "WARN"
    else:
        overall = "PASS"

    if overall == "FAIL":
        fail_tables = [v for v in verdicts if v.verdict == "FAIL"]
        structural = any(v.structural_failure_count > 0 for v in fail_tables)
        issue = "critical structural issues" if structural else "critical failures"
        affected_str = f"{total_affected:,} rows"
        pending_str = (
            f" {total_pending} test{'s' if total_pending != 1 else ''} pending review."
            if total_pending > 0 else ""
        )
        headline = (
            f"FAIL: {n_nogo} of {n_total} table{'s' if n_nogo != 1 else ''} "
            f"has {issue}. "
            f"{affected_str} affected.{pending_str}"
        )
    elif overall == "WARN":
        headline = (
            f"WARN: {n_caveats} of {n_total} tables have warnings. "
            "Review before promoting."
        )
    else:
        headline = f"PASS: All {n_total} table{'s' if n_total != 1 else ''} passed validation."

    return RunSummary(
        run_id=run_id,
        run_timestamp=run_timestamp,
        overall_verdict=overall,
        tables_total=n_total,
        tables_go=n_go,
        tables_caveats=n_caveats,
        tables_nogo=n_nogo,
        total_rows_affected=total_affected,
        total_value_at_risk=total_value,
        total_critical=total_critical,
        total_warning=total_warning,
        total_pending_approval=total_pending,
        headline=headline,
    )


# ─────────────────────────────── CSV writing ─────────────────────────────────

def _bool_csv(v: bool) -> str:
    return "true" if v else "false"


def _num_csv(v: float) -> str:
    if v == int(v):
        return str(int(v))
    return str(round(v, 6))


def write_check_results_csv(path: Path, checks: list[CheckRow]) -> None:
    field_names = [f.name for f in fields(CheckRow)]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=field_names)
        w.writeheader()
        for c in checks:
            row = asdict(c)
            row["is_structural"] = _bool_csv(row["is_structural"])
            row["is_llm_generated"] = _bool_csv(row["is_llm_generated"])
            row["llm_test_approved"] = _bool_csv(row["llm_test_approved"])
            row["affects_verdict"] = _bool_csv(row["affects_verdict"])
            row["pct_rows_affected"] = _num_csv(row["pct_rows_affected"])
            row["value_at_risk"] = _num_csv(row["value_at_risk"])
            w.writerow(row)


def write_table_verdicts_csv(path: Path, verdicts: list[TableVerdict]) -> None:
    field_names = [f.name for f in fields(TableVerdict)]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=field_names)
        w.writeheader()
        for v in verdicts:
            row = asdict(v)
            row["pct_rows_affected"] = _num_csv(row["pct_rows_affected"])
            row["total_value_at_risk"] = _num_csv(row["total_value_at_risk"])
            w.writerow(row)


def write_run_summary_csv(path: Path, summary: RunSummary) -> None:
    field_names = [f.name for f in fields(RunSummary)]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=field_names)
        w.writeheader()
        row = asdict(summary)
        row["total_value_at_risk"] = _num_csv(row["total_value_at_risk"])
        w.writerow(row)


# ─────────────────────────────── HTML rendering ──────────────────────────────

_VERDICT_COLOR = {"FAIL": "#c0392b", "WARN": "#d4770c", "PASS": "#27ae60"}
_SEVERITY_COLOR = {"critical": "#c0392b", "warning": "#d4770c", "info": "#636e72"}


def _esc(s: str | None) -> str:
    if not s:
        return ""
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def render_html_report(
    checks: list[CheckRow],
    verdicts: list[TableVerdict],
    summary: RunSummary,
) -> str:
    vc = _VERDICT_COLOR.get(summary.overall_verdict, "#636e72")

    # Sort verdicts: FAIL first, then WARN, then PASS
    order = {"FAIL": 0, "WARN": 1, "PASS": 2}
    sorted_verdicts = sorted(verdicts, key=lambda v: (order.get(v.verdict, 3), v.table_pair_id))

    # Group checks by table
    checks_by_table: dict[str, list[CheckRow]] = {}
    for c in checks:
        checks_by_table.setdefault(c.table_pair_id, []).append(c)

    def badge(verdict: str) -> str:
        color = _VERDICT_COLOR.get(verdict, "#636e72")
        label = verdict.replace("_", " ")
        return (f'<span style="background:{color};color:#fff;padding:2px 8px;'
                f'border-radius:4px;font-size:0.8em;font-weight:600">{_esc(label)}</span>')

    def sev_badge(severity: str) -> str:
        color = _SEVERITY_COLOR.get(severity, "#636e72")
        return (f'<span style="background:{color};color:#fff;padding:1px 6px;'
                f'border-radius:3px;font-size:0.75em;font-weight:600">{_esc(severity.upper())}</span>')

    def status_icon(status: str) -> str:
        icons = {"PASS": "&#10003;", "FAIL": "&#10007;", "ERROR": "&#9888;",
                 "PENDING_APPROVAL": "&#63;"}
        colors = {"PASS": "#27ae60", "FAIL": "#c0392b", "ERROR": "#e67e22",
                  "PENDING_APPROVAL": "#8e44ad"}
        ic = icons.get(status, "&#9679;")
        col = colors.get(status, "#636e72")
        return f'<span style="color:{col};font-weight:bold">{ic}</span>'

    # Table scorecard rows
    scorecard_rows = ""
    for v in sorted_verdicts:
        pct_str = f"{v.pct_rows_affected:.1%}" if v.pct_rows_affected > 0 else "0%"
        val_str = f"${v.total_value_at_risk:,.0f}" if v.total_value_at_risk > 0 else "—"
        scorecard_rows += f"""
        <tr onclick="togglePanel('tp_{_esc(v.table_pair_id)}')" style="cursor:pointer">
          <td style="font-weight:600">{_esc(v.table_pair_id)}</td>
          <td>{badge(v.verdict)}</td>
          <td>{v.total_rows_affected:,} ({pct_str})</td>
          <td>{val_str}</td>
          <td>{v.checks_failed + v.checks_error}</td>
          <td><span style="color:#c0392b">{v.critical_count}</span> /
              <span style="color:#d4770c">{v.warning_count}</span> /
              <span style="color:#636e72">{v.info_count}</span></td>
          <td>
            <small style="color:#636e72">{_esc(v.source_table)}</small><br>
            <small>&#8595; {_esc(v.target_table)}</small>
          </td>
        </tr>"""

    # Per-table detail panels
    table_panels = ""
    for v in sorted_verdicts:
        table_checks = checks_by_table.get(v.table_pair_id, [])

        # Group by family
        families: dict[str, list[CheckRow]] = {}
        for c in table_checks:
            families.setdefault(c.check_family, []).append(c)

        family_html = ""
        family_order = ["reconciliation", "business_rule", "llm_generated"]
        for fam in family_order:
            fam_checks = families.get(fam, [])
            if not fam_checks:
                continue
            fam_fail = sum(1 for c in fam_checks if c.status in ("FAIL", "ERROR"))
            fam_pass = sum(1 for c in fam_checks if c.status == "PASS")
            fam_pending = sum(1 for c in fam_checks if c.status == "PENDING_APPROVAL")
            fam_label = fam.replace("_", " ").title()
            fam_id = f"fam_{_esc(v.table_pair_id)}_{fam}"
            fam_summary_parts = []
            if fam_fail:
                fam_summary_parts.append(f'<span style="color:#c0392b">{fam_fail} failed</span>')
            if fam_pass:
                fam_summary_parts.append(f'<span style="color:#27ae60">{fam_pass} passed</span>')
            if fam_pending:
                fam_summary_parts.append(
                    f'<span style="color:#8e44ad">{fam_pending} pending</span>'
                )
            fam_summary = " &bull; ".join(fam_summary_parts)

            check_rows_html = ""
            for c in fam_checks:
                cid = f"chk_{_esc(c.check_id)}"
                llm_badge = ""
                if c.is_llm_generated and not c.llm_test_approved:
                    llm_badge = (' <span style="background:#8e44ad;color:#fff;'
                                 'padding:1px 5px;border-radius:3px;font-size:0.7em">'
                                 'AI PENDING</span>')
                elif c.is_llm_generated:
                    llm_badge = (' <span style="background:#9b59b6;color:#fff;'
                                 'padding:1px 5px;border-radius:3px;font-size:0.7em">'
                                 'AI</span>')

                keys_html = ""
                if c.example_failing_keys:
                    keys_html = (
                        f'<p style="margin:4px 0 0"><strong>Example failing keys:</strong> '
                        f'<code style="font-size:0.85em">{_esc(c.example_failing_keys)}</code></p>'
                    )

                sql_html = ""
                if c.sql_source_path or c.sql_target_path:
                    srcs = (f'<li>Source: <code>{_esc(c.sql_source_path)}</code></li>'
                            if c.sql_source_path else "")
                    tgts = (f'<li>Target: <code>{_esc(c.sql_target_path)}</code></li>'
                            if c.sql_target_path else "")
                    sql_html = f"""
                    <details style="margin-top:6px">
                      <summary style="cursor:pointer;color:#636e72;font-size:0.85em">
                        For engineers: SQL references
                      </summary>
                      <ul style="margin:4px 0;font-size:0.85em">{srcs}{tgts}</ul>
                    </details>"""

                rca_html = ""
                if c.rca_summary:
                    rca_html = (
                        f'<p style="margin:4px 0 0;color:#555">'
                        f'<strong>Root cause:</strong> {_esc(c.rca_summary)}</p>'
                    )

                check_rows_html += f"""
                <div style="border-left:3px solid {_SEVERITY_COLOR.get(c.severity,'#ccc')};
                            margin:4px 0;padding:4px 8px;background:#fafafa">
                  <div onclick="togglePanel('{cid}')" style="cursor:pointer;display:flex;
                       align-items:baseline;gap:8px">
                    {status_icon(c.status)}
                    {sev_badge(c.severity)}
                    <strong style="font-size:0.9em">{_esc(c.check_id)}</strong>
                    {llm_badge}
                    <span style="color:#555;font-size:0.85em;margin-left:4px">
                      {_esc(c.impact_summary)}
                    </span>
                  </div>
                  <div id="{cid}" style="display:none;margin-top:6px;padding-left:8px;
                       border-top:1px solid #eee;padding-top:6px">
                    <p style="margin:0 0 4px;color:#333">{_esc(c.check_description)}</p>
                    {rca_html}
                    {keys_html}
                    {sql_html}
                  </div>
                </div>"""

            family_html += f"""
            <div style="margin:8px 0">
              <div onclick="togglePanel('{fam_id}')" style="cursor:pointer;
                   background:#f0f0f0;padding:6px 10px;border-radius:4px;
                   display:flex;justify-content:space-between">
                <strong>{_esc(fam_label)}</strong>
                <small style="color:#555">{fam_summary}</small>
              </div>
              <div id="{fam_id}" style="padding:4px 0">
                {check_rows_html}
              </div>
            </div>"""

        vc_table = _VERDICT_COLOR.get(v.verdict, "#636e72")
        table_panels += f"""
        <div style="border:1px solid #ddd;border-radius:6px;margin:8px 0">
          <div id="tp_{_esc(v.table_pair_id)}"
               style="display:none;padding:12px 16px;border-top:1px solid #eee">
            <p style="margin:0 0 4px;color:#555">{_esc(v.verdict_reason)}</p>
            <div style="display:flex;gap:16px;flex-wrap:wrap;font-size:0.85em;
                 color:#555;margin-bottom:8px">
              <span>Rows total: <strong>{v.rows_total:,}</strong></span>
              <span>Rows affected: <strong>{v.total_rows_affected:,}</strong></span>
              <span>Value at risk: <strong>${v.total_value_at_risk:,.0f}</strong></span>
              <span>Checks: <strong>{v.checks_total}</strong>
                ({v.checks_passed} pass / {v.checks_failed} fail
                {f'/ {v.checks_pending_approval} pending' if v.checks_pending_approval else ''})</span>
            </div>
            {family_html}
          </div>
          <div onclick="togglePanel('tp_{_esc(v.table_pair_id)}')"
               style="cursor:pointer;padding:10px 16px;display:flex;
               align-items:center;gap:12px;background:#fff;border-radius:6px">
            {badge(v.verdict)}
            <strong>{_esc(v.table_pair_id)}</strong>
            <span style="color:#555;font-size:0.85em">{_esc(v.verdict_reason)}</span>
          </div>
        </div>"""

    stats_items = [
        f"<strong>{summary.tables_total}</strong> tables",
        f'<span style="color:{_VERDICT_COLOR["FAIL"]}">'
        f'<strong>{summary.tables_nogo}</strong> FAIL</span>' if summary.tables_nogo else "",
        f'<span style="color:{_VERDICT_COLOR["WARN"]}">'
        f'<strong>{summary.tables_caveats}</strong> WARN</span>' if summary.tables_caveats else "",
        f'<span style="color:{_VERDICT_COLOR["PASS"]}">'
        f'<strong>{summary.tables_go}</strong> PASS</span>' if summary.tables_go else "",
        f"<strong>{summary.total_rows_affected:,}</strong> rows affected",
        (f'<span style="color:#8e44ad"><strong>{summary.total_pending_approval}</strong>'
         f' AI test{"s" if summary.total_pending_approval != 1 else ""} pending</span>'
         if summary.total_pending_approval else ""),
    ]
    stats_bar = " &nbsp;|&nbsp; ".join(s for s in stats_items if s)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DQ Report: {_esc(summary.run_id)}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
        background:#f5f6fa;color:#2d3436;font-size:14px;line-height:1.5}}
  .banner{{background:{vc};color:#fff;padding:16px 24px}}
  .banner h1{{font-size:1.4em;font-weight:700}}
  .banner p{{font-size:0.95em;opacity:.9;margin-top:4px}}
  .stats{{background:#fff;padding:10px 24px;border-bottom:1px solid #ddd;
          font-size:0.9em;color:#555}}
  .container{{max-width:1100px;margin:0 auto;padding:16px}}
  h2{{font-size:1em;text-transform:uppercase;letter-spacing:.06em;
      color:#636e72;margin:16px 0 8px}}
  table{{width:100%;border-collapse:collapse;background:#fff;
         border-radius:6px;overflow:hidden;
         box-shadow:0 1px 3px rgba(0,0,0,.08)}}
  th{{background:#f8f9fa;text-align:left;padding:8px 12px;
      font-size:0.8em;text-transform:uppercase;letter-spacing:.04em;
      color:#636e72;border-bottom:2px solid #ddd}}
  td{{padding:8px 12px;border-bottom:1px solid #f0f0f0;vertical-align:middle}}
  tr:hover td{{background:#f8f9fa}}
  .panels{{margin-top:16px}}
  code{{font-family:monospace;background:#f0f0f0;padding:1px 4px;border-radius:2px}}
</style>
</head>
<body>
<div class="banner">
  <h1>{_esc(summary.overall_verdict.replace("_"," "))} &mdash; {_esc(summary.headline)}</h1>
  <p>Run ID: {_esc(summary.run_id)} &nbsp;&bull;&nbsp; {_esc(summary.run_timestamp)}</p>
</div>
<div class="stats">{stats_bar}</div>
<div class="container">
  <h2>Table Scorecard</h2>
  <table>
    <thead><tr>
      <th>Table</th><th>Verdict</th><th>Rows Affected</th>
      <th>$ at Risk</th><th>Failed Checks</th>
      <th>Crit / Warn / Info</th><th>Source &rarr; Target</th>
    </tr></thead>
    <tbody>{scorecard_rows}</tbody>
  </table>
  <h2>Table Details</h2>
  <div class="panels">{table_panels}</div>
</div>
<script>
function togglePanel(id){{
  var el=document.getElementById(id);
  if(el)el.style.display=el.style.display==='none'?'block':'none';
}}
</script>
</body>
</html>"""


# ─────────────────────────────── Excel writer ────────────────────────────────

_HEADER_FILL = PatternFill("solid", fgColor="2C3E50")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=10)
_VERDICT_FILLS = {
    "FAIL": PatternFill("solid", fgColor="FADBD8"),
    "WARN": PatternFill("solid", fgColor="FDEBD0"),
    "PASS": PatternFill("solid", fgColor="D5F5E3"),
}
_SEVERITY_FILLS = {
    "critical": PatternFill("solid", fgColor="FADBD8"),
    "warning":  PatternFill("solid", fgColor="FDEBD0"),
    "info":     PatternFill("solid", fgColor="EBF5FB"),
}
_STATUS_FILLS = {
    "FAIL":             PatternFill("solid", fgColor="FADBD8"),
    "ERROR":            PatternFill("solid", fgColor="FADBD8"),
    "PENDING_APPROVAL": PatternFill("solid", fgColor="E8DAEF"),
    "PASS":             PatternFill("solid", fgColor="D5F5E3"),
}


def _write_sheet(
    ws,
    headers: list[str],
    rows: list[dict],
    color_col: str | None = None,
    color_map: dict | None = None,
) -> None:
    # Header row
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Data rows
    color_col_idx = headers.index(color_col) + 1 if color_col and color_col in headers else None
    for row_idx, row in enumerate(rows, 2):
        fill = None
        if color_col_idx and color_map:
            val = str(row.get(color_col, ""))
            fill = color_map.get(val)
        for col_idx, h in enumerate(headers, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=row.get(h, ""))
            cell.alignment = Alignment(wrap_text=False, vertical="center")
            if fill:
                cell.fill = fill

    # Auto-width (cap at 60)
    for col_idx, h in enumerate(headers, 1):
        max_len = len(h)
        for row_idx in range(2, ws.max_row + 1):
            v = ws.cell(row=row_idx, column=col_idx).value
            if v:
                max_len = max(max_len, min(len(str(v)), 60))
        ws.column_dimensions[get_column_letter(col_idx)].width = max_len + 2

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def write_tiered_excel(
    path: Path,
    checks: list[CheckRow],
    verdicts: list[TableVerdict],
    summary: RunSummary,
) -> None:
    wb = openpyxl.Workbook()

    # ── Sheet 1: Run Summary ────────────────────────────────────────────────
    # (field_name, display_name)
    _RUN_COLS: list[tuple[str, str]] = [
        ("run_id",               "Run ID"),
        ("run_timestamp",        "Timestamp"),
        ("overall_verdict",      "Verdict"),
        ("tables_total",         "Tables Total"),
        ("tables_go",            "Tables PASS"),
        ("tables_caveats",       "Tables WARN"),
        ("tables_nogo",          "Tables FAIL"),
        ("total_rows_affected",  "Rows Affected"),
        ("total_critical",       "Critical"),
        ("total_warning",        "Warning"),
        ("total_pending_approval", "Pending"),
        ("headline",             "Headline"),
    ]
    ws_run = wb.active
    ws_run.title = "Run Summary"
    run_data = asdict(summary)
    _write_sheet(
        ws_run,
        [d for _, d in _RUN_COLS],
        [{d: run_data[f] for f, d in _RUN_COLS}],
        color_col="Verdict",
        color_map=_VERDICT_FILLS,
    )

    # ── Sheet 2: Table Verdicts ─────────────────────────────────────────────
    _TV_COLS: list[tuple[str, str]] = [
        ("run_id",                  "Run ID"),
        ("table_pair_id",           "Table"),
        ("source_table",            "Source"),
        ("target_table",            "Target"),
        ("verdict",                 "Verdict"),
        ("verdict_reason",          "Reason"),
        ("rows_total",              "Rows Total"),
        ("total_rows_affected",     "Rows Affected"),
        ("pct_rows_affected",       "% Affected"),
        ("checks_total",            "Test Cases Total"),
        ("checks_passed",           "Passed"),
        ("checks_failed",           "Failed"),
        ("checks_error",            "Errors"),
        ("checks_pending_approval", "Pending"),
        ("critical_count",          "Critical"),
        ("warning_count",           "Warning"),
        ("info_count",              "Info"),
        ("structural_failure_count","Structural Failures"),
        ("worst_check_id",          "Worst Test Case"),
    ]
    ws_tv = wb.create_sheet("Table Verdicts")
    tv_rows = [
        {d: asdict(v)[f] for f, d in _TV_COLS}
        for v in verdicts
    ]
    _write_sheet(ws_tv, [d for _, d in _TV_COLS], tv_rows,
                 color_col="Verdict", color_map=_VERDICT_FILLS)

    # ── Sheet 3: Test Cases ─────────────────────────────────────────────────
    _TC_COLS: list[tuple[str, str]] = [
        ("table_pair_id",      "Table"),
        ("check_id",           "Test Case ID"),
        ("check_type",         "Type"),
        ("check_description",  "Description"),
        ("column_scope",       "Columns"),
        ("status",             "Status"),
        ("severity",           "Severity"),
        ("is_structural",      "Structural"),
        ("rows_affected",      "Rows Affected"),
        ("rows_total",         "Rows Total"),
        ("pct_rows_affected",  "% Affected"),
        ("impact_summary",     "Impact"),
        ("rca_summary",        "Root Cause"),
        ("affects_verdict",    "Affects Verdict"),
        ("example_failing_keys", "Example Keys"),
        ("sql_source_path",    "SQL (Source)"),
        ("sql_target_path",    "SQL (Target)"),
    ]
    ws_cr = wb.create_sheet("Test Cases")
    cr_rows = []
    for c in checks:
        d = asdict(c)
        d["is_structural"] = "Yes" if d["is_structural"] else "No"
        d["affects_verdict"] = "Yes" if d["affects_verdict"] else "No"
        cr_rows.append({disp: d[f] for f, disp in _TC_COLS})
    _write_sheet(ws_cr, [d for _, d in _TC_COLS], cr_rows,
                 color_col="Status", color_map=_STATUS_FILLS)

    wb.save(path)


# ─────────────────────────────── main entry point ────────────────────────────

def write_tiered_reports(
    run_id: str,
    run_timestamp: str,
    output_dir: Path,
    table_outputs: list[dict[str, Any]],
    approval_proposals: list[dict[str, Any]],
    config: ReportingConfig | None = None,
) -> dict[str, Path]:
    """
    Build and write check_results.csv, table_verdicts.csv, run_summary.csv,
    and report.html from engine table_outputs + approval_proposals.

    Returns mapping of {artifact_name: path}.
    """
    if config is None:
        config = load_reporting_config()

    # Build lookup structures
    pending_llm_ids: set[str] = set()
    approved_llm_ids: set[str] = set()
    pending_proposals_by_pair: dict[str, list[dict[str, Any]]] = {}
    for p in approval_proposals:
        if p.get("category") == "business_rule" and p.get("status", "PENDING") in ("PENDING", "REVIEW"):
            if not p.get("blocking", True):
                pair_id = p.get("pair_id", "")
                subject = p.get("subject", "")
                pending_llm_ids.add(f"{pair_id}__{subject}")
                pending_proposals_by_pair.setdefault(pair_id, []).append(p)

    # Build RCA map: rule_id -> conclusion text
    rca_map: dict[str, str] = {}
    for to in table_outputs:
        for r in to.get("rca") or []:
            rid = r.get("rule_id", "")
            conclusion = r.get("conclusion", "")
            if rid and conclusion:
                rca_map[rid] = str(conclusion)

    all_checks: list[CheckRow] = []

    for to in table_outputs:
        summary = to.get("summary") or {}
        pair_id = summary.get("pair_id", "")
        pair_meta = {
            "source_table": summary.get("source_table") or "",
            "target_table": summary.get("target_table") or "",
        }

        for result in to.get("results") or []:
            check = engine_result_to_check(
                result, run_id, pair_meta, rca_map,
                pending_llm_ids, approved_llm_ids, config,
            )
            if check is not None:
                all_checks.append(check)

        # Add stubs for pending non-blocking LLM proposals
        for p in pending_proposals_by_pair.get(pair_id, []):
            stub = pending_proposal_to_check(p, run_id, pair_meta, config)
            if stub is not None:
                all_checks.append(stub)

    # Aggregate to table verdicts
    table_ids = list(dict.fromkeys(c.table_pair_id for c in all_checks))
    verdicts: list[TableVerdict] = []
    for tid in table_ids:
        table_checks = [c for c in all_checks if c.table_pair_id == tid]
        src = table_checks[0].source_table if table_checks else ""
        tgt = table_checks[0].target_table if table_checks else ""
        verdicts.append(
            checks_to_table_verdict(table_checks, run_id, tid, src, tgt, config)
        )

    run_summary = table_verdicts_to_run_summary(run_id, run_timestamp, verdicts)

    # Write CSVs
    out: dict[str, Path] = {}
    out["check_results"] = output_dir / "check_results.csv"
    out["table_verdicts"] = output_dir / "table_verdicts.csv"
    out["run_summary"] = output_dir / "run_summary.csv"
    out["report_html"] = output_dir / "report.html"

    write_check_results_csv(out["check_results"], all_checks)
    write_table_verdicts_csv(out["table_verdicts"], verdicts)
    write_run_summary_csv(out["run_summary"], run_summary)
    out["report_html"].write_text(
        render_html_report(all_checks, verdicts, run_summary), encoding="utf-8"
    )
    out["tiered_report_xlsx"] = output_dir / "tiered_report.xlsx"
    write_tiered_excel(out["tiered_report_xlsx"], all_checks, verdicts, run_summary)

    return out
