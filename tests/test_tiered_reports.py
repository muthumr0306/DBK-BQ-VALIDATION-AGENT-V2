"""
Unit and golden tests for the tiered reporting layer.

Run with:  python -m pytest tests/test_tiered_reports.py -v
"""
from __future__ import annotations

import csv
from dataclasses import fields
from pathlib import Path

import pytest

from dq_agent.tiered_reports import (
    CheckRow,
    ReportingConfig,
    TableVerdict,
    checks_to_table_verdict,
    compute_check_severity,
    compute_is_structural,
    table_verdicts_to_run_summary,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ─────────────────────────────── helpers ──────────────────────────────────────

def _make_check(
    check_id: str = "tbl__chk1",
    table_pair_id: str = "tbl",
    check_type: str = "row_count",
    status: str = "PASS",
    severity: str = "info",
    is_structural: bool = False,
    rows_affected: int = 0,
    rows_total: int = 1000,
    pct_rows_affected: float = 0.0,
    value_at_risk: float = 0.0,
    check_family: str = "reconciliation",
    is_llm_generated: bool = False,
    llm_test_approved: bool = False,
    affects_verdict: bool = True,
) -> CheckRow:
    return CheckRow(
        run_id="run_test",
        table_pair_id=table_pair_id,
        source_table="src.tbl",
        target_table="tgt.tbl",
        check_id=check_id,
        check_family=check_family,
        check_type=check_type,
        check_description="test check",
        column_scope="",
        status=status,
        severity=severity,
        is_structural=is_structural,
        rows_affected=rows_affected,
        rows_total=rows_total,
        pct_rows_affected=pct_rows_affected,
        value_at_risk=value_at_risk,
        value_metric="",
        impact_summary="",
        rca_summary="",
        is_llm_generated=is_llm_generated,
        llm_test_approved=llm_test_approved,
        affects_verdict=affects_verdict,
        example_failing_keys="",
        sql_source_path="",
        sql_target_path="",
    )


def _cfg(**overrides) -> ReportingConfig:
    return ReportingConfig(
        critical_pct_rows=overrides.get("critical_pct_rows", 0.01),
        critical_value_at_risk=overrides.get("critical_value_at_risk", 100_000),
        structural_check_types=overrides.get(
            "structural_check_types",
            ["pk_in_source_not_target", "pk_in_target_not_source", "duplicate_keys",
             "grain_breakage", "target_key_unique"],
        ),
    )


# ─────────────────────────────── compute_check_severity ───────────────────────

def test_pass_is_info():
    assert compute_check_severity(False, "PASS", 0.5, 999_999, _cfg()) == "info"


def test_pending_is_info():
    assert compute_check_severity(False, "PENDING_APPROVAL", 0.5, 999_999, _cfg()) == "info"


def test_error_is_critical():
    assert compute_check_severity(False, "ERROR", 0.0, 0.0, _cfg()) == "critical"


def test_structural_fail_is_critical():
    assert compute_check_severity(True, "FAIL", 0.0, 0.0, _cfg()) == "critical"


def test_pct_just_under_threshold_is_warning():
    cfg = _cfg(critical_pct_rows=0.01)
    # 0.99% → warning
    assert compute_check_severity(False, "FAIL", 0.0099, 0.0, cfg) == "warning"


def test_pct_at_threshold_is_critical():
    cfg = _cfg(critical_pct_rows=0.01)
    # exactly 1% → critical
    assert compute_check_severity(False, "FAIL", 0.01, 0.0, cfg) == "critical"


def test_value_at_risk_threshold_crossing():
    cfg = _cfg(critical_value_at_risk=100_000)
    assert compute_check_severity(False, "FAIL", 0.0, 99_999.99, cfg) == "warning"
    assert compute_check_severity(False, "FAIL", 0.0, 100_000.0, cfg) == "critical"


# ─────────────────────────────── compute_is_structural ────────────────────────

def test_structural_types_detected():
    cfg = _cfg()
    for ctype in ["pk_in_source_not_target", "pk_in_target_not_source",
                  "duplicate_keys", "grain_breakage", "target_key_unique"]:
        assert compute_is_structural(ctype, cfg) is True


def test_non_structural_type():
    assert compute_is_structural("row_count", _cfg()) is False
    assert compute_is_structural("null_count", _cfg()) is False


# ─────────────────────────────── structural_always_nogo ───────────────────────

def test_structural_always_nogo():
    """A structural FAIL must produce FAIL regardless of magnitude."""
    cfg = _cfg()
    checks = [
        _make_check("c1", status="FAIL", severity="critical",
                    check_type="pk_in_source_not_target", is_structural=True,
                    rows_affected=1, rows_total=1_000_000, pct_rows_affected=0.000001),
    ]
    v = checks_to_table_verdict(checks, "run_x", "t", "src.t", "tgt.t", cfg)
    assert v.verdict == "FAIL"
    assert v.structural_failure_count == 1


def test_structural_nogo_even_zero_rows():
    """Even with 0 rows affected, structural failure → FAIL."""
    cfg = _cfg()
    checks = [
        _make_check("c1", status="FAIL", severity="critical",
                    check_type="duplicate_keys", is_structural=True,
                    rows_affected=0, rows_total=100),
    ]
    v = checks_to_table_verdict(checks, "run_x", "t", "s", "t", cfg)
    assert v.verdict == "FAIL"


# ─────────────────────────────── threshold_crossing ───────────────────────────

def test_threshold_crossing_just_under_is_caveats():
    """0.99% with no structural failures → WARN."""
    cfg = _cfg(critical_pct_rows=0.01)
    checks = [
        _make_check("c1", status="FAIL", severity="warning", check_type="null_count",
                    rows_affected=99, rows_total=10_000, pct_rows_affected=0.0099),
    ]
    v = checks_to_table_verdict(checks, "run_x", "t", "s", "t", cfg)
    assert v.verdict == "WARN"
    assert v.structural_failure_count == 0


def test_threshold_crossing_at_and_over_is_nogo():
    """1% exactly → FAIL (critical severity)."""
    cfg = _cfg(critical_pct_rows=0.01)
    checks = [
        _make_check("c1", status="FAIL", severity="critical", check_type="null_count",
                    rows_affected=100, rows_total=10_000, pct_rows_affected=0.01,
                    value_at_risk=0.0),
    ]
    v = checks_to_table_verdict(checks, "run_x", "t", "s", "t", cfg)
    assert v.verdict == "FAIL"


# ─────────────────────────────── pending_llm_excluded ─────────────────────────

def test_pending_llm_excluded_from_verdict():
    """PENDING_APPROVAL checks must not affect the table verdict."""
    cfg = _cfg()
    checks = [
        _make_check("c1", status="PASS", severity="info"),
        _make_check("tbl__llm1", table_pair_id="tbl",
                    status="PENDING_APPROVAL", severity="info",
                    is_llm_generated=True, llm_test_approved=False,
                    affects_verdict=False),
    ]
    v = checks_to_table_verdict(checks, "run_x", "tbl", "s", "t", cfg)
    assert v.verdict == "PASS"
    assert v.checks_pending_approval == 1
    assert v.checks_failed == 0


def test_pending_counted_separately_from_pass_fail():
    cfg = _cfg()
    checks = [
        _make_check("c1", status="PASS"),
        _make_check("c2", status="PENDING_APPROVAL", severity="info",
                    affects_verdict=False),
        _make_check("c3", status="PENDING_APPROVAL", severity="info",
                    affects_verdict=False),
    ]
    v = checks_to_table_verdict(checks, "r", "t", "s", "t", cfg)
    assert v.checks_pending_approval == 2
    assert v.checks_passed == 1
    assert v.verdict == "PASS"


# ─────────────────────────────── all_pass_go ──────────────────────────────────

def test_all_pass_go():
    cfg = _cfg()
    checks = [_make_check(f"c{i}", status="PASS") for i in range(5)]
    v = checks_to_table_verdict(checks, "r", "t", "s", "t", cfg)
    assert v.verdict == "PASS"
    assert v.checks_failed == 0
    assert v.checks_passed == 5


# ─────────────────────────────── run summary rollup ───────────────────────────

def test_run_summary_worst_verdict_wins():
    verdicts = [
        TableVerdict("r", "t1", "s1", "t1", "PASS", "", 100, 0, 0.0, 0.0,
                     5, 5, 0, 0, 0, 0, 0, 5, 0, 0, 0, 0, ""),
        TableVerdict("r", "t2", "s2", "t2", "WARN", "", 100, 5, 0.05, 0.0,
                     5, 4, 1, 0, 0, 0, 1, 4, 0, 1, 0, 0, "t2__chk"),
        TableVerdict("r", "t3", "s3", "t3", "FAIL", "", 100, 100, 1.0, 0.0,
                     5, 2, 3, 0, 0, 3, 0, 2, 1, 2, 0, 0, "t3__chk"),
    ]
    rs = table_verdicts_to_run_summary("r", "2026-06-25T00:00:00Z", verdicts)
    assert rs.overall_verdict == "FAIL"
    assert rs.tables_go == 1
    assert rs.tables_caveats == 1
    assert rs.tables_nogo == 1
    assert rs.tables_total == 3


def test_run_summary_all_go():
    verdicts = [
        TableVerdict("r", f"t{i}", "s", "t", "PASS", "", 100, 0, 0.0, 0.0,
                     3, 3, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0, "")
        for i in range(4)
    ]
    rs = table_verdicts_to_run_summary("r", "2026-06-25T00:00:00Z", verdicts)
    assert rs.overall_verdict == "PASS"
    assert rs.tables_go == 4
    assert rs.tables_nogo == 0


# ─────────────────────────────── golden test ──────────────────────────────────

def _read_fixture_checks() -> list[dict]:
    path = FIXTURES / "check_results.csv"
    if not path.exists():
        pytest.skip(f"fixture not found: {path}")
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _read_fixture_verdicts() -> list[dict]:
    path = FIXTURES / "table_verdicts.csv"
    if not path.exists():
        pytest.skip(f"fixture not found: {path}")
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _read_fixture_summary() -> dict:
    path = FIXTURES / "run_summary.csv"
    if not path.exists():
        pytest.skip(f"fixture not found: {path}")
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    return rows[0]


def _fixture_row_to_check(row: dict) -> CheckRow:
    """Reconstruct a CheckRow from the fixture CSV (all fields are strings)."""
    def _b(s): return s.strip().lower() == "true"
    def _i(s):
        try: return int(s)
        except (ValueError, TypeError): return 0
    def _f(s):
        try: return float(s)
        except (ValueError, TypeError): return 0.0

    return CheckRow(
        run_id=row["run_id"],
        table_pair_id=row["table_pair_id"],
        source_table=row["source_table"],
        target_table=row["target_table"],
        check_id=row["check_id"],
        check_family=row["check_family"],
        check_type=row["check_type"],
        check_description=row["check_description"],
        column_scope=row["column_scope"],
        status=row["status"],
        severity=row["severity"],
        is_structural=_b(row["is_structural"]),
        rows_affected=_i(row["rows_affected"]),
        rows_total=_i(row["rows_total"]),
        pct_rows_affected=_f(row["pct_rows_affected"]),
        value_at_risk=_f(row["value_at_risk"]),
        value_metric=row["value_metric"],
        impact_summary=row["impact_summary"],
        rca_summary=row["rca_summary"],
        is_llm_generated=_b(row["is_llm_generated"]),
        llm_test_approved=_b(row["llm_test_approved"]),
        affects_verdict=_b(row["affects_verdict"]),
        example_failing_keys=row["example_failing_keys"],
        sql_source_path=row["sql_source_path"],
        sql_target_path=row["sql_target_path"],
    )


def test_golden_aggregation():
    """
    Feed fixture check_results.csv through aggregation, compare result
    counts/verdicts to fixture table_verdicts.csv and run_summary.csv.

    Compares numeric counts and verdicts only — not free-text fields like
    verdict_reason / headline, which are generated deterministically but
    may differ from sample text.
    """
    cfg = _cfg()
    raw_checks = _read_fixture_checks()
    checks = [_fixture_row_to_check(r) for r in raw_checks]

    expected_verdicts = {r["table_pair_id"]: r for r in _read_fixture_verdicts()}
    expected_summary = _read_fixture_summary()

    # Group by table and aggregate
    table_ids = list(dict.fromkeys(c.table_pair_id for c in checks))
    verdicts = []
    for tid in table_ids:
        tc = [c for c in checks if c.table_pair_id == tid]
        src = tc[0].source_table
        tgt = tc[0].target_table
        v = checks_to_table_verdict(tc, "run_2026_06_25", tid, src, tgt, cfg)
        verdicts.append(v)

    # Assert per-table verdicts
    for v in verdicts:
        ev = expected_verdicts.get(v.table_pair_id)
        if ev is None:
            continue  # extra tables in fixture not listed — skip
        assert v.verdict == ev["verdict"], (
            f"{v.table_pair_id}: verdict {v.verdict!r} != {ev['verdict']!r}"
        )
        assert v.checks_total == int(ev["checks_total"]), (
            f"{v.table_pair_id}: checks_total {v.checks_total} != {ev['checks_total']}"
        )
        assert v.checks_passed == int(ev["checks_passed"]), (
            f"{v.table_pair_id}: checks_passed {v.checks_passed} != {ev['checks_passed']}"
        )
        assert v.checks_failed == int(ev["checks_failed"]), (
            f"{v.table_pair_id}: checks_failed {v.checks_failed} != {ev['checks_failed']}"
        )
        assert v.checks_pending_approval == int(ev["checks_pending_approval"]), (
            f"{v.table_pair_id}: pending {v.checks_pending_approval} != {ev['checks_pending_approval']}"
        )
        assert v.structural_failure_count == int(ev["structural_failure_count"]), (
            f"{v.table_pair_id}: structural_failure_count mismatch"
        )
        assert v.critical_count == int(ev["critical_count"]), (
            f"{v.table_pair_id}: critical_count {v.critical_count} != {ev['critical_count']}"
        )
        assert v.warning_count == int(ev["warning_count"]), (
            f"{v.table_pair_id}: warning_count {v.warning_count} != {ev['warning_count']}"
        )
        assert v.recon_failed == int(ev["recon_failed"]), (
            f"{v.table_pair_id}: recon_failed {v.recon_failed} != {ev['recon_failed']}"
        )
        assert v.business_rule_failed == int(ev["business_rule_failed"]), (
            f"{v.table_pair_id}: business_rule_failed {v.business_rule_failed} != {ev['business_rule_failed']}"
        )

    # Assert run summary
    run_summary = table_verdicts_to_run_summary(
        "run_2026_06_25", "2026-06-25T09:30:00Z", verdicts
    )
    assert run_summary.overall_verdict == expected_summary["overall_verdict"]
    assert run_summary.tables_total == int(expected_summary["tables_total"])
    assert run_summary.tables_go == int(expected_summary["tables_go"])
    assert run_summary.tables_caveats == int(expected_summary["tables_caveats"])
    assert run_summary.tables_nogo == int(expected_summary["tables_nogo"])
    assert run_summary.total_rows_affected == int(expected_summary["total_rows_affected"])
    assert run_summary.total_pending_approval == int(expected_summary["total_pending_approval"])
