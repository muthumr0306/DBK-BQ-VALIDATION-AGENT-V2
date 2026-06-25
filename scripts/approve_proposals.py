#!/usr/bin/env python3
"""
Auto-approve LLM inference proposals and write them into config files.

Usage:
    python scripts/approve_proposals.py                  # uses latest run
    python scripts/approve_proposals.py --dry-run        # preview only
    python scripts/approve_proposals.py path/to/approval_proposals.json

Threshold rationale (derived from observed score distributions):

  column_mapping
    Score alone is not reliable — "store_id" -> "store_number" scores only 0.58
    because they share few tokens, yet is clearly correct.  The margin over the
    2nd candidate is the real signal: if the winner is 0.15+ ahead, it's the
    right mapping.
    Rule: score >= 0.50  AND  margin_over_2nd >= 0.15  -> auto-approve

  measure
    Scores are composite (name + type + profile).  0.70+ with a resolved
    source_expression is safe.  If source_expression is null the score is 0.0
    and the measure must wait for column mappings to be resolved first.
    Rule: score >= 0.70  AND  no metadata_errors  -> auto-approve

  primary_key
    NEVER auto-approved.  The agent uses column cardinality to guess the PK,
    but low-cardinality columns (e.g. sku_id with 10 distinct values in 1200
    rows) get incorrectly proposed.  Composite PKs require business knowledge.
    The script prints the proposal as a manual-action item instead.
"""

import argparse
import csv
import io
import json
import sys
from pathlib import Path

# ── thresholds ────────────────────────────────────────────────────────────────
COL_MAP_MIN_SCORE  = 0.50   # absolute floor
COL_MAP_MIN_MARGIN = 0.15   # gap over 2nd-best candidate
MEASURE_MIN_SCORE  = 0.70   # composite score threshold

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT               = Path(__file__).parent.parent
OUTPUTS_DIR        = ROOT / "tests" / "outputs"
COL_MAPPINGS_CSV   = ROOT / "tests" / "inputs" / "column_mappings.csv"
MEASURES_YAML      = ROOT / "tests" / "inputs" / "measures.yaml"
BUSINESS_CTX_YAML  = ROOT / "inputs" / "business_context.yaml"


# ── helpers ───────────────────────────────────────────────────────────────────

def find_latest_proposals() -> Path:
    candidates = sorted(OUTPUTS_DIR.glob("*/approval_proposals.json"))
    if not candidates:
        raise FileNotFoundError(f"No approval_proposals.json found under {OUTPUTS_DIR}")
    return candidates[-1]


def parse_evidence_scores(evidence_str: str) -> tuple[float, float]:
    """Return (top_score, margin_over_2nd) from a JSON evidence string."""
    try:
        ev = json.loads(evidence_str)
        if isinstance(ev, list):
            scores = sorted([float(e.get("score", 0)) for e in ev], reverse=True)
            top    = scores[0] if scores else 0.0
            second = scores[1] if len(scores) > 1 else 0.0
            return top, top - second
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return 0.0, 0.0


def should_approve_col_map(p: dict) -> tuple[bool, str]:
    score, margin = parse_evidence_scores(p.get("evidence", "[]"))
    if score < COL_MAP_MIN_SCORE:
        return False, f"score {score:.3f} < {COL_MAP_MIN_SCORE}"
    if margin < COL_MAP_MIN_MARGIN:
        return False, f"margin {margin:.3f} < {COL_MAP_MIN_MARGIN} (ambiguous)"
    return True, f"score={score:.3f}, margin={margin:.3f}"


def should_approve_measure(p: dict) -> tuple[bool, str]:
    score = float(p.get("confidence", 0))
    try:
        body   = json.loads(p.get("proposal", "{}"))
        errors = body.get("metadata_errors", [])
    except (json.JSONDecodeError, TypeError):
        errors = ["unparseable proposal"]
    if errors:
        return False, f"metadata_errors={errors}"
    if score < MEASURE_MIN_SCORE:
        return False, f"score {score:.3f} < {MEASURE_MIN_SCORE}"
    return True, f"score={score:.3f}"


# ── writers ───────────────────────────────────────────────────────────────────

def write_col_mappings(proposals: list[dict], dry_run: bool) -> int:
    existing: set[tuple] = set()
    if COL_MAPPINGS_CSV.exists():
        with open(COL_MAPPINGS_CSV, newline="") as f:
            for row in csv.DictReader(f):
                existing.add((row["pair_id"], row["source_column"], row["target_column"]))

    new_rows = []
    for p in proposals:
        body = json.loads(p["proposal"])
        score, margin = parse_evidence_scores(p.get("evidence", "[]"))
        key  = (p["pair_id"], body["source_column"], body["target_column"])
        if key not in existing:
            new_rows.append({
                "pair_id":       p["pair_id"],
                "source_column": body["source_column"],
                "target_column": body["target_column"],
                "status":        "approved",
                "comments":      f"auto-approved score={score:.3f} margin={margin:.3f}",
            })

    if not new_rows:
        return 0

    if dry_run:
        print(f"    [DRY RUN] would append {len(new_rows)} row(s) to {COL_MAPPINGS_CSV.name}")
        for r in new_rows:
            print(f"      {r['pair_id']}: {r['source_column']} -> {r['target_column']}")
        return len(new_rows)

    with open(COL_MAPPINGS_CSV, "a", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["pair_id", "source_column", "target_column", "status", "comments"]
        )
        for row in new_rows:
            writer.writerow(row)

    print(f"    Wrote {len(new_rows)} row(s) to {COL_MAPPINGS_CSV.name}")
    return len(new_rows)


def _measure_to_yaml_block(body: dict, indent: int = 2) -> str:
    """Render a measure dict as a YAML list item without a dependency on PyYAML."""
    pad  = " " * indent
    pad2 = " " * (indent + 2)
    lines = [f"{pad}- measure_id: {body.get('measure_id', '')}"]
    scalar_keys = [
        "definition_type", "pair_id", "source_expression", "target_expression",
        "business_name", "description", "aggregation",
        "tolerance_absolute", "tolerance_percentage",
        "enabled", "severity", "approval_status", "publish_to_context",
    ]
    for k in scalar_keys:
        if k in body:
            v = body[k]
            if v is None:
                lines.append(f"{pad2}{k}: null")
            elif isinstance(v, bool):
                lines.append(f"{pad2}{k}: {'true' if v else 'false'}")
            elif isinstance(v, str):
                lines.append(f"{pad2}{k}: {v}")
            else:
                lines.append(f"{pad2}{k}: {v}")
    for list_key in ("source_filters", "target_filters"):
        val = body.get(list_key, [])
        if val:
            lines.append(f"{pad2}{list_key}:")
            for item in val:
                lines.append(f"{pad2}  - {item}")
        else:
            lines.append(f"{pad2}{list_key}: []")
    return "\n".join(lines)


def write_measures(proposals: list[dict], dry_run: bool) -> int:
    # Read existing measure_ids per pair to avoid duplicates
    existing: set[tuple] = set()
    content  = MEASURES_YAML.read_text(encoding="utf-8") if MEASURES_YAML.exists() else ""
    # Simple parse: look for "measure_id:" and "pair_id:" lines
    cur_mid = cur_pid = None
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("measure_id:"):
            cur_mid = s.split(":", 1)[1].strip()
        if s.startswith("pair_id:"):
            cur_pid = s.split(":", 1)[1].strip()
        if cur_mid and cur_pid:
            existing.add((cur_pid, cur_mid))
            cur_mid = cur_pid = None

    new_blocks = []
    for p in proposals:
        body = json.loads(p["proposal"])
        body["approval_status"] = "approved"
        body.pop("origin",    None)
        body.pop("evidence",  None)
        key = (body.get("pair_id"), body.get("measure_id"))
        if key not in existing:
            new_blocks.append(body)
            existing.add(key)

    if not new_blocks:
        return 0

    if dry_run:
        print(f"    [DRY RUN] would append {len(new_blocks)} measure(s) to {MEASURES_YAML.name}")
        for b in new_blocks:
            print(f"      {b.get('pair_id')}: {b.get('measure_id')}")
        return len(new_blocks)

    with open(MEASURES_YAML, "a", encoding="utf-8") as f:
        for b in new_blocks:
            f.write("\n" + _measure_to_yaml_block(b) + "\n")

    print(f"    Wrote {len(new_blocks)} measure(s) to {MEASURES_YAML.name}")
    return len(new_blocks)


# ── business_rule writer ──────────────────────────────────────────────────────

HUMAN_TESTS_YAML = ROOT / "inputs" / "human_tests.yaml"
BUSINESS_RULE_MIN_CONFIDENCE = 0.80


def should_approve_business_rule(p: dict) -> tuple[bool, str]:
    score = float(p.get("confidence", 0))
    if score < BUSINESS_RULE_MIN_CONFIDENCE:
        return False, f"score {score:.3f} < {BUSINESS_RULE_MIN_CONFIDENCE}"
    return True, f"score={score:.3f}"


def _business_rule_to_yaml_block(pair_id: str, body: dict, indent: int = 2) -> str:
    pad  = " " * indent
    pad2 = " " * (indent + 2)
    test_id  = body.get("test_id", "llm_rule")
    rule_type = body.get("type", "predicate")
    scope    = body.get("scope", "target")
    tgt_cols = body.get("target_columns", [])
    src_cols = body.get("source_columns", [])
    params   = body.get("parameters", {})
    severity = body.get("severity", "warning")
    desc     = body.get("description", "")

    lines = [f"{pad}- test_id: {pair_id}__{test_id}"]
    lines.append(f"{pad2}pair_id: {pair_id}")
    lines.append(f"{pad2}enabled: true")
    lines.append(f"{pad2}type: {rule_type}")
    lines.append(f"{pad2}scope: {scope}")
    lines.append(f"{pad2}severity: {severity}")
    if tgt_cols:
        lines.append(f"{pad2}target_columns: {json.dumps(tgt_cols)}")
    if src_cols:
        lines.append(f"{pad2}source_columns: {json.dumps(src_cols)}")
    if params:
        lines.append(f"{pad2}parameters:")
        for k, v in params.items():
            lines.append(f"{pad2}  {k}: {json.dumps(v)}")
    if desc:
        safe_desc = desc.replace('"', "'")
        lines.append(f'{pad2}description: "{safe_desc}"')
    lines.append(f"{pad2}publish_to_context: false")
    return "\n".join(lines)


def write_business_rules(proposals: list[dict], dry_run: bool) -> int:
    existing: set[str] = set()
    content = HUMAN_TESTS_YAML.read_text(encoding="utf-8") if HUMAN_TESTS_YAML.exists() else "tests:\n"
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("test_id:"):
            existing.add(s.split(":", 1)[1].strip())

    new_blocks = []
    for p in proposals:
        body    = json.loads(p["proposal"])
        pair_id = p["pair_id"]
        full_id = f"{pair_id}__{body.get('test_id', '')}"
        if full_id in existing:
            continue
        new_blocks.append((pair_id, body))
        existing.add(full_id)

    if not new_blocks:
        return 0

    if dry_run:
        print(f"    [DRY RUN] would append {len(new_blocks)} rule(s) to {HUMAN_TESTS_YAML.name}")
        for pair_id, body in new_blocks:
            print(f"      {pair_id}: {body.get('test_id')}  ({body.get('type')}) -- {body.get('description', '')[:60]}")
        return len(new_blocks)

    with open(HUMAN_TESTS_YAML, "a", encoding="utf-8") as f:
        for pair_id, body in new_blocks:
            f.write("\n" + _business_rule_to_yaml_block(pair_id, body) + "\n")

    print(f"    Wrote {len(new_blocks)} rule(s) to {HUMAN_TESTS_YAML.name}")
    return len(new_blocks)


def print_business_rule_review(proposals: list[dict]) -> None:
    for p in proposals:
        body  = json.loads(p["proposal"])
        score = p.get("confidence", 0)
        print(f"\n  [business_rule] {p['pair_id']}: {body.get('test_id')}  score={score:.2f}")
        print(f"    Type    : {body.get('type')}  scope={body.get('scope')}")
        print(f"    Columns : {body.get('target_columns')}")
        print(f"    Params  : {body.get('parameters')}")
        print(f"    Desc    : {body.get('description', '')[:80]}")
        print(f"    Rationale: {body.get('rationale', '')[:80]}")
        print(f"    Action  : Run with --approve-all or add manually to inputs/human_tests.yaml")


# ── manual-review printer ─────────────────────────────────────────────────────

def print_pk_review(proposals: list[dict]) -> None:
    for p in proposals:
        body = json.loads(p["proposal"])
        try:
            ev = json.loads(p.get("evidence", "{}"))
        except (json.JSONDecodeError, TypeError):
            ev = {}
        pair  = p["pair_id"]
        src   = body.get("source", [])
        tgt   = body.get("target", [])
        print(f"\n  [primary_key] {pair}")
        print(f"    Agent proposed : source={src}, target={tgt}  (confidence={p['confidence']:.3f})")
        if ev:
            print(f"    Cardinality    : {ev}")
        print(f"    Action needed  : Add/verify primary_key in business_context.yaml under '{pair}'")
        print(f"    Example:")
        print(f"      {pair}:")
        print(f"        primary_key:")
        print(f"          source: {json.dumps(src)}")
        print(f"          target: {json.dumps(tgt)}")
        print(f"    [!] Verify these columns form a UNIQUE key before accepting.")


def print_col_review(proposals: list[dict]) -> None:
    for p in proposals:
        body   = json.loads(p["proposal"])
        try:
            ev = json.loads(p.get("evidence", "[]"))
        except (json.JSONDecodeError, TypeError):
            ev = []
        score, margin = parse_evidence_scores(p.get("evidence", "[]"))
        print(f"\n  [column_mapping] {p['pair_id']}: {body.get('source_column')} -> ?")
        print(f"    Score={score:.3f}, margin={margin:.3f}  — too low/ambiguous to auto-approve")
        print(f"    Candidates:")
        for e in ev[:3]:
            marker = "← proposed" if e.get("target") == body.get("target_column") else ""
            print(f"      {e.get('target','?'):30s} {e.get('score',0):.4f}  {marker}")
        print(f"    Action: add a row to column_mappings.csv if correct, or correct the target.")


def print_measure_review(proposals: list[dict]) -> None:
    for p in proposals:
        body   = json.loads(p["proposal"])
        errors = body.get("metadata_errors", [])
        score  = p.get("confidence", 0)
        print(f"\n  [measure] {p['pair_id']}: {body.get('measure_id')}  score={score:.2f}")
        if errors:
            print(f"    Blocked by: {errors}")
            if "Missing source_expression" in errors:
                print(f"    Likely fix: add the column mapping for the source measure column first,")
                print(f"    then re-run - the source_expression will be auto-resolved.")
        print(f"    Source expression : {body.get('source_expression')}")
        print(f"    Target expression : {body.get('target_expression')}")


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("proposals_file", nargs="?", help="Path to approval_proposals.json")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without writing files")
    args = parser.parse_args()

    proposals_path = Path(args.proposals_file) if args.proposals_file else find_latest_proposals()
    print(f"\nReading: {proposals_path}")
    proposals = json.loads(proposals_path.read_text(encoding="utf-8"))
    pending   = [p for p in proposals if p.get("status") == "PENDING"]
    print(f"Found {len(pending)} PENDING proposal(s)\n")

    col_approve, col_review   = [], []
    meas_approve, meas_review = [], []
    biz_approve, biz_review   = [], []
    pk_review                 = []

    for p in pending:
        cat = p.get("category")
        if cat == "column_mapping":
            ok, _ = should_approve_col_map(p)
            (col_approve if ok else col_review).append(p)
        elif cat == "measure":
            ok, _ = should_approve_measure(p)
            (meas_approve if ok else meas_review).append(p)
        elif cat == "business_rule":
            ok, _ = should_approve_business_rule(p)
            (biz_approve if ok else biz_review).append(p)
        elif cat == "primary_key":
            pk_review.append(p)

    sep = "-" * 60

    print(sep)
    print(f"COLUMN MAPPINGS   auto={len(col_approve)}  review={len(col_review)}")
    if col_approve:
        for p in col_approve:
            _, reason = should_approve_col_map(p)
            body = json.loads(p["proposal"])
            print(f"  [OK] {p['pair_id']}: {body['source_column']} -> {body['target_column']}  ({reason})")
        write_col_mappings(col_approve, args.dry_run)

    print()
    print(f"MEASURES          auto={len(meas_approve)}  review={len(meas_review)}")
    if meas_approve:
        for p in meas_approve:
            _, reason = should_approve_measure(p)
            body = json.loads(p["proposal"])
            print(f"  [OK] {p['pair_id']}: {body.get('measure_id')}  ({reason})")
        write_measures(meas_approve, args.dry_run)

    print()
    print(f"BUSINESS RULES    auto={len(biz_approve)}  review={len(biz_review)}")
    if biz_approve:
        for p in biz_approve:
            _, reason = should_approve_business_rule(p)
            body = json.loads(p["proposal"])
            print(f"  [OK] {p['pair_id']}: {body.get('test_id')}  ({body.get('type')})  ({reason})")
        write_business_rules(biz_approve, args.dry_run)

    print()
    print(f"PRIMARY KEYS      auto=0  review={len(pk_review)}  (never auto-approved)")

    needs_review = pk_review or col_review or meas_review or biz_review
    if needs_review:
        print(f"\n{sep}")
        print("MANUAL REVIEW REQUIRED")
        if pk_review:
            print_pk_review(pk_review)
        if col_review:
            print_col_review(col_review)
        if meas_review:
            print_measure_review(meas_review)
        if biz_review:
            print_business_rule_review(biz_review)

    total_auto   = len(col_approve) + len(meas_approve) + len(biz_approve)
    total_manual = len(pk_review) + len(col_review) + len(meas_review) + len(biz_review)
    print(f"\n{sep}")
    print(f"Auto-approved: {total_auto}   Needs manual review: {total_manual}")

    return 1 if total_manual > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
